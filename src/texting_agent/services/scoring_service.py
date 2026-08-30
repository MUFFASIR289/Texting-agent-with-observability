"""Deterministic churn scoring `[FR-04]`-`[FR-08]`, `[FR-11]`.

Seven signals, each normalised to 0-1 where higher is worse. Three measure a
level, two measure a trend, two are windowed counters. The score is the weighted
mean over the signals that have data - renormalised, not zero-filled, so a
never-purchased customer drops the purchase signals rather than scoring 0 for
them `[EC-03]`, `[EC-05]`.

No LLM is involved at any point. The score is a heuristic ranking used to order a
list, not a calibrated probability: 0.87 does not mean an 87% chance of churning
`[R8]`.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from texting_agent.schemas.churn import (
    AccountAssessment,
    ChurnAssessment,
    Reason,
    ReasonCode,
    RiskLevel,
    ValueTier,
)
from texting_agent.schemas.customer import CustomerRecord
from texting_agent.services.scoring_config import ScoringConfig
from texting_agent.services.value_service import assign_tiers, is_purchaser


@dataclass
class Signal:
    name: str
    value: float
    code: ReasonCode
    evidence: dict = field(default_factory=dict)
    # A zero counter cannot tell "no events happened" from "we do not track this",
    # so it may lower a score but may not be a reason we claim to *know* a
    # customer. See the min_signals_required gate below `[RV-M2a]`.
    informative: bool = True


def _clamp(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def days_since(moment: datetime | None, now: datetime) -> float | None:
    """FR-08: derived at read time, never read from a stored column.

    A future timestamp is clock skew, not a negative age: clamped to 0 (DQ-03).
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max((now - moment).total_seconds() / 86400.0, 0.0)


def _recency(r: CustomerRecord, c: ScoringConfig, now: datetime) -> Signal | None:
    days = days_since(r.last_activity_at, now)
    if days is None:
        return None
    horizon = c.normalisation.inactivity_horizon_days
    return Signal("recency", _clamp(days / horizon), ReasonCode.DORMANCY,
                  {"days_since_activity": round(days, 1),
                   "inactivity_horizon_days": horizon})


def _login_lapse(r: CustomerRecord, c: ScoringConfig, now: datetime) -> Signal | None:
    days = days_since(r.last_login_at, now)
    if days is None:
        return None
    horizon = c.normalisation.inactivity_horizon_days
    return Signal("login_lapse", _clamp(days / horizon), ReasonCode.LOGIN_LAPSE,
                  {"days_since_login": round(days, 1),
                   "inactivity_horizon_days": horizon})


def _purchase_gap(r: CustomerRecord, c: ScoringConfig, now: datetime) -> Signal | None:
    # DQ-04: orders and last_purchase_at must agree, or the signal is unusable.
    days = days_since(r.last_purchase_at, now)
    if days is None or r.total_orders <= 0:
        return None
    expected = r.purchase_frequency_days
    if expected is None:
        tenure = days_since(r.registration_date, now) or 1.0
        expected = tenure / r.total_orders
    expected = max(expected, c.normalisation.expected_interval_floor_days)
    return Signal("purchase_gap", _clamp(days / expected), ReasonCode.PURCHASE_GAP,
                  {"days_since_purchase": round(days, 1),
                   "expected_interval_days": round(expected, 1)})


def _engagement(r: CustomerRecord, c: ScoringConfig, _now: datetime) -> Signal | None:
    """Channel-aware: the better of email and SMS, each against its own baseline.

    Scoring an SMS-primary customer as disengaged because they ignore email was a
    guaranteed false positive, and contradicted the channel-selection logic
    `[FR-04d]`.
    """
    n = c.normalisation

    def best(email: float | None, sms: float | None) -> tuple[float, str] | None:
        options = []
        if email is not None:
            options.append((email / n.baseline_email_open_rate, "EMAIL"))
        if sms is not None:
            options.append((sms / n.baseline_sms_response_rate, "SMS"))
        return max(options) if options else None

    current = best(r.email_open_rate, r.sms_response_rate)
    if current is None:
        # Covers DQ-04b: a prior window with no current window is unusable data,
        # not a 100% decline.
        return None
    now_value, channel = current
    prior = best(r.email_open_rate_prev_90d, r.sms_response_rate_prev_90d)

    evidence: dict = {
        "channel": channel,
        "email_open_rate": r.email_open_rate,
        "sms_response_rate": r.sms_response_rate,
    }
    if prior is not None and prior[0] > 0:
        prev_value = prior[0]
        evidence |= {
            "email_open_rate_prev_90d": r.email_open_rate_prev_90d,
            "sms_response_rate_prev_90d": r.sms_response_rate_prev_90d,
            "change_pct": round((now_value / prev_value - 1) * 100),
        }
        return Signal("engagement", _clamp(1 - now_value / prev_value),
                      ReasonCode.ENGAGEMENT_DECLINE, evidence)
    # Nothing to decline from, so this is a level, not a trend. A separate code
    # keeps any consumer from reading a trend into it `[FR-06]`.
    return Signal("engagement", _clamp(1 - now_value), ReasonCode.LOW_ENGAGEMENT, evidence)


def _purchase_decline(r: CustomerRecord, _c: ScoringConfig, _now: datetime) -> Signal | None:
    if r.orders_prev_90d <= 0:
        return None
    return Signal("purchase_decline", _clamp(1 - r.orders_last_90d / r.orders_prev_90d),
                  ReasonCode.PURCHASE_DECLINE,
                  {"orders_last_90d": r.orders_last_90d,
                   "orders_prev_90d": r.orders_prev_90d})


def _cart_abandon(r: CustomerRecord, c: ScoringConfig, _now: datetime) -> Signal:
    cap = c.normalisation.abandon_cap
    return Signal("cart_abandon", _clamp(r.cart_abandonment_count_90d / cap),
                  ReasonCode.CART_ABANDONMENT,
                  {"cart_abandonment_count_90d": r.cart_abandonment_count_90d,
                   "abandon_cap": cap},
                  informative=r.cart_abandonment_count_90d > 0)


def _support(r: CustomerRecord, c: ScoringConfig, _now: datetime) -> Signal:
    cap = c.normalisation.support_cap
    return Signal("support", _clamp(r.support_issue_count_90d / cap),
                  ReasonCode.SUPPORT_FRICTION,
                  {"support_issue_count_90d": r.support_issue_count_90d,
                   "support_cap": cap},
                  informative=r.support_issue_count_90d > 0)


SIGNAL_FUNCTIONS = (
    _recency, _purchase_gap, _engagement, _purchase_decline,
    _login_lapse, _cart_abandon, _support,
)


def _risk_level(score: float, c: ScoringConfig) -> RiskLevel:
    t = c.thresholds
    if score >= t.critical:
        return RiskLevel.CRITICAL
    if score >= t.high:
        return RiskLevel.HIGH
    if score >= t.medium:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def is_stale(record: CustomerRecord, config: ScoringConfig, now: datetime) -> bool:
    age = days_since(record.data_as_of, now)
    return age is not None and age > config.data_quality.freshness_window_days


def assess_customer(
    record: CustomerRecord,
    config: ScoringConfig,
    value_tier: ValueTier,
    now: datetime | None = None,
) -> ChurnAssessment:
    now = now or datetime.now(UTC)
    signals = [s for fn in SIGNAL_FUNCTIONS if (s := fn(record, config, now)) is not None]
    informative = sum(1 for s in signals if s.informative)
    stale = is_stale(record, config, now)

    if informative < config.min_signals_required:
        # A score built from one signal is a guess wearing a decimal point
        # `[FR-04c]`. Reported and counted, never targeted.
        return ChurnAssessment(
            customer_id=record.customer_id, churn_score=None,
            risk_level=RiskLevel.UNKNOWN, value_tier=value_tier,
            signals_used=informative, stale=stale,
        )

    weight_total = sum(config.weights[s.name] for s in signals)
    score = round(sum(config.weights[s.name] * s.value for s in signals) / weight_total, 4)

    reasons = sorted(
        (
            Reason(code=s.code,
                   contribution=round(config.weights[s.name] * s.value / weight_total, 4),
                   evidence=s.evidence)
            for s in signals if s.value >= config.reason_threshold
        ),
        key=lambda reason: reason.contribution,
        reverse=True,
    )
    return ChurnAssessment(
        customer_id=record.customer_id, churn_score=score,
        # Derived from the rounded score, so the level and the number a caller
        # sees can never disagree at a threshold boundary.
        risk_level=_risk_level(score, config), value_tier=value_tier,
        reasons=reasons, signals_used=informative, stale=stale,
    )


def assess_account(
    account_id: str,
    records: list[CustomerRecord],
    config: ScoringConfig,
    now: datetime | None = None,
) -> AccountAssessment:
    """Rank an account by risk. Value is assigned here but never feeds the score:
    risk and worth are separate axes `[FR-04d]`."""
    now = now or datetime.now(UTC)
    tiers, suppressed = assign_tiers(records, config)
    assessed = [assess_customer(r, config, tiers[r.customer_id], now) for r in records]
    # UNKNOWN sorts last: a null score is not a low score.
    assessed.sort(key=lambda a: (a.churn_score is None, -(a.churn_score or 0), a.customer_id))
    return AccountAssessment(
        account_id=account_id,
        assessed=assessed,
        unknown_count=sum(1 for a in assessed if a.risk_level is RiskLevel.UNKNOWN),
        stale_count=sum(1 for a in assessed if a.stale),
        tiering_suppressed=suppressed,
        purchaser_count=sum(1 for r in records if is_purchaser(r)),
    )
