"""Account-level assessment: the M2 deliverable, `assess_account` returning
ranked, explained candidates with the exclusions counted rather than hidden."""

from datetime import UTC, datetime, timedelta

import pytest

from texting_agent.schemas.churn import RiskLevel, ValueTier
from texting_agent.schemas.customer import CustomerRecord
from texting_agent.services import scoring_config
from texting_agent.services.scoring_service import assess_account

NOW = datetime(2026, 6, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def config():
    return scoring_config.load()


def record(customer_id: str, **overrides) -> CustomerRecord:
    base = {
        "account_id": "ACC_1", "customer_id": customer_id,
        "registration_date": NOW - timedelta(days=400), "data_as_of": NOW,
        "last_activity_at": NOW, "last_login_at": NOW,
    }
    return CustomerRecord.model_validate(base | overrides)


@pytest.fixture
def population() -> list[CustomerRecord]:
    lapsed = [
        record(f"L{i:02d}", last_activity_at=NOW - timedelta(days=80),
               last_login_at=NOW - timedelta(days=80),
               support_issue_count_90d=2, total_orders=5, total_spend=500.0)
        for i in range(3)
    ]
    healthy = [
        record(f"H{i:02d}", total_orders=5, total_spend=100.0,
               cart_abandonment_count_90d=1)
        for i in range(20)
    ]
    ghost = record("G01", last_activity_at=None, last_login_at=None)
    stale = record("S01", last_activity_at=NOW - timedelta(days=80),
                   last_login_at=NOW - timedelta(days=80),
                   support_issue_count_90d=2,
                   data_as_of=NOW - timedelta(days=45))
    return lapsed + healthy + [ghost, stale]


def test_candidates_come_back_ranked_by_risk(config, population):
    result = assess_account("ACC_1", population, config, NOW)
    scored = [a.churn_score for a in result.assessed if a.churn_score is not None]
    assert scored == sorted(scored, reverse=True)
    assert result.assessed[0].customer_id.startswith(("L", "S"))


def test_unknown_sorts_last_because_a_null_score_is_not_a_low_score(config, population):
    result = assess_account("ACC_1", population, config, NOW)
    assert result.assessed[-1].customer_id == "G01"
    assert result.assessed[-1].risk_level is RiskLevel.UNKNOWN


def test_exclusions_are_counted_not_silently_dropped(config, population):
    """FR-04c and FR-10a both report rather than hide: an operator who cannot see
    that 40 customers were skipped cannot tell a clean run from a broken one."""
    result = assess_account("ACC_1", population, config, NOW)
    assert len(result.assessed) == len(population)
    assert result.unknown_count == 1
    assert result.stale_count == 1
    assert sum(1 for a in result.assessed if not a.targetable) == 2


def test_reasons_are_attached_to_the_customers_that_earned_them(config, population):
    result = assess_account("ACC_1", population, config, NOW)
    by_id = {a.customer_id: a for a in result.assessed}
    assert {r.code.value for r in by_id["L00"].reasons} == {
        "DORMANCY", "LOGIN_LAPSE", "SUPPORT_FRICTION",
    }
    assert by_id["H00"].reasons == []


def test_tiering_is_suppressed_when_too_few_customers_have_spent(config):
    """FR-09b: reported on the account so the campaign can say why."""
    few = [record(f"P{i}", total_orders=2, total_spend=float(100 - i)) for i in range(5)]
    result = assess_account("ACC_1", few, config, NOW)
    assert result.tiering_suppressed is True
    assert result.purchaser_count == 5
    assert {a.value_tier for a in result.assessed} == {ValueTier.STANDARD}


def test_an_empty_account_does_not_explode(config):
    result = assess_account("ACC_EMPTY", [], config, NOW)
    assert result.assessed == []
    assert result.purchaser_count == 0
    assert result.tiering_suppressed is False
