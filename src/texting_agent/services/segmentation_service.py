"""Predicate evaluation `[FR-22]`, `[EC-06]`, `[EC-08]`.

The model proposed definitions; this decides who is in them. Two rules make the
result defensible:

* **One customer, one segment.** Predicates are evaluated in priority order and
  the first match wins, so a customer cannot be treated twice by one campaign.
* **Empty segments are dropped with a reason**, not silently. A campaign that
  quietly loses half its plan looks the same as one that worked.
"""

from dataclasses import dataclass, field

from texting_agent.schemas.agent_io import ProposedSegment
from texting_agent.schemas.churn import ChurnAssessment


@dataclass
class AssignedSegment:
    segment: ProposedSegment
    customer_ids: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.customer_ids)


@dataclass
class SegmentAssignment:
    segments: list[AssignedSegment]                 # non-empty, priority order
    dropped: list[tuple[str, str]] = field(default_factory=list)   # (name, reason)
    unassigned_count: int = 0

    @property
    def targeted_count(self) -> int:
        return sum(segment.size for segment in self.segments)


def assign(proposed: list[ProposedSegment],
           assessed: list[ChurnAssessment]) -> SegmentAssignment:
    """Assign targetable customers to at most one segment each.

    Only targetable customers are considered: UNKNOWN risk and stale data were
    already excluded upstream `[FR-04c]`, `[FR-10a]`. Passing them in here would
    put them back in a campaign by a side door.
    """
    ordered = sorted(proposed, key=lambda s: (s.priority, s.name))
    buckets = {segment.name: AssignedSegment(segment=segment) for segment in ordered}
    unassigned = 0

    for entry in assessed:
        if not entry.targetable:
            continue
        codes = {reason.code for reason in entry.reasons}
        for segment in ordered:
            if segment.predicate.matches(entry.risk_level, entry.value_tier, codes):
                buckets[segment.name].customer_ids.append(entry.customer_id)
                break
        else:
            unassigned += 1

    kept = [bucket for bucket in buckets.values() if bucket.size > 0]
    dropped = [(bucket.segment.name, "no customers matched this predicate")
               for bucket in buckets.values() if bucket.size == 0]
    return SegmentAssignment(segments=kept, dropped=dropped,
                             unassigned_count=unassigned)
