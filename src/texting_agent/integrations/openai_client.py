"""OpenAI implementation of `LLMClient` `[EH-01]`, `[EH-02]`, `[FR-27]`.

Verified against the pinned SDK (openai 3.6.0): `responses.parse` accepts
`model`, `instructions`, `input`, `text_format` and `timeout`, and returns a
response carrying `output_parsed` and a `usage` with `input_tokens` and
`output_tokens`.

Retries cover transient failures only - 429, 5xx and timeouts. A 400 is a bug in
our request and retrying it three times just makes the same mistake slower. A
schema failure gets exactly one re-ask with the error appended `[EH-02]`,
because a model that cannot satisfy a schema twice will not satisfy it a third
time either.
"""

import random
import time

import structlog
from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

from texting_agent.agent.llm import StageFailed, StageResult, TokenBudget, Usage
from texting_agent.config import settings

log = structlog.get_logger()

TRANSIENT = (RateLimitError, InternalServerError, APITimeoutError, APIConnectionError)


class OpenAILLMClient:
    def __init__(self, budget: TokenBudget, client: OpenAI | None = None) -> None:
        self._budget = budget
        self._client = client or OpenAI(api_key=settings.openai_api_key)

    def _model_for(self, stage: str) -> str:
        return settings.stage_models.get(stage, settings.openai_model_default)

    def parse[T: BaseModel](
        self, stage: str, instructions: str, prompt: str, schema: type[T]
    ) -> StageResult[T]:
        self._budget.check()
        model = self._model_for(stage)
        prompts = [prompt]
        last_error = ""
        attempts = 0

        for attempt in range(1, settings.llm_max_attempts + 1):
            attempts = attempt
            try:
                response = self._client.responses.parse(
                    model=model,
                    instructions=instructions,
                    input=prompts[-1],
                    text_format=schema,
                    timeout=settings.llm_timeout_seconds,
                )
            except TRANSIENT as exc:
                last_error = type(exc).__name__
                if attempt == settings.llm_max_attempts:
                    break
                self._backoff(attempt, stage, last_error)
                continue

            usage = Usage(
                input_tokens=response.usage.input_tokens if response.usage else 0,
                output_tokens=response.usage.output_tokens if response.usage else 0,
            )
            self._budget.record(usage)

            parsed = response.output_parsed
            if parsed is None:
                last_error = "the model returned no parsable output"
            else:
                try:
                    output = schema.model_validate(parsed)
                except ValidationError as exc:
                    last_error = _first_error(exc)
                else:
                    log.info("llm.stage.ok", stage=stage, model=model,
                             attempts=attempt, tokens=usage.total_tokens)
                    return StageResult(output=output, usage=usage, model=model,
                                       stage=stage, attempts=attempt)

            # One re-ask, with the schema error appended `[EH-02]`.
            if len(prompts) > 1:
                break
            prompts.append(
                f"{prompt}\n\nYour previous reply did not match the required "
                f"schema: {last_error}. Reply again, matching it exactly."
            )
            self._budget.check()

        log.warning("llm.stage.failed", stage=stage, model=model,
                    attempts=attempts, error=last_error)
        raise StageFailed(stage, attempts, last_error)

    @staticmethod
    def _backoff(attempt: int, stage: str, error: str) -> None:
        # Jitter, so a burst of stages that all hit a 429 do not retry in step.
        delay = min(2 ** (attempt - 1), 8) * (0.5 + random.random())
        log.info("llm.retry", stage=stage, attempt=attempt, error=error,
                 delay_seconds=round(delay, 2))
        time.sleep(delay)


def _first_error(exc: ValidationError) -> str:
    """One field and one problem. The full error would echo our schema back into
    a prompt, which is both noisy and more than the model needs."""
    error = exc.errors()[0]
    field = ".".join(str(part) for part in error["loc"])
    return f"{field}: {error['msg']}"
