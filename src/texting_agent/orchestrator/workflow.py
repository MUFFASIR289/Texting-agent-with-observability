"""The pipeline `[FR-63]`. Deterministic control flow around five LLM calls.

The orchestrator, not the agent, owns everything that matters: it binds the
account, decides who is targetable, moves the state machine, and writes every
`agent_runs` row from the usage the stage handed back. That is what keeps the
agent package free of an app-DB connection `[SEC-09]`, and it means no model
output can advance a campaign.

The pipeline runs to `AWAITING_APPROVAL` and stops there. Sending is a separate
call, made by a person, after a person has read the content `[FR-41]`.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog

from texting_agent.agent import instructions
from texting_agent.agent.llm import BudgetExceeded, LLMClient, StageFailed, StageResult
from texting_agent.agent.texting_agent import TextingAgent
from texting_agent.agent.tools import ScopedToolset
from texting_agent.database.repositories.campaign_repo import CampaignRepository
from texting_agent.config import settings
from texting_agent.database.repositories.customer_repo import CustomerRepository
from texting_agent.orchestrator.approval import content_hash
from texting_agent.orchestrator.transitions import transition
from texting_agent.schemas.agent_io import ChurnAnalysis, RetentionPlan
from texting_agent.schemas.campaign import CampaignState as S
from texting_agent.schemas.churn import RiskLevel, ValueTier
from texting_agent.services import (
    playbook_service,
    policy_service,
    rendering_service,
    scoring_config,
)
from texting_agent.services.rendering_service import TemplateError
from texting_agent.services.scoring_service import assess_account
from texting_agent.services.segmentation_service import SegmentAssignment, assign

log = structlog.get_logger()


class NoTargetableCustomers(Exception):
    """EC-01, EC-02: nothing to campaign to. Not a failure of the run, and
    emphatically not something to ask a model about."""

    def __init__(self, reason: str, matched: int = 0) -> None:
        super().__init__(reason)
        self.reason = reason
        self.matched = matched


@dataclass
class CampaignResult:
    campaign_id: str
    account_id: str
    state: S
    analysis: ChurnAnalysis | None = None
    segments: SegmentAssignment | None = None
    plans: list[RetentionPlan] = field(default_factory=list)
    variant_count: int = 0
    violations: list[dict] = field(default_factory=list)
    frozen_audience: int = 0
    content_hash: str | None = None
    targetable_count: int = 0
    excluded_unknown: int = 0
    excluded_stale: int = 0
    tiering_suppressed: bool = False
    tokens_used: int = 0
    failure_code: str | None = None
    failure_detail: str | None = None
    notes: list[str] = field(default_factory=list)


class CampaignWorkflow:
    def __init__(self, customer_repo: CustomerRepository,
                 campaign_repo: CampaignRepository, client: LLMClient) -> None:
        self._customers = customer_repo
        self._campaigns = campaign_repo
        self._client = client

    def run(self, account_id: str, goal: str, created_by: str,
            revised_from: str | None = None) -> CampaignResult:
        config = scoring_config.get()
        now = datetime.now(UTC)

        # Score first. If there is nobody to talk to, we have not spent a token
        # and there is nothing for a model to be creative about `[EC-01]`.
        records = self._customers.list_for_account(account_id)
        assessment = assess_account(account_id, records, config, now)
        targetable = [entry for entry in assessment.assessed if entry.targetable]

        campaign_id = self._campaigns.create_campaign(
            account_id=account_id, goal=goal, created_by=created_by,
            prompt_version=instructions.VERSION,
            config_version=str(config.version),
            excluded_stale_count=assessment.stale_count,
            excluded_unknown_count=assessment.unknown_count,
            revised_from=revised_from,
        )
        result = CampaignResult(
            campaign_id=campaign_id, account_id=account_id, state=S.RECEIVED,
            targetable_count=len(targetable),
            excluded_unknown=assessment.unknown_count,
            excluded_stale=assessment.stale_count,
            tiering_suppressed=assessment.tiering_suppressed,
        )
        if assessment.tiering_suppressed:
            result.notes.append(
                f"Value tiering was suppressed: only {assessment.purchaser_count} "
                "customers have purchased, too few to rank into percentiles."
            )

        if not targetable:
            return self._fail(result, "NO_TARGETABLE_CUSTOMERS", _why_empty(assessment))

        toolset = ScopedToolset(account_id, self._customers, config, now)
        agent = TextingAgent(self._client, toolset)

        try:
            transition(self._campaigns, campaign_id, S.RECEIVED, S.ANALYZING)
            result.state = S.ANALYZING

            analysis = self._stage(campaign_id, account_id,
                                   lambda: agent.analyze(), result)
            result.analysis = analysis.output

            segmentation = self._stage(
                campaign_id, account_id,
                lambda: agent.segment(analysis.output, goal), result)
            result.segments = assign(segmentation.output.segments, assessment.assessed)
            segment_ids = self._persist_segments(campaign_id, result.segments)

            transition(self._campaigns, campaign_id, S.ANALYZING, S.SEGMENTED)
            result.state = S.SEGMENTED

            if not result.segments.segments:
                return self._fail(result, "NO_TARGETABLE_CUSTOMERS",
                                  "every proposed segment matched zero customers")

            self._plan_and_generate(campaign_id, account_id, agent, assessment,
                                    result, segment_ids)
            transition(self._campaigns, campaign_id, S.SEGMENTED, S.PLANNED)
            transition(self._campaigns, campaign_id, S.PLANNED, S.CONTENT_READY)
            result.state = S.CONTENT_READY

            if result.violations:
                # FR-38: failed with the full list of rule ids, never corrected.
                # Fixing one violation at a time means running the campaign five
                # times, so every violation found is reported.
                return self._fail(
                    result, "POLICY_VIOLATION",
                    "; ".join(v["rule_id"] for v in result.violations))

            transition(self._campaigns, campaign_id, S.CONTENT_READY, S.VALIDATED)
            result.state = S.VALIDATED

            self._freeze_and_hash(campaign_id, account_id, assessment, result,
                                  segment_ids)
            transition(self._campaigns, campaign_id, S.VALIDATED, S.AWAITING_APPROVAL)
            result.state = S.AWAITING_APPROVAL
        except BudgetExceeded as exc:
            return self._fail(result, "BUDGET_EXCEEDED", str(exc))
        except StageFailed as exc:
            return self._fail(result, "STAGE_FAILED",
                              f"{exc.stage}: {exc.last_error}")
        except TemplateError as exc:
            # VR-08. A placeholder the allowlist does not carry is wrong for
            # every customer in the segment, so the campaign fails rather than
            # the template being quietly patched `[FR-38]`.
            return self._fail(result, "INVALID_TEMPLATE", str(exc))

        return result

    # --- internals --------------------------------------------------------

    def _stage[T](self, campaign_id: str, account_id: str,
                  call, result: CampaignResult) -> StageResult[T]:
        """Run one stage and record what it cost.

        The usage arrives in the return value; the agent never wrote it. If the
        stage raises, the run is still recorded, because a campaign that cost
        tokens and produced nothing is exactly the one worth seeing.
        """
        started = datetime.now(UTC)
        try:
            stage_result = call()
        except (StageFailed, BudgetExceeded) as exc:
            stage = getattr(exc, "stage", "unknown")
            self._campaigns.record_run(
                campaign_id, account_id, stage=stage, model_id="",
                tokens_in=0, tokens_out=0, status="FAILED", error=type(exc).__name__,
            )
            raise
        elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        self._campaigns.record_run(
            campaign_id, account_id, stage=stage_result.stage,
            model_id=stage_result.model, tokens_in=stage_result.usage.input_tokens,
            tokens_out=stage_result.usage.output_tokens, status="OK",
            latency_ms=elapsed_ms,
        )
        self._campaigns.add_usage(campaign_id, stage_result.usage.input_tokens,
                                  stage_result.usage.output_tokens,
                                  stage_result.model)
        result.tokens_used += stage_result.usage.total_tokens
        return stage_result

    def _persist_segments(self, campaign_id: str,
                          assignment: SegmentAssignment) -> dict[str, str]:
        return {
            assigned.segment.name: self._campaigns.add_segment(
                campaign_id, name=assigned.segment.name,
                priority=assigned.segment.priority,
                predicate=assigned.segment.predicate.model_dump(mode="json"),
                customer_count=assigned.size,
                rationale=assigned.segment.hypothesis,
            )
            for assigned in assignment.segments
        }

    def _plan_and_generate(self, campaign_id: str, account_id: str,
                           agent: TextingAgent, assessment, result: CampaignResult,
                           segment_ids: dict[str, str]) -> None:
        """One PLAN and one GENERATE call per surviving segment `[FR-23]`,
        `[FR-25]`.

        Per segment rather than one call for all of them: a segment's copy has
        to be justified by that segment's own engagement rates, and a single
        call would let one segment's numbers bleed into another's rationale.
        """
        playbooks = playbook_service.get()
        policy = policy_service.get()
        render_config = rendering_service.get()
        placeholder_menu = {
            name: f"resolves to the customer's {spec.source}"
            for name, spec in render_config.placeholders.items()
        }
        tiers = {entry.customer_id: entry.value_tier for entry in assessment.assessed}

        for assigned in result.segments.segments:
            segment = assigned.segment
            dominant_tier = _dominant_tier(assigned.customer_ids, tiers)

            plan = self._stage(
                campaign_id, account_id,
                lambda s=segment, a=assigned, t=dominant_tier: agent.plan(
                    s.name, s.hypothesis, a.size, s.predicate, t, playbooks),
                result,
            ).output
            self._campaigns.set_plan(
                segment_ids[segment.name], playbook_id=plan.playbook_id.value,
                offer=plan.offer.model_dump(mode="json"),
                channels=[channel.value for channel in plan.channels],
                rationale=plan.channel_rationale,
            )
            result.plans.append(plan)

            variants = self._stage(
                campaign_id, account_id,
                lambda p=plan, s=segment: agent.generate(
                    p, s.hypothesis, s.predicate, placeholder_menu,
                    settings.sms_max_characters),
                result,
            ).output

            # Validated before storage `[VR-05]`-`[VR-08]`. A campaign that
            # cannot legally be sent is not content worth keeping, and storing
            # it would only move the failure to send time.
            result.violations += [
                v.as_dict() for v in
                policy_service.check_plan(plan, dominant_tier, playbooks, policy)
            ]
            result.violations += [
                v.as_dict() for v in policy_service.check_variants(
                    variants.variants, plan.channels, plan.variants_per_channel,
                    policy, render_config, assigned.customer_ids)
            ]

            for variant in variants.variants:
                rendering_service.validate_template(variant.body_template,
                                                    render_config)
                if variant.subject_template:
                    rendering_service.validate_template(variant.subject_template,
                                                        render_config)
                self._campaigns.add_variant(
                    segment_ids[segment.name], channel=variant.channel.value,
                    label=variant.label, body_template=variant.body_template,
                    subject_template=variant.subject_template,
                    cta_text=variant.cta_text, cta_url_key=variant.cta_url_key,
                )
                result.variant_count += 1

    def _freeze_and_hash(self, campaign_id: str, account_id: str,
                         assessment, result: CampaignResult,
                         segment_ids: dict[str, str]) -> None:
        """Freeze the audience, then hash it with the content `[FR-42]`,
        `[FR-42a]`.

        Order matters: hashing before the freeze would leave the audience free
        to change under an approval that had already been given.
        """
        lapsed = {
            entry.customer_id: entry.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
            for entry in assessment.assessed
        }
        for assigned in result.segments.segments:
            self._campaigns.freeze_targets(
                campaign_id, account_id, segment_ids[assigned.segment.name],
                [(customer_id, lapsed.get(customer_id, False))
                 for customer_id in assigned.customer_ids],
            )
        result.frozen_audience = self._campaigns.count_targets(campaign_id)
        result.content_hash = content_hash(self._campaigns, campaign_id)
        self._campaigns.set_content_hash(campaign_id, result.content_hash)


    def _fail(self, result: CampaignResult, code: str, detail: str) -> CampaignResult:
        self._campaigns.record_failure(result.campaign_id, code, detail)
        if transition_safely(self._campaigns, result.campaign_id, result.state):
            result.state = S.FAILED
        result.failure_code = code
        result.failure_detail = detail
        log.info("campaign.failed", campaign_id=result.campaign_id, code=code)
        return result


def _dominant_tier(customer_ids: list[str],
                   tiers: dict[str, ValueTier]) -> ValueTier:
    """The tier most of the segment sits in, which decides which playbooks are
    on the menu. Ties break towards the more valuable tier, because offering a
    VIP a price-sensitive playbook is the more expensive mistake."""
    order = list(ValueTier)
    counts = Counter(tiers[customer_id] for customer_id in customer_ids
                     if customer_id in tiers)
    if not counts:
        return ValueTier.STANDARD
    return min(counts, key=lambda tier: (-counts[tier], order.index(tier)))


def transition_safely(repo: CampaignRepository, campaign_id: str,
                      current: S) -> bool:
    """Move to FAILED without letting the failure path raise its own error."""
    return repo.try_transition(campaign_id, current, S.FAILED)


def _why_empty(assessment) -> str:
    """EC-01 and EC-02 want different sentences: 'no customers' and 'no customers
    that matched' are different problems for the operator."""
    if not assessment.assessed:
        return "this account has no customers"
    return (
        f"none of {len(assessment.assessed)} customers are targetable: "
        f"{assessment.unknown_count} have too little data to score and "
        f"{assessment.stale_count} have stale data"
    )
