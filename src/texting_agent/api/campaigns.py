"""Campaign and agent endpoints `[FR-63]`-`[FR-68]`.

Every route resolves scope from the key, never from the body: `account_id` in a
request is a *claim*, checked against the caller's scope before anything reads a
customer `[FR-66]`. A campaign belonging to another account is 404, not 403 with
details, because a distinguishable refusal confirms the id exists `[AZ-05]`.
"""

import sqlite3
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from texting_agent.agent.llm import LLMClient, TokenBudget
from texting_agent.agent.texting_agent import TextingAgent
from texting_agent.agent.tools import ScopedToolset
from texting_agent.config import settings
from texting_agent.database import agent_db, app_db
from texting_agent.database.repositories.campaign_repo import CampaignRepository
from texting_agent.database.repositories.customer_repo import CustomerRepository
from texting_agent.deps import (
    APIError,
    RequestContext,
    rate_limit,
    require_account,
    require_approver,
    require_operator,
)
from texting_agent.integrations.openai_client import OpenAILLMClient
from texting_agent.orchestrator.transitions import InvalidTransition, transition
from texting_agent.orchestrator.workflow import CampaignWorkflow
from texting_agent.schemas.campaign import CampaignState, Channel
from texting_agent.schemas.churn import ValueTier
from texting_agent.services import rendering_service, scoring_config
from texting_agent.services.rendering_service import RenderContext
from texting_agent.services.scoring_service import assess_account, days_since
from texting_agent.services.value_service import assign_tiers

log = structlog.get_logger()

router = APIRouter(tags=["campaigns"])

Operator = Annotated[RequestContext, Depends(require_operator)]
Approver = Annotated[RequestContext, Depends(require_approver)]


class CreateCampaign(BaseModel):
    account_id: str = Field(examples=["ACC_A"])
    goal: str = Field(min_length=1, max_length=500,
                      examples=["Win back customers who have stopped buying"])


class AgentQuery(BaseModel):
    account_id: str = Field(examples=["ACC_A"])
    query: str = Field(min_length=1, max_length=1000,
                       examples=["Which customers are most likely to churn?"])


def customer_repository() -> CustomerRepository:
    return CustomerRepository(agent_db.connect(settings.agent_db_path))


def campaign_repository() -> CampaignRepository:
    return CampaignRepository(app_db.connect(settings.app_db_path))


def llm_client(budget: TokenBudget) -> LLMClient:
    """The one place a model is constructed, so the whole HTTP surface can be
    exercised offline by replacing this.

    A missing key is a configuration problem, not a server fault: it names the
    variable to set rather than surfacing an SDK error as an opaque 500. The
    deterministic routes keep working without it `[EH-11]`.
    """
    if not settings.openai_api_key:
        raise APIError(503, "LLM_NOT_CONFIGURED",
                       "OPENAI_API_KEY is not set, so this endpoint cannot run.")
    return OpenAILLMClient(budget)


def _campaign_or_404(repo: CampaignRepository, context: RequestContext,
                     campaign_id: str) -> sqlite3.Row:
    """Search only the accounts this key holds, so an id from another tenant is
    indistinguishable from one that was never issued `[AZ-05]`, `[FR-68]`."""
    for account_id in context.accounts:
        row = repo.get(account_id, campaign_id)
        if row is not None:
            return row
    raise APIError(404, "NOT_FOUND", "No such campaign.")


@router.post("/campaigns", dependencies=[Depends(rate_limit)])
def create_campaign(body: CreateCampaign, context: Operator) -> dict:
    """Score, analyse, segment. One account, bound here `[FR-63]`, `[FR-63b]`."""
    account_id = require_account(context, body.account_id)
    budget = TokenBudget(settings.token_budget_per_campaign)
    workflow = CampaignWorkflow(customer_repository(), campaign_repository(),
                                llm_client(budget))
    result = workflow.run(account_id, body.goal, created_by=context.key_id)
    log.info("campaign.created", campaign_id=result.campaign_id,
             account_id=account_id, state=result.state.value,
             tokens=result.tokens_used)
    return _serialise(result)


@router.get("/campaigns")
def list_campaigns(context: Operator, account_id: str | None = None) -> dict:
    """All in-scope accounts, or one via `?account_id=` `[FR-64]`."""
    repo = campaign_repository()
    accounts = ((require_account(context, account_id),) if account_id
                else context.accounts)
    campaigns = [dict(row) for account in accounts
                 for row in repo.list_for_account(account)]
    return {"campaigns": campaigns, "count": len(campaigns)}


@router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: str, context: Operator) -> dict:
    repo = campaign_repository()
    row = _campaign_or_404(repo, context, campaign_id)
    return {"campaign": dict(row),
            "segments": [dict(s) for s in repo.list_segments(campaign_id)],
            "agent_runs": [dict(r) for r in repo.list_runs(campaign_id)]}


@router.get("/campaigns/{campaign_id}/segments")
def get_segments(campaign_id: str, context: Operator) -> dict:
    repo = campaign_repository()
    _campaign_or_404(repo, context, campaign_id)
    return {"segments": [dict(s) for s in repo.list_segments(campaign_id)]}


@router.get("/campaigns/{campaign_id}/customers")
def get_customers(campaign_id: str, context: Operator) -> dict:
    """Ids and behaviour only - never a name, email or phone `[RV-C8]`."""
    repo = campaign_repository()
    _campaign_or_404(repo, context, campaign_id)
    targets = repo.list_targets(campaign_id)
    return {"customers": [{"customer_id": t["customer_id"],
                           "segment_id": t["segment_id"],
                           "was_lapsed": bool(t["was_lapsed"])} for t in targets],
            "count": len(targets)}


@router.get("/campaigns/{campaign_id}/messages")
def get_messages(campaign_id: str, context: Operator) -> dict:
    """Variants with a preview rendered against a real in-scope customer
    `[FR-34]`.

    A preview against an invented customer would prove nothing. The point is to
    show the operator what an actual recipient receives - including the cases
    where rendering refuses and that recipient is skipped, which is exactly what
    they need to see before approving.
    """
    repo = campaign_repository()
    campaign = _campaign_or_404(repo, context, campaign_id)
    preview = _preview_context(repo, campaign_id, campaign["account_id"])

    rendered = []
    for row in repo.list_variants(campaign_id):
        variant = _VariantRow(row)
        entry = {
            "variant_id": row["variant_id"],
            "segment_name": row["segment_name"],
            "channel": row["channel"],
            "label": row["label"],
            "subject_template": row["subject_template"],
            "body_template": row["body_template"],
            "preview": None,
            "preview_unavailable": None,
        }
        if preview is None:
            entry["preview_unavailable"] = "no targeted customer to render against"
        else:
            try:
                message = rendering_service.render_variant(variant, preview)
            except rendering_service.SkipCustomer as skip:
                entry["preview_unavailable"] = skip.reason
            else:
                entry["preview"] = {
                    "subject": message.subject, "body": message.body,
                    "cta_text": message.cta_text, "cta_url": message.cta_url,
                }
        rendered.append(entry)
    return {"variants": rendered, "count": len(rendered)}


class Decision(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class Rejection(BaseModel):
    reason: str = Field(min_length=1, max_length=500,
                        examples=["The 25% discount is too generous for this segment"])


@router.post("/campaigns/{campaign_id}/approve")
def approve_campaign(campaign_id: str, body: Decision,
                     context: Approver) -> dict:
    """Approve a specific campaign: this copy, this offer, to these people.

    The hash recorded here is what send time re-verifies. Approving is guarded by
    a conditional UPDATE, so two approvers pressing the button together produce
    exactly one approval `[FR-43]`, `[FR-44]`, `[EC-12]`.
    """
    repo = campaign_repository()
    campaign = _campaign_or_404(repo, context, campaign_id)
    stored_hash = campaign["content_hash"] or ""

    _decide(repo, campaign_id, CampaignState.APPROVED)
    repo.record_decision(campaign_id, decision="APPROVED",
                         approver_id=context.key_id, content_hash=stored_hash,
                         reason=body.note)
    log.info("campaign.approved", campaign_id=campaign_id,
             approver=context.key_id)
    return {"campaign_id": campaign_id, "state": CampaignState.APPROVED.value,
            "approved_by": context.key_id, "content_hash": stored_hash}


@router.post("/campaigns/{campaign_id}/reject")
def reject_campaign(campaign_id: str, body: Rejection, context: Approver) -> dict:
    """Reject with a reason `[FR-47]`. The reason is not decoration: `revise`
    hands it to the agent as operator feedback."""
    repo = campaign_repository()
    campaign = _campaign_or_404(repo, context, campaign_id)
    _decide(repo, campaign_id, CampaignState.REJECTED)
    repo.record_decision(campaign_id, decision="REJECTED",
                         approver_id=context.key_id,
                         content_hash=campaign["content_hash"] or "",
                         reason=body.reason)
    return {"campaign_id": campaign_id, "state": CampaignState.REJECTED.value,
            "reason": body.reason}


@router.post("/campaigns/{campaign_id}/cancel")
def cancel_campaign(campaign_id: str, context: Operator) -> dict:
    """Any non-terminal campaign, by an operator `[FR-48]`. Cancelling is not a
    decision about content, so it needs no approver."""
    repo = campaign_repository()
    campaign = _campaign_or_404(repo, context, campaign_id)
    current = CampaignState(campaign["state"])
    try:
        transition(repo, campaign_id, current, CampaignState.CANCELLED)
    except InvalidTransition as exc:
        raise _conflict(exc) from exc
    return {"campaign_id": campaign_id, "state": CampaignState.CANCELLED.value}


@router.post("/campaigns/{campaign_id}/revise", dependencies=[Depends(rate_limit)])
def revise_campaign(campaign_id: str, context: Operator) -> dict:
    """Clone a REJECTED or FAILED campaign into a fresh run `[FR-48a]`.

    The original stays terminal. Re-running the same campaign id would erase the
    record of what was rejected and why, which is the only thing that makes a
    rejection useful.
    """
    repo = campaign_repository()
    campaign = _campaign_or_404(repo, context, campaign_id)
    current = CampaignState(campaign["state"])
    if current not in (CampaignState.REJECTED, CampaignState.FAILED):
        raise APIError(409, "INVALID_STATE",
                       "Only a rejected or failed campaign can be revised.",
                       [{"current": current.value,
                         "allowed": ["REJECTED", "FAILED"]}])

    feedback = _revision_feedback(repo, campaign, current)
    budget = TokenBudget(settings.token_budget_per_campaign)
    workflow = CampaignWorkflow(customer_repository(), repo, llm_client(budget))
    goal = f"{campaign['goal']}\n\nPrevious attempt feedback: {feedback}"
    result = workflow.run(campaign["account_id"], goal,
                          created_by=context.key_id, revised_from=campaign_id)
    return _serialise(result)


def _decide(repo: CampaignRepository, campaign_id: str,
            decision: CampaignState) -> None:
    """Approval and rejection are only legal from AWAITING_APPROVAL, and the
    conditional UPDATE is what makes "exactly one wins" true rather than
    likely `[FR-44]`, `[EC-11]`, `[EC-12]`."""
    if not repo.try_transition(campaign_id, CampaignState.AWAITING_APPROVAL,
                               decision):
        current = repo.current_state(campaign_id) or "missing"
        raise APIError(409, "INVALID_STATE",
                       f"This campaign is {current}, not awaiting approval.",
                       [{"current": current, "requested": decision.value}])


def _conflict(exc: InvalidTransition) -> APIError:
    return APIError(409, "INVALID_STATE",
                    f"A campaign in {exc.current} cannot move to "
                    f"{exc.requested.value}.",
                    [{"current": str(exc.current),
                      "requested": exc.requested.value}])


def _revision_feedback(repo: CampaignRepository, campaign,
                       current: CampaignState) -> str:
    if current is CampaignState.REJECTED:
        decisions = [d for d in repo.list_decisions(campaign["campaign_id"])
                     if d["decision"] == "REJECTED"]
        if decisions:
            return decisions[-1]["reason"] or "rejected without a stated reason"
    return campaign["failure_detail"] or "the previous attempt failed"


@router.post("/agent/query", dependencies=[Depends(rate_limit)])
def agent_query(body: AgentQuery, context: Operator) -> dict:
    """A grounded answer plus the tools it rests on `[FR-65]`."""
    account_id = require_account(context, body.account_id)
    budget = TokenBudget(settings.token_budget_per_campaign)
    toolset = ScopedToolset(account_id, customer_repository())
    agent = TextingAgent(llm_client(budget), toolset,
                         max_tool_iterations=settings.agent_max_tool_iterations)
    result = agent.query(body.query)
    return {
        "answer": result.answer,
        "grounded_in": result.grounded_in,
        "tools_called": result.tools_called,
        "truncated": result.truncated,
        "tokens_used": result.usage.total_tokens,
    }


class _VariantRow:
    """Adapts a stored row to what the renderer expects, so the renderer does
    not have to know about database rows."""

    def __init__(self, row) -> None:
        self.channel = Channel(row["channel"])
        self.label = row["label"]
        self.subject_template = row["subject_template"]
        self.body_template = row["body_template"]
        self.cta_text = row["cta_text"]
        self.cta_url_key = row["cta_url_key"]


def _preview_context(repo: CampaignRepository, campaign_id: str,
                     account_id: str) -> RenderContext | None:
    """Build a render context from a real targeted customer.

    Before M7 freezes the audience there are no targets, so previews fall back
    to the highest-risk customer in the account - still a real one, never an
    invented one.
    """
    targets = repo.list_targets(campaign_id)
    customers = CustomerRepository(agent_db.connect(settings.agent_db_path))
    if targets:
        record = customers.get(account_id, targets[0]["customer_id"])
    else:
        config = scoring_config.get()
        assessment = assess_account(account_id,
                                    customers.list_for_account(account_id), config)
        targetable = [entry for entry in assessment.assessed if entry.targetable]
        record = (customers.get(account_id, targetable[0].customer_id)
                  if targetable else None)
    if record is None:
        return None

    tiers, _ = assign_tiers(customers.list_for_account(account_id),
                            scoring_config.get())
    return RenderContext(
        customer=record,
        value_tier=tiers.get(record.customer_id, ValueTier.STANDARD),
        days_since_purchase=_whole_days(days_since(record.last_purchase_at,
                                                   datetime.now(UTC))),
        offer={"value": 10, "code": "PREVIEW"},
        brand_name=account_id,
        unsubscribe_url=f"https://example.test/u/{record.customer_id}",
    )


def _whole_days(value: float | None) -> int | None:
    return None if value is None else int(value)


def _serialise(result) -> dict:
    return {
        "campaign_id": result.campaign_id,
        "account_id": result.account_id,
        "state": result.state.value,
        "targetable_customers": result.targetable_count,
        "excluded": {"unknown_risk": result.excluded_unknown,
                     "stale_data": result.excluded_stale},
        "tiering_suppressed": result.tiering_suppressed,
        "analysis": result.analysis.model_dump(mode="json") if result.analysis else None,
        "segments": [
            {"name": s.segment.name, "priority": s.segment.priority,
             "hypothesis": s.segment.hypothesis, "size": s.size,
             "predicate": s.segment.predicate.model_dump(mode="json")}
            for s in (result.segments.segments if result.segments else [])
        ],
        "dropped_segments": [
            {"name": name, "reason": reason}
            for name, reason in (result.segments.dropped if result.segments else [])
        ],
        "plans": [
            {"segment_name": p.segment_name, "playbook_id": p.playbook_id.value,
             "offer": p.offer.model_dump(mode="json"),
             "channels": [c.value for c in p.channels],
             "channel_rationale": p.channel_rationale}
            for p in result.plans
        ],
        "variant_count": result.variant_count,
        "violations": result.violations,
        "frozen_audience": result.frozen_audience,
        "content_hash": result.content_hash,
        "tokens_used": result.tokens_used,
        "failure": ({"code": result.failure_code, "detail": result.failure_detail}
                    if result.failure_code else None),
        "notes": result.notes,
    }
