"""Value tiering `[FR-09]`. Bands: top 5% VIP, next 20% HIGH_VALUE, next 50%
STANDARD, remainder LOW_VALUE - over purchasers only."""

from datetime import UTC, datetime

import pytest

from texting_agent.schemas.churn import ValueTier
from texting_agent.schemas.customer import CustomerRecord
from texting_agent.services import scoring_config
from texting_agent.services.value_service import assign_tiers

NOW = datetime(2026, 6, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def config():
    return scoring_config.load()


def purchaser(customer_id: str, spend: float, orders: int = 3) -> CustomerRecord:
    return CustomerRecord(
        account_id="ACC_1", customer_id=customer_id, registration_date=NOW,
        data_as_of=NOW, total_orders=orders, total_spend=spend,
    )


def browser(customer_id: str) -> CustomerRecord:
    return CustomerRecord(account_id="ACC_1", customer_id=customer_id,
                          registration_date=NOW, data_as_of=NOW)


def hundred_purchasers() -> list[CustomerRecord]:
    """Spend 100 down to 1, so position in the ranking is unambiguous."""
    return [purchaser(f"C{i:03d}", spend=float(101 - i)) for i in range(1, 101)]


def test_bands_land_on_the_documented_percentiles(config):
    tiers, suppressed = assign_tiers(hundred_purchasers(), config)
    assert suppressed is False
    counts = {t: sum(1 for v in tiers.values() if v is t) for t in ValueTier}
    assert counts == {ValueTier.VIP: 5, ValueTier.HIGH_VALUE: 20,
                      ValueTier.STANDARD: 50, ValueTier.LOW_VALUE: 25}


@pytest.mark.parametrize(
    ("customer_id", "expected"),
    [("C001", ValueTier.VIP),          # rank 1
     ("C005", ValueTier.VIP),          # rank 5, last VIP
     ("C006", ValueTier.HIGH_VALUE),   # rank 6, first HIGH_VALUE
     ("C025", ValueTier.HIGH_VALUE),   # rank 25, last HIGH_VALUE
     ("C026", ValueTier.STANDARD),     # rank 26, first STANDARD
     ("C075", ValueTier.STANDARD),     # rank 75, last STANDARD
     ("C076", ValueTier.LOW_VALUE),    # rank 76, first LOW_VALUE
     ("C100", ValueTier.LOW_VALUE)],
)
def test_each_band_boundary(config, customer_id, expected):
    tiers, _ = assign_tiers(hundred_purchasers(), config)
    assert tiers[customer_id] is expected


def test_non_purchasers_skip_the_ranking_entirely(config):
    """EC-23: percentiles over a population that is mostly zeros would hand VIP to
    an arbitrary never-purchaser."""
    records = hundred_purchasers() + [browser(f"B{i:03d}") for i in range(500)]
    tiers, _ = assign_tiers(records, config)
    assert all(tiers[f"B{i:03d}"] is ValueTier.LOW_VALUE for i in range(500))
    assert tiers["C001"] is ValueTier.VIP


def test_a_customer_with_orders_but_no_spend_is_low_value(config):
    """FR-09a. Refunded to zero is not VIP behaviour."""
    records = hundred_purchasers() + [purchaser("Z001", spend=0.0, orders=9)]
    tiers, _ = assign_tiers(records, config)
    assert tiers["Z001"] is ValueTier.LOW_VALUE


def test_too_few_purchasers_suppresses_tiering(config):
    """FR-09b, EC-24: 19 purchasers cannot support a 5% band, so nobody is ranked
    and the caller is told why."""
    records = [purchaser(f"C{i:03d}", spend=float(100 - i)) for i in range(19)]
    tiers, suppressed = assign_tiers(records, config)
    assert suppressed is True
    assert set(tiers.values()) == {ValueTier.STANDARD}


def test_twenty_purchasers_is_enough_to_rank(config):
    records = [purchaser(f"C{i:03d}", spend=float(100 - i)) for i in range(20)]
    tiers, suppressed = assign_tiers(records, config)
    assert suppressed is False
    assert tiers["C000"] is ValueTier.VIP


def test_an_account_with_no_purchasers_at_all_is_not_suppressed(config):
    """Nothing to rank is not the same as too little to rank: there is no caveat
    to report, only a page of LOW_VALUE browsers."""
    tiers, suppressed = assign_tiers([browser(f"B{i}") for i in range(50)], config)
    assert suppressed is False
    assert set(tiers.values()) == {ValueTier.LOW_VALUE}


def test_equal_spend_ranks_deterministically(config):
    """Ties broken by customer_id, so the same data always produces the same
    tiers - an operator seeing a tier move wants a reason for it."""
    records = [purchaser(f"C{i:03d}", spend=50.0) for i in range(100)]
    first, _ = assign_tiers(records, config)
    second, _ = assign_tiers(list(reversed(records)), config)
    assert first == second
    assert first["C000"] is ValueTier.VIP
