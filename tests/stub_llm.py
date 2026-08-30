"""A deterministic, offline stand-in for the LLM `[NFR-10]`.

The agent depends on a Protocol, not on a vendor client, so a stub is the whole
of the test seam - no library patching, no recorded HTTP, no network. Every test
of agent behaviour runs against this.
"""

from dataclasses import dataclass, field

from pydantic import BaseModel

from texting_agent.agent.llm import StageFailed, StageResult, Usage


@dataclass
class StubLLMClient:
    """Returns queued outputs in order; records what it was asked.

    Queue an `Exception` to make that call fail. Queue nothing and the stub
    raises rather than inventing a reply, so a test that forgot to script a
    stage fails loudly instead of passing on a default.
    """

    queue: list[object] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    tokens_per_call: int = 100

    def parse[T: BaseModel](self, stage: str, instructions: str, prompt: str,
                            schema: type[T]) -> StageResult[T]:
        self.calls.append({"stage": stage, "instructions": instructions,
                           "prompt": prompt, "schema": schema})
        if not self.queue:
            raise AssertionError(
                f"the stub had no reply queued for stage {stage!r} "
                f"expecting {schema.__name__}"
            )
        nxt = self.queue.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        if not isinstance(nxt, schema):
            raise StageFailed(stage, 1, f"stub queued {type(nxt).__name__}, "
                                        f"stage wanted {schema.__name__}")
        return StageResult(
            output=nxt,
            usage=Usage(input_tokens=self.tokens_per_call,
                        output_tokens=self.tokens_per_call // 2),
            model="stub", stage=stage,
        )

    @property
    def prompts(self) -> list[str]:
        return [call["prompt"] for call in self.calls]

    @property
    def stages(self) -> list[str]:
        return [call["stage"] for call in self.calls]
