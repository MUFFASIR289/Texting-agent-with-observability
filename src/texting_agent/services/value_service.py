"""Percentile value tiering `[FR-09]`, `[FR-09a]`, `[FR-09b]`.

Percentiles within the account, not currency thresholds, so the same code serves
a boutique and an enterprise. Non-purchasers are routed to LOW_VALUE without
entering the ranking: percentiles over a population that is mostly zeros produce
ties that would label arbitrary never-purchasers as VIP `[EC-23]`.

Value is a separate axis from risk and never enters the churn score `[FR-04d]`.
"""

from texting_agent.schemas.churn import ValueTier
from texting_agent.schemas.customer import CustomerRecord
from texting_agent.services.scoring_config import ScoringConfig


def is_purchaser(record: CustomerRecord) -> bool:
    """FR-09a. Keyed on orders and money, not on `last_purchase_at`: a missing
    purchase timestamp disables the purchase-gap *signal* (DQ-04), but it does
    not make the spend on the account disappear.
    """
    return record.total_orders > 0 and record.total_spend > 0


def assign_tiers(
    records: list[CustomerRecord], config: ScoringConfig
) -> tuple[dict[str, ValueTier], bool]:
    """Return customer_id -> tier, and whether tiering was suppressed.

    Suppressed means the account has too few purchasers for a percentile to mean
    anything, so every purchaser is STANDARD and the campaign response says why
    `[FR-09b]`, `[EC-24]`.
    """
    tiers = {r.customer_id: ValueTier.LOW_VALUE for r in records}
    purchasers = [r for r in records if is_purchaser(r)]

    if len(purchasers) < config.value.min_purchasers_for_tiering:
        for r in purchasers:
            tiers[r.customer_id] = ValueTier.STANDARD
        return tiers, bool(purchasers)

    # Descending spend; customer_id breaks ties so the same data always ranks the
    # same way.
    purchasers.sort(key=lambda r: (-r.total_spend, r.customer_id))
    total = len(purchasers)
    v = config.value
    vip_end = round(total * v.vip_pct)
    high_end = vip_end + round(total * v.high_pct)
    standard_end = high_end + round(total * v.standard_pct)

    for position, record in enumerate(purchasers):
        if position < vip_end:
            tier = ValueTier.VIP
        elif position < high_end:
            tier = ValueTier.HIGH_VALUE
        elif position < standard_end:
            tier = ValueTier.STANDARD
        else:
            tier = ValueTier.LOW_VALUE
        tiers[record.customer_id] = tier

    return tiers, False
