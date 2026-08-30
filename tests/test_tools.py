"""What the tools actually return `[FR-12]`, `[FR-15]`, `[FR-17]`.

Isolation and PII are covered in tests/security/; this file is about the numbers
and the filtering being right.
"""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from texting_agent.agent.tools import ScopedToolset
from texting_agent.database import agent_db
from texting_agent.database.repositories.customer_repo import CustomerRepository

NOW = datetime(2026, 6, 1, tzinfo=UTC)
COLUMNS = (
    "account_id, customer_id, customer_name, email, phone, registration_date, "
    "last_activity_at, last_login_at, last_purchase_at, total_orders, total_spend, "
    "purchase_frequency_days, email_open_rate, sms_response_rate, "
    "email_open_rate_prev_90d, orders_last_90d, orders_prev_90d, "
    "cart_abandonment_count_90d, support_issue_count_90d, data_as_of"
)


def iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def row(customer_id, *, activity, login, purchase=None, orders=0, spend=0.0,
        frequency=None, open_rate=None, sms=None, open_prev=None,
        orders_90=0, orders_prev=0, carts=0, support=0, as_of=0.0):
    return (
        "ACC_1", customer_id, f"Name {customer_id}", f"{customer_id}@example.test",
        f"+1555000{customer_id[-4:]}", iso(500),
        None if activity is None else iso(activity),
        None if login is None else iso(login),
        None if purchase is None else iso(purchase),
        orders, spend, frequency, open_rate, sms, open_prev,
        orders_90, orders_prev, carts, support, iso(as_of),
    )


POPULATION = [
    # Three critical, dormant, high-spending customers.
    *[row(f"C10{i}", activity=120, login=120, purchase=300, orders=20, spend=4000.0,
          frequency=20, support=2) for i in range(3)],
    # Twenty healthy purchasers, so tiering is not suppressed.
    *[row(f"C20{i}", activity=1, login=1, purchase=5, orders=10,
          spend=float(1000 - i * 10), frequency=30, open_rate=0.30) for i in range(20)],
    # One cart abandoner.
    row("C300", activity=3, login=3, purchase=40, orders=4, spend=300.0,
        frequency=25, carts=3),
    # One stale record and one customer too sparse to score.
    row("C400", activity=120, login=120, purchase=300, orders=6, spend=600.0,
        frequency=20, as_of=45),
    row("C500", activity=None, login=None),
]


@pytest.fixture(scope="module")
def toolset(tmp_path_factory):
    path = tmp_path_factory.mktemp("tools") / "customer_agent.db"
    agent_db.create(path)
    with sqlite3.connect(path) as conn:
        conn.executemany(
            f"INSERT INTO customer_agent_records ({COLUMNS}) "
            f"VALUES ({', '.join('?' * len(POPULATION[0]))})",
            POPULATION,
        )
    repo = CustomerRepository(agent_db.connect(path))
    return ScopedToolset("ACC_1", repo, now=NOW)


# --- summary ---------------------------------------------------------------


def test_summary_counts_everyone_but_offers_fewer(toolset):
    summary = toolset.get_churn_summary()
    assert summary.total_customers == 26
    # 26 minus the UNKNOWN one and the stale one.
    assert summary.targetable_customers == 24
    assert summary.unknown_count == 1
    assert summary.stale_count == 1


def test_summary_reports_the_reason_distribution_highest_first(toolset):
    frequency = toolset.get_churn_summary().reason_code_frequency
    assert list(frequency.values()) == sorted(frequency.values(), reverse=True)
    assert frequency["DORMANCY"] >= 3


def test_summary_median_ignores_customers_who_never_purchased(toolset):
    assert toolset.get_churn_summary().median_days_since_purchase == pytest.approx(5.0)


def test_summary_states_that_the_score_is_a_heuristic(toolset):
    """R8: the caveat travels with the number, not only in the documentation."""
    assert "not calibrated" in toolset.get_churn_summary().note


# --- candidates ------------------------------------------------------------


def test_candidates_are_filtered_by_risk_level(toolset):
    result = toolset.get_churn_candidates(risk_level="CRITICAL")
    assert result.matching_count == 3
    assert {c.risk_level.value for c in result.candidates} == {"CRITICAL"}


def test_candidates_are_filtered_by_reason_code(toolset):
    result = toolset.get_churn_candidates(reason_code="CART_ABANDONMENT")
    assert [c.customer_id for c in result.candidates] == ["C300"]


def test_candidates_are_filtered_by_value_tier(toolset):
    result = toolset.get_churn_candidates(value_tier="VIP")
    assert result.matching_count >= 1
    assert {c.value_tier.value for c in result.candidates} == {"VIP"}


def test_the_full_count_travels_with_the_truncated_sample(toolset):
    """FR-15: the model must be able to see it is looking at a sample."""
    result = toolset.get_churn_candidates(limit=2)
    assert result.returned_count == 2
    assert result.matching_count == 24
    assert result.limit_applied == 2


def test_candidates_come_back_ranked(toolset):
    scores = [c.churn_score for c in toolset.get_churn_candidates(limit=50).candidates]
    assert scores == sorted(scores, reverse=True)


def test_unknown_and_stale_customers_are_never_offered_as_candidates(toolset):
    """FR-04c and FR-10a: counted in the summary, absent from the targets."""
    offered = {c.customer_id for c in toolset.get_churn_candidates(limit=50).candidates}
    assert "C500" not in offered      # UNKNOWN
    assert "C400" not in offered      # stale
    assert len(offered) == 24


def test_a_filter_matching_nobody_returns_an_empty_list_not_an_error(toolset):
    result = toolset.get_churn_candidates(risk_level="CRITICAL", value_tier="VIP",
                                          reason_code="SUPPORT_FRICTION", limit=5)
    assert result.candidates == [] or result.matching_count == result.returned_count


# --- one customer ----------------------------------------------------------


def test_customer_behavior_returns_derived_days_not_stored_ones(toolset):
    facts = toolset.get_customer_behavior(customer_id="C100")
    assert facts.days_since_activity == 120
    assert facts.days_since_purchase == 300
    assert facts.days_since_login == 120


def test_customer_behavior_includes_both_engagement_windows(toolset):
    """RV-B3: without the prior window the model cannot check a decline claim."""
    facts = toolset.get_customer_behavior(customer_id="C200")
    assert facts.email_open_rate == 0.30
    assert set(facts.model_dump()) >= {"email_open_rate_prev_90d",
                                       "sms_response_rate_prev_90d",
                                       "orders_last_90d", "orders_prev_90d"}


def test_an_unknown_risk_customer_can_still_be_looked_up(toolset):
    """Reported, not hidden: the operator asking about C500 deserves an answer."""
    facts = toolset.get_customer_behavior(customer_id="C500")
    assert facts.risk_level.value == "UNKNOWN"
    assert facts.churn_score is None


# --- segments --------------------------------------------------------------


def test_segment_statistics_describe_the_matching_customers(toolset):
    stats = toolset.get_segment_statistics(
        predicate={"risk_levels": ["CRITICAL"], "required_reason_codes": ["DORMANCY"]}
    )
    assert stats.size == 3
    assert stats.mean_churn_score is not None
    assert stats.share_of_targetable == pytest.approx(3 / 24, abs=1e-4)


def test_an_empty_predicate_matches_every_targetable_customer(toolset):
    assert toolset.get_segment_statistics(predicate={}).size == 24


def test_excluded_reason_codes_remove_customers(toolset):
    with_abandonment = toolset.get_segment_statistics(
        predicate={"required_reason_codes": ["CART_ABANDONMENT"]}
    )
    without = toolset.get_segment_statistics(
        predicate={"excluded_reason_codes": ["CART_ABANDONMENT"]}
    )
    assert with_abandonment.size == 1
    assert without.size == 23


def test_channel_means_are_reported_for_channel_selection(toolset):
    """FR-24 requires the channel choice to be justified by these numbers, so the
    tool has to supply them."""
    stats = toolset.get_segment_statistics(predicate={"risk_levels": ["LOW"]})
    assert stats.size == 20
    assert stats.mean_email_open_rate == pytest.approx(0.30, abs=1e-4)
    # Nobody in this segment has an SMS rate: a mean over nothing is null, not 0,
    # or the channel choice would be justified by a number nobody measured.
    assert stats.mean_sms_response_rate is None


def test_a_segment_matching_nobody_reports_zero_rather_than_failing(toolset):
    stats = toolset.get_segment_statistics(
        predicate={"risk_levels": ["LOW"], "required_reason_codes": ["DORMANCY"]}
    )
    assert stats.size == 0
    assert stats.mean_churn_score is None
    assert stats.share_of_targetable == 0.0


# --- caching ---------------------------------------------------------------


def test_the_account_is_scored_once_per_toolset(toolset):
    """A tool loop asking four questions should not re-rank the account four
    times; at 5,000 customers that is one pass instead of four."""
    calls: list[str] = []

    class CountingRepo:
        def __init__(self, inner):
            self._inner = inner

        def list_for_account(self, account_id):
            calls.append(account_id)
            return self._inner.list_for_account(account_id)

    fresh = ScopedToolset("ACC_1", CountingRepo(toolset._repo), now=NOW)
    fresh.get_churn_summary()
    fresh.get_churn_candidates(limit=1)
    fresh.get_segment_statistics(predicate={})
    fresh.get_customer_behavior(customer_id="C100")
    assert calls == ["ACC_1"]
