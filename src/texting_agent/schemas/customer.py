"""Customer models.

`CustomerRecord` is the internal shape and carries PII. It must never be handed to
the agent - `CustomerFacts` (M3) is the only shape allowed near a prompt.
"""

from datetime import datetime

from pydantic import BaseModel, field_validator, model_validator

from texting_agent.schemas.churn import Reason, RiskLevel, ValueTier


def _clamp01(value: float | None) -> float | None:
    if value is None:
        return None
    return min(1.0, max(0.0, value))


class CustomerRecord(BaseModel):
    """One row of customer_agent_records, with read-time data-quality rules applied.

    Rates are clamped rather than rejected (DQ-01, DQ-02): a bad upstream value
    should degrade one signal, not fail the whole account's analysis.
    """

    account_id: str
    customer_id: str

    customer_name: str | None = None
    email: str | None = None
    phone: str | None = None

    customer_status: str = "ACTIVE"
    registration_date: datetime

    last_activity_at: datetime | None = None
    last_login_at: datetime | None = None
    last_purchase_at: datetime | None = None

    total_orders: int = 0
    total_spend: float = 0.0
    average_order_value: float | None = None
    purchase_frequency_days: float | None = None

    email_open_rate: float | None = None
    email_click_rate: float | None = None
    sms_response_rate: float | None = None
    orders_last_90d: int = 0
    cart_abandonment_count_90d: int = 0
    support_issue_count_90d: int = 0

    email_open_rate_prev_90d: float | None = None
    sms_response_rate_prev_90d: float | None = None
    orders_prev_90d: int = 0

    preferred_channel: str | None = None
    email_consent: bool = False
    sms_consent: bool = False
    last_purchase_category: str | None = None
    data_as_of: datetime

    @field_validator(
        "email_open_rate",
        "email_click_rate",
        "sms_response_rate",
        "email_open_rate_prev_90d",
        "sms_response_rate_prev_90d",
        mode="after",
    )
    @classmethod
    def _rates_within_bounds(cls, value: float | None) -> float | None:
        return _clamp01(value)  # DQ-01

    @model_validator(mode="after")
    def _internal_consistency(self) -> "CustomerRecord":
        # DQ-02: a click implies an open, so click rate cannot exceed open rate.
        if (
            self.email_click_rate is not None
            and self.email_open_rate is not None
            and self.email_click_rate > self.email_open_rate
        ):
            object.__setattr__(self, "email_click_rate", self.email_open_rate)

        # DQ-04a: windowed order counts cannot exceed the lifetime total.
        if self.orders_last_90d > self.total_orders:
            object.__setattr__(self, "orders_last_90d", self.total_orders)
        if self.orders_prev_90d > self.total_orders:
            object.__setattr__(self, "orders_prev_90d", self.total_orders)
        return self

    @property
    def has_purchased(self) -> bool:
        return self.total_orders > 0 and self.last_purchase_at is not None


class CustomerFacts(BaseModel):
    """The ONLY customer shape allowed near a prompt `[FR-14]`, `[SEC-06]`.

    Name, email and phone are absent by construction, not filtered out later, so
    there is no code path that can leak them into a request. Free-text customer
    fields are absent too, which closes the prompt-injection-via-data route: a
    customer cannot write instructions into a field the model will read
    `[EC-16]`, `[R4]`.
    """

    customer_id: str
    risk_level: RiskLevel
    churn_score: float | None = None       # null when UNKNOWN
    value_tier: ValueTier

    days_since_activity: int | None = None
    days_since_purchase: int | None = None
    days_since_login: int | None = None

    total_orders: int = 0
    total_spend: float = 0.0

    email_open_rate: float | None = None
    sms_response_rate: float | None = None
    email_open_rate_prev_90d: float | None = None
    sms_response_rate_prev_90d: float | None = None
    orders_last_90d: int = 0
    orders_prev_90d: int = 0

    reasons: list[Reason] = []
    stale: bool = False
