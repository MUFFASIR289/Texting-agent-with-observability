"""The pipeline `[FR-63]`. Deterministic control flow around five LLM calls.

The orchestrator, not the agent, owns everything that matters: it binds the
account, decides who is targetable, moves the state machine, and writes every
`agent_runs` row from the usage the stage handed back. That is what keeps the
agent package free of an app-DB connection `[SEC-09]`, and it means no model
output can advance a campaign.

M5 drives the pipeline as far as `SEGMENTED`. PLAN, GENERATE, validation, the
audience freeze and the approval hash arrive with M6 and M7.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog

from texting_agent.agent import instructions
from texting_agent.agent.llm import BudgetExceeded, LLMClient, StageFailed, StageResult
from texting_agent.agent.texting_agent import TextingAgent
from texting_agent.agent.tools import ScopedToolset
from texting_agent.database.repositories.campaign_repo import CampaignRepository
from texting_agent.database.repositories.customer_repo import CustomerRepository
from texting_agent.orchestrator.transitions import transition
from texting_agent.schemas.agent_io import ChurnAnalysis, SegmentationResult
from texting_agent.schemas.campaign import CampaignState as S
from texting_agent.services import scoring_config
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

    def run(self, account_id: str, goal: str, created_by: str) -> CampaignResult:
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
            self._persist_segments(campaign_id, result.segments)

            transition(self._campaigns, campaign_id, S.ANALYZING, S.SEGMENTED)
            result.state = S.SEGMENTED
        except BudgetExceeded as exc:
            return self._fail(result, "BUDGET_EXCEEDED", str(exc))
        except StageFailed as exc:
            return self._fail(result, "STAGE_FAILED",
                              f"{exc.stage}: {exc.last_error}")

        if not result.segments.segments:
            return self._fail(result, "NO_TARGETABLE_CUSTOMERS",
                              "every proposed segment matched zero customers")
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
                          assignment: SegmentAssignment) -> None:
        for assigned in assignment.segments:
            self._campaigns.add_segment(
                campaign_id, name=assigned.segment.name,
                priority=assigned.segment.priority,
                predicate=assigned.segment.predicate.model_dump(mode="json"),
                customer_count=assigned.size,
                rationale=assigned.segment.hypothesis,
            )

    def _fail(self, result: CampaignResult, code: str, detail: str) -> CampaignResult:
        self._campaigns.record_failure(result.campaign_id, code, detail)
        if transition_safely(self._campaigns, result.campaign_id, result.state):
            result.state = S.FAILED
        result.failure_code = code
        result.failure_detail = detail
        log.info("campaign.failed", campaign_id=result.campaign_id, code=code)
        return result


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
