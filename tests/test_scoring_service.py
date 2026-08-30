"""Scoring is the one thing in this system that must be *correct* rather than
plausible, so the expectations here are computed by hand, not captured from a run.

Config used throughout: weights recency .20, purchase_gap .20, engagement .20,
purchase_decline .15, login_lapse .10, cart_abandon .10, support .05;
horizon 90d, interval floor 7d, email baseline .25, sms baseline .10,
abandon cap 3, support cap 2; thresholds .80/.60/.35; reason threshold .60;
min signals 2.

Note the two counters are *always* scored - a zero count is a real observation
that lowers a score - so they contribute weight 0.15 to every denominator below,
even when their value is 0. What a zero counter may not do is prove we know
enough about a customer to rank them, which is the separate min_signals gate.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from texting_agent.schemas.churn import ReasonCode, RiskLevel, ValueTier
from texting_agent.schemas.customer import CustomerRecord
from texting_agent.services import scoring_config
from texting_agent.services.scoring_service import assess_customer

NOW = datetime(2026, 6, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def config():
    return scoring_config.load()


def customer(**overrides) -> CustomerRecord:
    """A customer with nothing but a registration, so each test adds exactly the
    fields its signals need and nothing else contributes."""
    base = {
        "account_id": "ACC_1",
        "customer_id": "C1",
        "registration_date": NOW - timedelta(days=400),
        "data_as_of": NOW,
    }
    return CustomerRecord.model_validate(base | overrides)


def score(record, config) -> float | None:
    return assess_customer(record, config, ValueTier.STANDARD, NOW).churn_score


# --- individual signals, hand-computed -------------------------------------


def test_two_timestamp_signals_and_two_empty_counters(config):
    """45d/90d = 0.5 on recency and login; both counters 0.
    (0.20*0.5 + 0.10*0.5) / (0.20+0.10+0.15) = 0.15/0.45 = 0.3333."""
    record = customer(
        last_activity_at=NOW - timedelta(days=45),
        last_login_at=NOW - timedelta(days=45),
    )
    assert score(record, config) == pytest.approx(0.3333, abs=1e-4)


def test_signals_renormalise_rather_than_zero_fill(config):
    """recency 90/90 = 1.0, login 9/90 = 0.1.
    (0.20*1.0 + 0.10*0.1) / 0.45 = 0.21/0.45 = 0.4667."""
    record = customer(
        last_activity_at=NOW - timedelta(days=90),
        last_login_at=NOW - timedelta(days=9),
    )
    assert score(record, config) == pytest.approx(0.4667, abs=1e-4)


def test_purchase_gap_uses_the_stated_frequency(config):
    """60d against a 30d expected interval -> clamped to 1.0.
    With recency 30/90 = 0.3333: (0.20*0.3333 + 0.20*1.0) / 0.55 = 0.4848."""
    record = customer(
        last_activity_at=NOW - timedelta(days=30),
        last_purchase_at=NOW - timedelta(days=60),
        total_orders=10,
        purchase_frequency_days=30,
    )
    assert score(record, config) == pytest.approx(0.4848, abs=1e-4)


def test_purchase_gap_falls_back_to_tenure_over_orders(config):
    """No stated frequency: 400d tenure / 8 orders = 50d expected.
    Gap 25/50 = 0.5. With recency 0: (0.20*0 + 0.20*0.5)/0.55 = 0.1818."""
    record = customer(
        last_activity_at=NOW,
        last_purchase_at=NOW - timedelta(days=25),
        total_orders=8,
    )
    assert score(record, config) == pytest.approx(0.1818, abs=1e-4)


def test_a_very_frequent_buyer_is_held_to_the_interval_floor(config):
    """A 1-day stated interval would make any gap look catastrophic; the 7-day
    floor is what stops that. 7d gap / 7d floor = 1.0, not 7.0.
    (0.20*0 + 0.20*1.0) / 0.55 = 0.3636."""
    record = customer(
        last_activity_at=NOW,
        last_purchase_at=NOW - timedelta(days=7),
        total_orders=300,
        purchase_frequency_days=1,
    )
    assert score(record, config) == pytest.approx(0.3636, abs=1e-4)


def test_purchase_decline_compares_the_two_windows(config):
    """1 order now against 4 before: 1 - 1/4 = 0.75.
    With recency 0: (0.20*0 + 0.15*0.75) / 0.50 = 0.225."""
    record = customer(last_activity_at=NOW, orders_last_90d=1, orders_prev_90d=4,
                      total_orders=20)
    assert score(record, config) == pytest.approx(0.225, abs=1e-4)


def test_counters_normalise_against_their_caps(config):
    """3 abandons / cap 3 = 1.0; 1 support issue / cap 2 = 0.5.
    With recency 0: (0.20*0 + 0.10*1.0 + 0.05*0.5) / 0.35 = 0.125/0.35 = 0.3571."""
    record = customer(last_activity_at=NOW, cart_abandonment_count_90d=3,
                      support_issue_count_90d=1)
    assert score(record, config) == pytest.approx(0.3571, abs=1e-4)


# --- engagement: the channel-aware trend signal ----------------------------


def test_engagement_decline_is_measured_against_the_prior_window(config):
    """Email 0.05/0.25 = 0.2 now against 0.20/0.25 = 0.8 before: 1 - 0.25 = 0.75.
    With recency 0: (0.20*0 + 0.20*0.75) / 0.55 = 0.2727."""
    record = customer(last_activity_at=NOW, email_open_rate=0.05,
                      email_open_rate_prev_90d=0.20)
    result = assess_customer(record, config, ValueTier.STANDARD, NOW)
    assert result.churn_score == pytest.approx(0.2727, abs=1e-4)
    assert [r.code for r in result.reasons] == [ReasonCode.ENGAGEMENT_DECLINE]
    assert result.reasons[0].evidence["change_pct"] == -75


def test_without_a_prior_window_the_code_is_low_engagement_not_decline(config):
    """FR-06: a level must never be reported as a trend."""
    record = customer(last_activity_at=NOW, email_open_rate=0.05)
    result = assess_customer(record, config, ValueTier.STANDARD, NOW)
    assert [r.code for r in result.reasons] == [ReasonCode.LOW_ENGAGEMENT]
    # 1 - 0.05/0.25 = 0.8, so (0.20*0 + 0.20*0.8)/0.55 = 0.2909
    assert result.churn_score == pytest.approx(0.2909, abs=1e-4)


def test_an_sms_primary_customer_is_not_punished_for_ignoring_email(config):
    """FR-04d. Email 0.0 alone would score 1.0; SMS 0.12 against a 0.10 baseline
    is 1.2, so engagement is 0 and the customer is not flagged at all."""
    record = customer(last_activity_at=NOW, email_open_rate=0.0, sms_response_rate=0.12)
    result = assess_customer(record, config, ValueTier.STANDARD, NOW)
    assert result.churn_score == pytest.approx(0.0, abs=1e-4)
    assert result.reasons == []


def test_a_prior_window_with_no_current_window_is_unusable_not_a_total_decline(config):
    """DQ-04b. Scoring this as 1 - 0/prev = 1.0 would invent a collapse out of a
    missing measurement."""
    record = customer(
        last_activity_at=NOW - timedelta(days=45),
        last_login_at=NOW - timedelta(days=45),
        email_open_rate_prev_90d=0.30,
    )
    result = assess_customer(record, config, ValueTier.STANDARD, NOW)
    assert [r.code for r in result.reasons] == []
    # recency and login only: 0.15/0.45 = 0.3333
    assert result.churn_score == pytest.approx(0.3333, abs=1e-4)


def test_zero_prior_engagement_is_a_level_not_an_infinite_decline(config):
    record = customer(last_activity_at=NOW, email_open_rate=0.05,
                      email_open_rate_prev_90d=0.0)
    result = assess_customer(record, config, ValueTier.STANDARD, NOW)
    assert [r.code for r in result.reasons] == [ReasonCode.LOW_ENGAGEMENT]


# --- missing data ----------------------------------------------------------


def test_a_never_purchased_customer_drops_the_purchase_signals(config):
    """EC-03. Scoring 0 for a purchase gap that cannot exist would make every
    browser look loyal; scoring 1 would make every browser look lapsed."""
    record = customer(last_activity_at=NOW - timedelta(days=60),
                      last_login_at=NOW - timedelta(days=60))
    result = assess_customer(record, config, ValueTier.LOW_VALUE, NOW)
    # 0.6667 on both timestamps: (0.20+0.10)*0.6667 / 0.45 = 0.4444
    assert result.churn_score == pytest.approx(0.4444, abs=1e-4)
    assert result.risk_level is RiskLevel.MEDIUM


def test_too_few_signals_gives_unknown_with_a_null_score(config):
    """FR-04c, EC-05. Only recency is usable; the two zero counters are not
    evidence that anything was observed."""
    record = customer(last_activity_at=NOW - timedelta(days=45))
    result = assess_customer(record, config, ValueTier.LOW_VALUE, NOW)
    assert result.risk_level is RiskLevel.UNKNOWN
    assert result.churn_score is None
    assert result.signals_used == 1
    assert result.targetable is False


def test_a_non_zero_counter_does_count_as_a_signal(config):
    record = customer(last_activity_at=NOW - timedelta(days=45),
                      cart_abandonment_count_90d=2)
    result = assess_customer(record, config, ValueTier.LOW_VALUE, NOW)
    assert result.risk_level is not RiskLevel.UNKNOWN
    assert result.signals_used == 2


def test_a_customer_with_nothing_at_all_is_unknown(config):
    result = assess_customer(customer(), config, ValueTier.LOW_VALUE, NOW)
    assert result.risk_level is RiskLevel.UNKNOWN
    assert result.signals_used == 0


# --- derived values, thresholds and reporting ------------------------------


def test_days_are_derived_at_read_time_not_stored(config):
    """FR-08: the same record scores differently a month later, with no write."""
    record = customer(last_activity_at=NOW - timedelta(days=10),
                      last_login_at=NOW - timedelta(days=10))
    later = assess_customer(record, config, ValueTier.STANDARD, NOW + timedelta(days=30))
    assert later.churn_score > assess_customer(record, config, ValueTier.STANDARD, NOW).churn_score


def test_a_future_timestamp_is_clock_skew_not_negative_age(config):
    """DQ-03, EC-21."""
    record = customer(last_activity_at=NOW + timedelta(days=5),
                      last_login_at=NOW + timedelta(days=5))
    assert score(record, config) == pytest.approx(0.0, abs=1e-4)


@pytest.mark.parametrize(
    ("days", "expected"),
    [(90, RiskLevel.CRITICAL), (45, RiskLevel.HIGH), (18, RiskLevel.MEDIUM),
     (0, RiskLevel.LOW)],
)
def test_thresholds_map_score_to_level(config, days, expected):
    """Counters pinned at their caps, so the score is (0.30*x + 0.15) / 0.45
    where x = days/90: 1.0, 0.6667, 0.4667, 0.3333."""
    record = customer(last_activity_at=NOW - timedelta(days=days),
                      last_login_at=NOW - timedelta(days=days),
                      cart_abandonment_count_90d=3, support_issue_count_90d=2)
    assert assess_customer(record, config, ValueTier.STANDARD, NOW).risk_level is expected


def test_only_signals_over_the_reason_threshold_become_reasons(config):
    """Recency 0.6667 is a reason; login 0.1111 is not, though both are scored."""
    record = customer(last_activity_at=NOW - timedelta(days=60),
                      last_login_at=NOW - timedelta(days=10))
    result = assess_customer(record, config, ValueTier.STANDARD, NOW)
    assert [r.code for r in result.reasons] == [ReasonCode.DORMANCY]
    assert result.reasons[0].evidence == {"days_since_activity": 60.0,
                                          "inactivity_horizon_days": 90}


def test_reasons_are_ordered_by_weighted_contribution(config):
    record = customer(last_activity_at=NOW - timedelta(days=90),
                      last_login_at=NOW - timedelta(days=90),
                      support_issue_count_90d=2)
    result = assess_customer(record, config, ValueTier.STANDARD, NOW)
    contributions = [r.contribution for r in result.reasons]
    assert contributions == sorted(contributions, reverse=True)
    assert [r.code for r in result.reasons] == [
        ReasonCode.DORMANCY, ReasonCode.LOGIN_LAPSE, ReasonCode.SUPPORT_FRICTION,
    ]


def test_stale_data_is_scored_and_reported_but_not_targetable(config):
    """FR-10a: excluded from targeting, never quietly dropped."""
    record = customer(last_activity_at=NOW - timedelta(days=45),
                      last_login_at=NOW - timedelta(days=45),
                      data_as_of=NOW - timedelta(days=30))
    result = assess_customer(record, config, ValueTier.STANDARD, NOW)
    assert result.stale is True
    assert result.churn_score is not None
    assert result.targetable is False


def test_value_never_moves_the_score(config):
    """FR-04d: spending retention budget where its return is worst is exactly
    what feeding value into risk would cause."""
    record = customer(last_activity_at=NOW - timedelta(days=45),
                      last_login_at=NOW - timedelta(days=45))
    scores = {
        tier: assess_customer(record, config, tier, NOW).churn_score
        for tier in ValueTier
    }
    assert len(set(scores.values())) == 1


def test_changing_a_weight_changes_the_output_with_no_code_change(config, tmp_path):
    """FR-05. Swapping the recency and login weights must move a score that leans
    on one of them, without touching a line of Python."""
    import yaml

    from texting_agent.config import settings
    from texting_agent.services import scoring_config

    raw = yaml.safe_load((Path(settings.config_dir) / "scoring.yaml").read_text())
    raw["weights"]["recency"], raw["weights"]["login_lapse"] = (
        raw["weights"]["login_lapse"], raw["weights"]["recency"],
    )
    altered = tmp_path / "scoring.yaml"
    altered.write_text(yaml.safe_dump(raw), encoding="utf-8")

    record = customer(last_activity_at=NOW - timedelta(days=90),
                      last_login_at=NOW - timedelta(days=9))
    assert score(record, config) == pytest.approx(0.4667, abs=1e-4)
    # 0.10*1.0 + 0.20*0.1 = 0.12, over 0.45 -> 0.2667
    assert score(record, scoring_config.load(altered)) == pytest.approx(0.2667, abs=1e-4)


def test_an_invalid_config_fails_to_load_rather_than_scoring_wrongly(tmp_path):
    import yaml

    from texting_agent.config import settings
    from texting_agent.services import scoring_config

    raw = yaml.safe_load((Path(settings.config_dir) / "scoring.yaml").read_text())
    raw["weights"]["recency"] = 0.50            # now sums to 1.30
    broken = tmp_path / "scoring.yaml"
    broken.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="sum to 1.0"):
        scoring_config.load(broken)
