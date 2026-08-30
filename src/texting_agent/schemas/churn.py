"""Churn domain types.

`churn_score` is a weighted heuristic ranking, not a calibrated probability: 0.87
does not mean an 87% chance of churning. It exists to order a list of customers,
and every consumer of it - API model, agent instructions, docs - says so `[R8]`.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"          # too little data to rank; never targeted [FR-04c]


class ValueTier(StrEnum):
    VIP = "VIP"
    HIGH_VALUE = "HIGH_VALUE"
    STANDARD = "STANDARD"
    LOW_VALUE = "LOW_VALUE"


class ReasonCode(StrEnum):
    """One code per scoring signal, so each traces to exactly one formula."""

    DORMANCY = "DORMANCY"
    PURCHASE_GAP = "PURCHASE_GAP"
    PURCHASE_DECLINE = "PURCHASE_DECLINE"
    ENGAGEMENT_DECLINE = "ENGAGEMENT_DECLINE"   # only with prior-window data
    LOW_ENGAGEMENT = "LOW_ENGAGEMENT"           # no prior window to compare against
    LOGIN_LAPSE = "LOGIN_LAPSE"
    CART_ABANDONMENT = "CART_ABANDONMENT"
    SUPPORT_FRICTION = "SUPPORT_FRICTION"


class Reason(BaseModel):
    """A reason the customer scored as they did, with the numbers behind it.

    `evidence` is the only factual basis the agent is ever given about a customer,
    which is what makes "do not fabricate" enforceable rather than aspirational.
    """

    code: ReasonCode
    contribution: float = Field(ge=0, le=1)
    evidence: dict[str, float | int | str | None]


class ChurnAssessment(BaseModel):
    customer_id: str
    churn_score: float | None = Field(default=None, ge=0, le=1)
    risk_level: RiskLevel
    value_tier: ValueTier
    reasons: list[Reason] = []
    signals_used: int = 0
    stale: bool = False

    @property
    def targetable(self) -> bool:
        """UNKNOWN risk and stale data are both reported but never campaigned to."""
        return self.risk_level is not RiskLevel.UNKNOWN and not self.stale


class AccountAssessment(BaseModel):
    account_id: str
    assessed: list[ChurnAssessment]          # ranked, highest risk first
    unknown_count: int = 0
    stale_count: int = 0
    tiering_suppressed: bool = False         # too few purchasers to rank [FR-09b]
    purchaser_count: int = 0
