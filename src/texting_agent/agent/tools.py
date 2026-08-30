"""`ScopedToolset` - the entire model-callable surface `[FR-12]`, `[FR-13]`.

Three properties hold by construction rather than by instruction:

* **The account is bound here, not passed.** `account_id` is a constructor
  argument. No tool signature contains it, so the model has no vocabulary for
  naming an account and cannot request one it was not given `[SEC-05]`, `[AZ-*]`.
* **No tool takes SQL, a table, a column, or free text.** Every parameter is an
  enum, a bounded integer, or a structured predicate over values the scoring
  service already computed `[SEC-03]`.
* **Nothing that leaves here carries PII.** Tools return `CustomerFacts`, which
  has no name, email or phone field to populate `[FR-14]`, `[SEC-06]`.

Scoring happens once per toolset and is reused across calls: a tool loop asking
four questions about the same account should not re-rank it four times.
"""

import statistics
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from texting_agent.database.repositories.customer_repo import CustomerRepository
from texting_agent.schemas.campaign import SegmentPredicate
from texting_agent.schemas.churn import (
    AccountAssessment,
    ChurnAssessment,
    ReasonCode,
    RiskLevel,
    ValueTier,
)
from texting_agent.schemas.customer import CustomerFacts, CustomerRecord
from texting_agent.services import scoring_config
from texting_agent.services.scoring_config import ScoringConfig
from texting_agent.services.scoring_service import assess_account, days_since

DEFAULT_CANDIDATE_LIMIT = 20
MAX_CANDIDATE_LIMIT = 50          # hard ceiling, whatever the model asks for [FR-15]


class ToolError(Exception):
    """A structured failure the model can act on `[FR-17]`.

    Carries a code and a plain sentence - never a stack trace, a SQL string or a
    filesystem path, all of which describe our internals to something we do not
    trust.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def as_payload(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}


# --- tool results ----------------------------------------------------------


class ChurnSummary(BaseModel):
    total_customers: int
    targetable_customers: int
    counts_by_risk_level: dict[RiskLevel, int]
    counts_by_value_tier: dict[ValueTier, int]
    reason_code_frequency: dict[ReasonCode, int]
    median_days_since_purchase: float | None
    unknown_count: int
    stale_count: int
    tiering_suppressed: bool
    note: str


class CandidateList(BaseModel):
    matching_count: int
    returned_count: int
    limit_applied: int
    candidates: list[CustomerFacts]


class SegmentStatistics(BaseModel):
    size: int
    share_of_targetable: float
    mean_churn_score: float | None
    mean_email_open_rate: float | None
    mean_sms_response_rate: float | None
    counts_by_value_tier: dict[ValueTier, int]
    top_reason_codes: dict[ReasonCode, int]


# --- model-visible parameter shapes ---------------------------------------


class CandidateFilters(BaseModel):
    risk_level: RiskLevel | None = None
    value_tier: ValueTier | None = None
    reason_code: ReasonCode | None = None
    limit: int = Field(default=DEFAULT_CANDIDATE_LIMIT, ge=1, le=MAX_CANDIDATE_LIMIT)


class CustomerLookup(BaseModel):
    customer_id: str = Field(min_length=1, max_length=64)


class SegmentQuery(BaseModel):
    predicate: SegmentPredicate


HEURISTIC_NOTE = (
    "churn_score is a weighted heuristic used to rank customers. It is not "
    "calibrated: 0.87 does not mean an 87% chance of churning."
)


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 4) if values else None


class ScopedToolset:
    def __init__(
        self,
        account_id: str,
        repo: CustomerRepository,
        config: ScoringConfig | None = None,
        now: datetime | None = None,
    ) -> None:
        if not account_id or not isinstance(account_id, str):
            raise ValueError("account_id is required and must be a non-empty string")
        self._account_id = account_id          # bound here; never a tool parameter
        self._repo = repo
        self._config = config or scoring_config.get()
        self._now = now or datetime.now(UTC)
        self._records: dict[str, CustomerRecord] | None = None
        self._assessment: AccountAssessment | None = None

    # --- internals the model cannot reach ---------------------------------

    def _assessed(self) -> AccountAssessment:
        if self._assessment is None:
            records = self._repo.list_for_account(self._account_id)
            self._records = {r.customer_id: r for r in records}
            self._assessment = assess_account(
                self._account_id, records, self._config, self._now
            )
        return self._assessment

    def _facts(self, assessment: ChurnAssessment) -> CustomerFacts:
        record = self._records[assessment.customer_id]

        def whole_days(value: float | None) -> int | None:
            return None if value is None else int(value)

        return CustomerFacts(
            customer_id=record.customer_id,
            risk_level=assessment.risk_level,
            churn_score=assessment.churn_score,
            value_tier=assessment.value_tier,
            days_since_activity=whole_days(days_since(record.last_activity_at, self._now)),
            days_since_purchase=whole_days(days_since(record.last_purchase_at, self._now)),
            days_since_login=whole_days(days_since(record.last_login_at, self._now)),
            total_orders=record.total_orders,
            total_spend=record.total_spend,
            email_open_rate=record.email_open_rate,
            sms_response_rate=record.sms_response_rate,
            email_open_rate_prev_90d=record.email_open_rate_prev_90d,
            sms_response_rate_prev_90d=record.sms_response_rate_prev_90d,
            orders_last_90d=record.orders_last_90d,
            orders_prev_90d=record.orders_prev_90d,
            reasons=assessment.reasons,
            stale=assessment.stale,
        )

    def _targetable(self) -> list[ChurnAssessment]:
        """UNKNOWN and stale customers are reported in the summary but never
        offered as candidates `[FR-04c]`, `[FR-10a]`."""
        return [a for a in self._assessed().assessed if a.targetable]

    # --- the tools --------------------------------------------------------

    def get_churn_summary(self) -> ChurnSummary:
        assessment = self._assessed()
        assert self._records is not None

        reason_frequency: dict[ReasonCode, int] = {}
        for entry in assessment.assessed:
            for reason in entry.reasons:
                reason_frequency[reason.code] = reason_frequency.get(reason.code, 0) + 1

        gaps = [
            gap for entry in assessment.assessed
            if (gap := days_since(self._records[entry.customer_id].last_purchase_at,
                                  self._now)) is not None
        ]
        return ChurnSummary(
            total_customers=len(assessment.assessed),
            targetable_customers=len(self._targetable()),
            counts_by_risk_level={
                level: sum(1 for a in assessment.assessed if a.risk_level is level)
                for level in RiskLevel
            },
            counts_by_value_tier={
                tier: sum(1 for a in assessment.assessed if a.value_tier is tier)
                for tier in ValueTier
            },
            reason_code_frequency=dict(
                sorted(reason_frequency.items(), key=lambda kv: kv[1], reverse=True)
            ),
            median_days_since_purchase=round(statistics.median(gaps), 1) if gaps else None,
            unknown_count=assessment.unknown_count,
            stale_count=assessment.stale_count,
            tiering_suppressed=assessment.tiering_suppressed,
            note=HEURISTIC_NOTE,
        )

    def get_churn_candidates(self, **params: Any) -> CandidateList:
        filters = CandidateFilters.model_validate(params)
        matching = [
            entry for entry in self._targetable()
            if (filters.risk_level is None or entry.risk_level is filters.risk_level)
            and (filters.value_tier is None or entry.value_tier is filters.value_tier)
            and (filters.reason_code is None
                 or filters.reason_code in {r.code for r in entry.reasons})
        ]
        # The full count travels with the truncated sample, so the model can see
        # that it is looking at 20 of 900 rather than at everyone.
        sample = matching[: filters.limit]
        return CandidateList(
            matching_count=len(matching),
            returned_count=len(sample),
            limit_applied=filters.limit,
            candidates=[self._facts(entry) for entry in sample],
        )

    def get_customer_behavior(self, **params: Any) -> CustomerFacts:
        lookup = CustomerLookup.model_validate(params)
        by_id = {a.customer_id: a for a in self._assessed().assessed}
        entry = by_id.get(lookup.customer_id)
        if entry is None:
            # Identical response whether the id belongs to another account or to
            # nobody: a distinguishable "wrong account" reply is an enumeration
            # oracle `[AZ-*]`.
            raise ToolError("NOT_FOUND", "No customer with that id is in scope.")
        return self._facts(entry)

    def get_segment_statistics(self, **params: Any) -> SegmentStatistics:
        query = SegmentQuery.model_validate(params)
        targetable = self._targetable()
        members = [
            entry for entry in targetable
            if query.predicate.matches(entry.risk_level, entry.value_tier,
                                       {r.code for r in entry.reasons})
        ]
        assert self._records is not None
        records = [self._records[entry.customer_id] for entry in members]

        reason_frequency: dict[ReasonCode, int] = {}
        for entry in members:
            for reason in entry.reasons:
                reason_frequency[reason.code] = reason_frequency.get(reason.code, 0) + 1

        return SegmentStatistics(
            size=len(members),
            share_of_targetable=round(len(members) / len(targetable), 4) if targetable else 0.0,
            mean_churn_score=_mean([e.churn_score for e in members if e.churn_score is not None]),
            mean_email_open_rate=_mean([r.email_open_rate for r in records
                                        if r.email_open_rate is not None]),
            mean_sms_response_rate=_mean([r.sms_response_rate for r in records
                                          if r.sms_response_rate is not None]),
            counts_by_value_tier={
                tier: sum(1 for e in members if e.value_tier is tier) for tier in ValueTier
            },
            top_reason_codes=dict(
                sorted(reason_frequency.items(), key=lambda kv: kv[1], reverse=True)[:5]
            ),
        )

    # --- dispatch ---------------------------------------------------------

    TOOL_PARAMETERS: dict[str, type[BaseModel] | None] = {
        "get_churn_summary": None,
        "get_churn_candidates": CandidateFilters,
        "get_customer_behavior": CustomerLookup,
        "get_segment_statistics": SegmentQuery,
    }

    TOOL_DESCRIPTIONS = {
        "get_churn_summary": "Counts by risk level and value tier, reason-code "
                             "frequency and median days since purchase for the "
                             "account in scope.",
        "get_churn_candidates": "A capped sample of at-risk customers with their "
                                "scores and reason evidence. Returns the full "
                                "matching count alongside the sample.",
        "get_customer_behavior": "The behaviour record and reasons for one customer "
                                 "in the account in scope.",
        "get_segment_statistics": "Size, mean score, channel engagement means and "
                                  "tier mix for the customers matching a predicate.",
    }

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Single entry point for the tool loop.

        Every failure leaves here as a structured payload, so a validation error
        or a bug becomes something the model can read and retry rather than a
        stack trace describing our internals `[FR-17]`, `[EH-*]`.
        """
        arguments = arguments or {}
        try:
            if name not in self.TOOL_PARAMETERS:
                raise ToolError("UNKNOWN_TOOL", f"No tool named {name!r} is available.")
            method = getattr(self, name)
            result = method() if self.TOOL_PARAMETERS[name] is None else method(**arguments)
            return result.model_dump(mode="json")
        except ToolError as exc:
            return exc.as_payload()
        except Exception:
            return ToolError(
                "INVALID_ARGUMENTS",
                f"The arguments given to {name} were not valid for that tool.",
            ).as_payload()

    @classmethod
    def tool_definitions(cls) -> list[dict[str, Any]]:
        """OpenAI tool definitions, generated from the parameter models so the
        schema the model sees and the schema we validate against cannot drift."""
        definitions = []
        for name, model in cls.TOOL_PARAMETERS.items():
            schema = (
                model.model_json_schema()
                if model is not None
                else {"type": "object", "properties": {}}
            )
            definitions.append({
                "type": "function",
                "name": name,
                "description": cls.TOOL_DESCRIPTIONS[name],
                "parameters": schema,
            })
        return definitions
