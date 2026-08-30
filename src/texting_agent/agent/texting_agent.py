"""The single agent `[FR-19]`.

One class, one toolset, one account. There are no sub-agents and no delegation:
a test asserts this is the only agent class in the codebase, because "one agent"
is a claim that decays the moment a second one is convenient.

What this class deliberately cannot do:

* **Choose an account.** The toolset was constructed with one, and there is no
  parameter here to change it `[AZ-03]`.
* **Write anything.** It holds no app-DB connection. Stage usage travels back in
  the `StageResult` and the orchestrator persists it `[SEC-09]`.
* **Advance a campaign.** State transitions are the orchestrator's, so no model
  output can approve, send or cancel `[SEC-09]`.
* **Loop without a bound.** Only `query()` calls tools repeatedly, and it is
  capped `[EC-18]`.
"""

import json

import structlog
from pydantic import BaseModel, Field

from texting_agent.agent import instructions, prompts
from texting_agent.agent.llm import LLMClient, StageResult
from texting_agent.agent.tools import ScopedToolset
from texting_agent.schemas.agent_io import AgentAnswer, ChurnAnalysis, SegmentationResult

log = structlog.get_logger()

CANDIDATE_SAMPLE_SIZE = 20


class _ToolStep(BaseModel):
    """One step of the tool loop: which tool, with what arguments, or none.

    Arguments arrive as a JSON string rather than an open object. A free-form
    nested dict is the one place a structured-output schema stops constraining
    anything, and the dispatcher validates the parsed result against the tool
    parameter model regardless. Defined here rather than in schemas/ because
    nothing outside this loop has a use for it.
    """

    tool: str | None = None
    arguments_json: str = Field(default="{}")

    def arguments_as_dict(self) -> dict:
        try:
            parsed = json.loads(self.arguments_json or "{}")
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}


class TextingAgent:
    def __init__(self, client: LLMClient, toolset: ScopedToolset,
                 max_tool_iterations: int = 6) -> None:
        self._client = client
        self._tools = toolset
        self._max_tool_iterations = max_tool_iterations

    def analyze(self, market_context: str | None = None) -> StageResult[ChurnAnalysis]:
        summary = self._tools.get_churn_summary()
        sample = self._tools.get_churn_candidates(limit=CANDIDATE_SAMPLE_SIZE)
        return self._client.parse(
            "analyze",
            instructions.ANALYZE,
            prompts.analyze_prompt(summary, sample, market_context),
            ChurnAnalysis,
        )

    def segment(self, analysis: ChurnAnalysis,
                goal: str) -> StageResult[SegmentationResult]:
        summary = self._tools.get_churn_summary()
        return self._client.parse(
            "segment",
            instructions.SEGMENT,
            prompts.segment_prompt(analysis, summary, goal),
            SegmentationResult,
        )

    def query(self, question: str) -> tuple[StageResult[AgentAnswer], list[str], bool]:
        """Answer an operator question, calling tools until it can `[FR-65]`.

        Returns the answer, the tools actually called, and whether the iteration
        cap was reached. The cap is what stops a model that keeps asking the same
        question from spending the budget on it `[EC-18]`; hitting it truncates
        rather than fails, because a grounded partial answer beats an error.
        """
        called: list[str] = []
        observations: list[str] = []
        truncated = False

        for iteration in range(self._max_tool_iterations):
            plan = self._client.parse(
                "query",
                instructions.QUERY + _tool_menu(),
                prompts.query_prompt(question) + _observations_block(observations),
                _ToolStep,
            )
            step = plan.output
            if step.tool is None:
                break
            called.append(step.tool)
            # The dispatcher validates the arguments and converts any failure into
            # a structured payload, so a bad tool call is something the model can
            # read and correct rather than a crash `[EC-17]`.
            result = self._tools.call(step.tool, step.arguments_as_dict())
            observations.append(f"{step.tool} -> {json.dumps(result, default=str)}")
            log.info("agent.tool_call", tool=step.tool, iteration=iteration + 1)
        else:
            truncated = True

        answer = self._client.parse(
            "query",
            instructions.QUERY,
            prompts.query_prompt(question) + _observations_block(observations)
            + ("\n\nYou have reached your tool limit. Answer from what you have."
               if truncated else "\n\nAnswer the question now."),
            AgentAnswer,
        )
        return answer, called, truncated


def _tool_menu() -> str:
    lines = ["", "Tools available to you:"]
    for definition in ScopedToolset.tool_definitions():
        lines.append(f"- {definition['name']}: {definition['description']}")
        lines.append(f"  parameters: {json.dumps(definition['parameters'])}")
    lines.append("")
    lines.append("Reply with the next tool to call, or with tool set to null when "
                 "you have enough to answer.")
    return "\n".join(lines)


def _observations_block(observations: list[str]) -> str:
    if not observations:
        return ""
    return "\n\nWhat your tools have returned so far:\n" + "\n".join(observations)
