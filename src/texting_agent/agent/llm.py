"""The agent's view of a language model `[FR-27]`, `[EH-03]`.

Deliberately a protocol with no vendor import. Two things follow: the agent
package stays free of `httpx`, provider SDKs and anything that could reach the
network on its own `[SEC-09]`, and the whole test suite runs offline against a
stub without patching a client library `[NFR-10]`.
"""

from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class BudgetExceeded(Exception):
    """The run cost more than it was allowed to `[EH-03]`. Fails the campaign
    with BUDGET_EXCEEDED rather than quietly costing more."""


class StageFailed(Exception):
    """A stage could not produce valid output after its retries `[EH-01]`,
    `[EH-02]`. Carries the stage and the last error, never a stack trace."""

    def __init__(self, stage: str, attempts: int, last_error: str) -> None:
        super().__init__(f"{stage} failed after {attempts} attempts: {last_error}")
        self.stage = stage
        self.attempts = attempts
        self.last_error = last_error


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class TokenBudget:
    """A hard cap for one run, not a warning `[NFR-04]`.

    Checked before each call as well as after, so a stage cannot start a request
    the budget cannot pay for.
    """

    max_tokens: int
    used: Usage = field(default_factory=Usage)

    @property
    def remaining(self) -> int:
        return self.max_tokens - self.used.total_tokens

    def check(self) -> None:
        if self.remaining <= 0:
            raise BudgetExceeded(
                f"token budget of {self.max_tokens} exhausted "
                f"({self.used.total_tokens} used)"
            )

    def record(self, usage: Usage) -> None:
        self.used.input_tokens += usage.input_tokens
        self.used.output_tokens += usage.output_tokens


@dataclass
class StageResult[T: BaseModel]:
    """What a stage returns: the parsed output and what it cost.

    Usage travels back with the result rather than being written here, because
    the agent holds no app-DB connection - the orchestrator persists it
    `[SEC-09]`, `[RV-C4]`.
    """

    output: T
    usage: Usage
    model: str
    stage: str
    attempts: int = 1


class LLMClient(Protocol):
    def parse(
        self,
        stage: str,
        instructions: str,
        prompt: str,
        schema: type[T],
    ) -> StageResult[T]:
        """Return `schema` parsed from the model, or raise `StageFailed`."""
        ...
