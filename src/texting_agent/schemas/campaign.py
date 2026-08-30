"""Campaign vocabulary.

Closed enums, not free text. The model picks an id from these; anything it makes
up fails validation before it can reach a customer.
"""

from enum import StrEnum

from pydantic import BaseModel

from texting_agent.schemas.churn import ReasonCode, RiskLevel, ValueTier


class Channel(StrEnum):
    EMAIL = "EMAIL"
    SMS = "SMS"


class OfferType(StrEnum):
    PERCENTAGE_DISCOUNT = "PERCENTAGE_DISCOUNT"
    FIXED_DISCOUNT = "FIXED_DISCOUNT"
    FREE_SHIPPING = "FREE_SHIPPING"
    LOYALTY_POINTS = "LOYALTY_POINTS"
    EARLY_ACCESS = "EARLY_ACCESS"
    NONE = "NONE"


class PlaybookId(StrEnum):
    VIP_REACTIVATION = "VIP_REACTIVATION"
    PRICE_SENSITIVE = "PRICE_SENSITIVE"
    CART_ABANDONMENT = "CART_ABANDONMENT"
    DORMANT = "DORMANT"
    SUPPORT_RECOVERY = "SUPPORT_RECOVERY"


class CampaignState(StrEnum):
    """Thirteen states `[FR-63]`. The orchestrator owns transitions; the model
    has no way to name one."""

    RECEIVED = "RECEIVED"
    ANALYZING = "ANALYZING"
    SEGMENTED = "SEGMENTED"
    PLANNED = "PLANNED"
    CONTENT_READY = "CONTENT_READY"
    VALIDATED = "VALIDATED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    SENDING = "SENDING"
    SENT = "SENT"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SegmentPredicate(BaseModel):
    """A segment definition, not a list of customers `[FR-21]`.

    The model proposes these; deterministic code decides who matches `[FR-22]`.
    Every field is a closed enum, so a predicate cannot express anything the
    scoring service did not already compute.
    """

    risk_levels: list[RiskLevel] = []
    value_tiers: list[ValueTier] = []
    required_reason_codes: list[ReasonCode] = []
    excluded_reason_codes: list[ReasonCode] = []

    def matches(self, risk_level: RiskLevel, value_tier: ValueTier,
                reason_codes: set[ReasonCode]) -> bool:
        """An empty list means "no constraint on this field", not "matches none"."""
        if self.risk_levels and risk_level not in self.risk_levels:
            return False
        if self.value_tiers and value_tier not in self.value_tiers:
            return False
        if not set(self.required_reason_codes) <= reason_codes:
            return False
        return not set(self.excluded_reason_codes) & reason_codes
