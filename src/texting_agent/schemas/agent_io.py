"""Stage inputs and outputs `[FR-20]`.

Every stage is a structured-output call with a schema on the way out. The schema
is the contract: a field the model cannot fill is a field it cannot invent, and
a closed enum is a vocabulary it cannot extend.
"""

from pydantic import BaseModel, Field

from texting_agent.schemas.campaign import (
    Channel,
    CtaUrlKey,
    OfferType,
    PlaybookId,
    SegmentPredicate,
)
from texting_agent.schemas.churn import ReasonCode


class Pattern(BaseModel):
    code: ReasonCode                      # closed enum, so no invented pattern
    share_of_at_risk: float = Field(ge=0, le=1)
    interpretation: str


class ChurnAnalysis(BaseModel):
    """ANALYZE. Interpretation of numbers the model was given, never numbers it
    worked out: every share here is checked against the computed distribution."""

    headline: str
    dominant_patterns: list[Pattern] = Field(min_length=1, max_length=5)
    cohorts_of_concern: list[str] = Field(max_length=5)
    caveats: list[str] = Field(min_length=1, max_length=4)


class ProposedSegment(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    priority: int = Field(ge=1, le=6)     # lower wins when a customer matches two
    predicate: SegmentPredicate
    hypothesis: str = Field(min_length=1)


class SegmentationResult(BaseModel):
    """SEGMENT. Definitions, not assignments `[FR-21]`. The model never sees or
    emits a list of individual customers, so it cannot target one by name."""

    segments: list[ProposedSegment] = Field(min_length=1, max_length=6)


class AgentAnswer(BaseModel):
    """The tool-loop answer for POST /agent/query `[FR-65]`."""

    answer: str
    grounded_in: list[str] = Field(
        default=[], description="The tools whose output this answer rests on."
    )


class Offer(BaseModel):
    """What the customer is offered. `value` means whatever the type implies -
    a percentage, an amount, a number of points - and policy caps it per tier
    in M7. `NONE` is a real choice, not a missing one."""

    type: OfferType
    value: float = Field(default=0, ge=0)
    code: str | None = Field(default=None, max_length=32)


class RetentionPlan(BaseModel):
    """PLAN, one per surviving segment.

    `message_count` and `followup_days` are deliberately absent `[FR-23a]`: v1
    sends one message per selected channel and has no scheduler, so a follow-up
    cadence could never fire. A field the system cannot honour is worse than no
    field, because it promises a capability to whoever reads the plan.
    """

    segment_name: str
    playbook_id: PlaybookId               # closed enum, checked against config
    offer: Offer
    channels: list[Channel] = Field(min_length=1, max_length=2)
    channel_rationale: str = Field(min_length=1)   # must cite engagement [FR-24]
    variants_per_channel: int = Field(default=2, ge=2, le=3)


class RetentionPlanSet(BaseModel):
    plans: list[RetentionPlan] = Field(min_length=1, max_length=6)


class MessageVariant(BaseModel):
    """A template, never a message `[FR-25]`.

    The model writes `{{first_name}}`; code substitutes the value. Fabricating a
    customer fact is therefore not something the model can do wrong - it is
    something it cannot express `[R1]`.
    """

    channel: Channel
    subject_template: str | None = None                 # email only
    body_template: str = Field(min_length=1)
    cta_text: str | None = None
    cta_url_key: CtaUrlKey | None = None   # closed, so an invented key cannot be said


class MessageVariantSet(BaseModel):
    """GENERATE, one call per segment. At least two variants per channel so
    there is something to A/B test `[FR-33]`."""

    segment_name: str
    variants: list[MessageVariant] = Field(min_length=2, max_length=6)
