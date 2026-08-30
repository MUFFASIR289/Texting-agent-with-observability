"""LLM client behaviour `[EH-01]`, `[EH-02]`, `[EH-03]`, `[FR-27]`.

Every test here runs offline against a fake `responses.parse`. The one test that
would need a real key is the live smoke test, which is skipped without one.
"""

import httpx2
import openai
import pytest
from pydantic import BaseModel

from texting_agent.agent.llm import BudgetExceeded, StageFailed, TokenBudget, Usage
from texting_agent.config import settings
from texting_agent.integrations.openai_client import OpenAILLMClient


class Answer(BaseModel):
    verdict: str
    confidence: float


class FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeResponse:
    def __init__(self, parsed, input_tokens=100, output_tokens=50) -> None:
        self.output_parsed = parsed
        self.usage = FakeUsage(input_tokens, output_tokens)


class FakeResponses:
    """Replays a scripted sequence: exceptions are raised, values are returned."""

    def __init__(self, script) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        step = self.script.pop(0) if self.script else self.script
        if isinstance(step, Exception):
            raise step
        return step


class FakeOpenAI:
    def __init__(self, script) -> None:
        self.responses = FakeResponses(script)


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    monkeypatch.setattr("texting_agent.integrations.openai_client.time.sleep",
                        lambda _seconds: None)


def build(script, max_tokens=10_000):
    fake = FakeOpenAI(script)
    client = OpenAILLMClient(TokenBudget(max_tokens), client=fake)
    return client, fake


REQUEST = httpx2.Request("POST", "https://api.openai.com/v1/responses")


def status_error(kind, code: int):
    """The SDK's status errors need a real response object to construct."""
    return kind(str(code), response=httpx2.Response(code, request=REQUEST), body=None)


def rate_limited() -> openai.RateLimitError:
    return status_error(openai.RateLimitError, 429)


# --- the happy path --------------------------------------------------------


def test_a_parsed_response_comes_back_with_its_cost():
    client, _ = build([FakeResponse(Answer(verdict="ok", confidence=0.5))])
    result = client.parse("analyze", "instructions", "prompt", Answer)
    assert result.output.verdict == "ok"
    assert result.usage.total_tokens == 150
    assert result.stage == "analyze"
    assert result.attempts == 1


def test_the_stage_chooses_its_own_model(monkeypatch):
    monkeypatch.setattr(settings, "openai_model_generate", "gpt-5-mini")
    client, fake = build([FakeResponse(Answer(verdict="ok", confidence=0.5))])
    client.parse("generate", "i", "p", Answer)
    assert fake.responses.calls[0]["model"] == "gpt-5-mini"


def test_other_stages_keep_the_default_model(monkeypatch):
    monkeypatch.setattr(settings, "openai_model_generate", "gpt-5-mini")
    client, fake = build([FakeResponse(Answer(verdict="ok", confidence=0.5))])
    client.parse("analyze", "i", "p", Answer)
    assert fake.responses.calls[0]["model"] == "gpt-5-nano"


# --- transient failures ----------------------------------------------------


def test_a_rate_limit_is_retried_then_succeeds():
    client, fake = build([rate_limited(), rate_limited(),
                          FakeResponse(Answer(verdict="ok", confidence=1.0))])
    result = client.parse("analyze", "i", "p", Answer)
    assert result.attempts == 3
    assert len(fake.responses.calls) == 3


def test_retries_stop_at_the_configured_limit():
    client, fake = build([rate_limited()] * 5)
    with pytest.raises(StageFailed) as raised:
        client.parse("analyze", "i", "p", Answer)
    assert len(fake.responses.calls) == settings.llm_max_attempts
    assert raised.value.stage == "analyze"
    assert raised.value.attempts == settings.llm_max_attempts


def test_a_bad_request_is_not_retried():
    """A 400 is a bug in our request; retrying it three times makes the same
    mistake more slowly."""
    client, fake = build([status_error(openai.BadRequestError, 400)])
    with pytest.raises(openai.BadRequestError):
        client.parse("analyze", "i", "p", Answer)
    assert len(fake.responses.calls) == 1


# --- schema failures -------------------------------------------------------


def test_unparsable_output_gets_exactly_one_re_ask():
    """EH-02. A model that cannot satisfy a schema twice will not satisfy it on
    a third attempt either."""
    client, fake = build([FakeResponse(None),
                          FakeResponse(Answer(verdict="ok", confidence=0.2))])
    result = client.parse("segment", "i", "p", Answer)
    assert result.attempts == 2
    assert len(fake.responses.calls) == 2


def test_the_re_ask_carries_the_schema_error_not_the_whole_schema():
    client, fake = build([FakeResponse(None), FakeResponse(None)])
    with pytest.raises(StageFailed):
        client.parse("segment", "i", "p", Answer)
    second_prompt = fake.responses.calls[1]["input"]
    assert "did not match the required schema" in second_prompt
    assert len(fake.responses.calls) == 2


def test_a_second_schema_failure_gives_up():
    client, fake = build([FakeResponse(None), FakeResponse(None), FakeResponse(None)])
    with pytest.raises(StageFailed) as raised:
        client.parse("segment", "i", "p", Answer)
    assert len(fake.responses.calls) == 2
    assert "parsable" in raised.value.last_error


# --- budget ----------------------------------------------------------------


def test_usage_accumulates_across_calls():
    client, _ = build([FakeResponse(Answer(verdict="a", confidence=0.1)),
                       FakeResponse(Answer(verdict="b", confidence=0.1))])
    client.parse("analyze", "i", "p", Answer)
    client.parse("segment", "i", "p", Answer)
    assert client._budget.used.total_tokens == 300


def test_a_stage_cannot_start_a_call_the_budget_cannot_pay_for():
    """EH-03: checked before the call as well as after, so an exhausted budget
    does not buy one more request on the way out."""
    client, fake = build([FakeResponse(Answer(verdict="a", confidence=0.1)),
                          FakeResponse(Answer(verdict="b", confidence=0.1))],
                         max_tokens=150)
    client.parse("analyze", "i", "p", Answer)
    with pytest.raises(BudgetExceeded):
        client.parse("segment", "i", "p", Answer)
    assert len(fake.responses.calls) == 1


def test_the_budget_reports_what_is_left():
    budget = TokenBudget(1000)
    budget.record(Usage(input_tokens=300, output_tokens=200))
    assert budget.remaining == 500
    budget.check()


# --- the request we actually send -----------------------------------------


def test_instructions_and_schema_travel_with_every_call():
    client, fake = build([FakeResponse(Answer(verdict="ok", confidence=0.5))])
    client.parse("analyze", "the instructions", "the prompt", Answer)
    call = fake.responses.calls[0]
    assert call["instructions"] == "the instructions"
    assert call["input"] == "the prompt"
    assert call["text_format"] is Answer
    assert call["timeout"] == settings.llm_timeout_seconds
