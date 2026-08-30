"""Campaign and agent endpoints `[FR-63]`-`[FR-68]`.

Every route resolves scope from the key, never from the body: `account_id` in a
request is a *claim*, checked against the caller's scope before anything reads a
customer `[FR-66]`. A campaign belonging to another account is 404, not 403 with
details, because a distinguishable refusal confirms the id exists `[AZ-05]`.
"""

import sqlite3
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
    require_operator,
)
from texting_agent.integrations.openai_client import OpenAILLMClient
from texting_agent.orchestrator.workflow import CampaignWorkflow

log = structlog.get_logger()

router = APIRouter(tags=["campaigns"])

Operator = Annotated[RequestContext, Depends(require_operator)]


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
        "tokens_used": result.tokens_used,
        "failure": ({"code": result.failure_code, "detail": result.failure_detail}
                    if result.failure_code else None),
        "notes": result.notes,
    }
