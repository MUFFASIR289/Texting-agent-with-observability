"""Predicate evaluation `[FR-22]`, `[EC-06]`, `[EC-08]`."""

import pytest

from texting_agent.schemas.agent_io import ProposedSegment
from texting_agent.schemas.campaign import SegmentPredicate
from texting_agent.schemas.churn import (
    ChurnAssessment,
    Reason,
    ReasonCode,
    RiskLevel,
    ValueTier,
)
from texting_agent.services.segmentation_service import assign


def assessed(customer_id, risk=RiskLevel.HIGH, tier=ValueTier.STANDARD,
             codes=(), stale=False) -> ChurnAssessment:
    return ChurnAssessment(
        customer_id=customer_id, churn_score=None if risk is RiskLevel.UNKNOWN else 0.7,
        risk_level=risk, value_tier=tier, stale=stale,
        reasons=[Reason(code=code, contribution=0.2, evidence={}) for code in codes],
    )


def segment(name, priority, **predicate) -> ProposedSegment:
    return ProposedSegment(name=name, priority=priority,
                           predicate=SegmentPredicate(**predicate),
                           hypothesis="because")


def test_customers_land_in_the_segment_that_matches_them():
    result = assign(
        [segment("VIPs", 1, value_tiers=[ValueTier.VIP]),
         segment("Rest", 2)],
        [assessed("C1", tier=ValueTier.VIP), assessed("C2")],
    )
    by_name = {s.segment.name: s.customer_ids for s in result.segments}
    assert by_name == {"VIPs": ["C1"], "Rest": ["C2"]}


def test_a_customer_matching_two_segments_gets_only_the_higher_priority_one():
    """EC-08: a customer receives at most one treatment per campaign, so two
    matching predicates must not mean two messages."""
    result = assign(
        [segment("Broad", 2), segment("Specific", 1, risk_levels=[RiskLevel.HIGH])],
        [assessed("C1", risk=RiskLevel.HIGH)],
    )
    assert [(s.segment.name, s.customer_ids) for s in result.segments] == [
        ("Specific", ["C1"]),
    ]
    assert result.targeted_count == 1


def test_priority_order_decides_not_the_order_the_model_listed_them():
    result = assign(
        [segment("Second", 5), segment("First", 1)],
        [assessed("C1")],
    )
    assert result.segments[0].segment.name == "First"


def test_an_empty_segment_is_dropped_with_a_reason():
    """EC-06: a campaign that quietly loses half its plan looks exactly like one
    that worked."""
    result = assign(
        [segment("Nobody", 1, value_tiers=[ValueTier.VIP]), segment("Everyone", 2)],
        [assessed("C1", tier=ValueTier.STANDARD)],
    )
    assert [s.segment.name for s in result.segments] == ["Everyone"]
    assert result.dropped == [("Nobody", "no customers matched this predicate")]


def test_customers_matching_nothing_are_counted():
    result = assign([segment("VIPs", 1, value_tiers=[ValueTier.VIP])],
                    [assessed("C1"), assessed("C2"), assessed("C3", tier=ValueTier.VIP)])
    assert result.unassigned_count == 2
    assert result.targeted_count == 1


def test_unknown_risk_customers_are_never_assigned():
    """FR-04c. They were excluded upstream; letting them in here would put them
    back into a campaign by a side door."""
    result = assign([segment("Everyone", 1)],
                    [assessed("C1", risk=RiskLevel.UNKNOWN), assessed("C2")])
    assert result.segments[0].customer_ids == ["C2"]
    assert result.unassigned_count == 0


def test_stale_customers_are_never_assigned():
    """FR-10a."""
    result = assign([segment("Everyone", 1)],
                    [assessed("C1", stale=True), assessed("C2")])
    assert result.segments[0].customer_ids == ["C2"]


def test_reason_codes_can_be_required_and_excluded():
    population = [
        assessed("C1", codes=[ReasonCode.CART_ABANDONMENT]),
        assessed("C2", codes=[ReasonCode.DORMANCY]),
        assessed("C3", codes=[ReasonCode.CART_ABANDONMENT, ReasonCode.DORMANCY]),
    ]
    result = assign(
        [segment("Abandoners not dormant", 1,
                 required_reason_codes=[ReasonCode.CART_ABANDONMENT],
                 excluded_reason_codes=[ReasonCode.DORMANCY])],
        population,
    )
    assert result.segments[0].customer_ids == ["C1"]


def test_every_segment_being_empty_leaves_nothing_to_target():
    """EC-07: the caller sees an empty result and the reasons, not an exception."""
    result = assign([segment("Nobody", 1, value_tiers=[ValueTier.VIP])],
                    [assessed("C1", tier=ValueTier.STANDARD)])
    assert result.segments == []
    assert result.targeted_count == 0
    assert len(result.dropped) == 1


@pytest.mark.parametrize("size", [0, 1, 500])
def test_assignment_is_total(size):
    """Every targetable customer is either in a segment or counted as
    unassigned; none simply disappear."""
    population = [assessed(f"C{i}", risk=RiskLevel.HIGH if i % 2 else RiskLevel.LOW)
                  for i in range(size)]
    result = assign([segment("High risk", 1, risk_levels=[RiskLevel.HIGH])], population)
    assert result.targeted_count + result.unassigned_count == size
