"""Stage inputs and outputs `[FR-20]`.

Every stage is a structured-output call with a schema on the way out. The schema
is the contract: a field the model cannot fill is a field it cannot invent, and
a closed enum is a vocabulary it cannot extend.
"""

from pydantic import BaseModel, Field

from texting_agent.schemas.campaign import SegmentPredicate
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
