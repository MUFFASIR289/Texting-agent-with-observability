"""Task 5.11a: does the configured model actually honour our schemas?

Skipped unless RUN_LIVE_SMOKE=1 and a key is present, so the suite stays offline
and free. This is the one test that costs money, and it exists because the rest
of the suite proves what our code does with a reply - not that gpt-5-nano can
produce one that parses.

    RUN_LIVE_SMOKE=1 uv run pytest tests/test_live_smoke.py -v
"""

import os

import pytest

from texting_agent.agent import instructions
from texting_agent.agent.llm import TokenBudget
from texting_agent.config import settings
from texting_agent.integrations.openai_client import OpenAILLMClient
from texting_agent.schemas.agent_io import ChurnAnalysis, SegmentationResult

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_SMOKE") != "1" or not settings.openai_api_key,
    reason="live smoke test: set RUN_LIVE_SMOKE=1 with OPENAI_API_KEY to run",
)

SUMMARY = """
Account churn summary:
{"total_customers": 5000, "targetable_customers": 4844,
 "counts_by_risk_level": {"CRITICAL": 362, "HIGH": 623, "MEDIUM": 1666,
                          "LOW": 2292, "UNKNOWN": 57},
 "counts_by_value_tier": {"VIP": 231, "HIGH_VALUE": 923, "STANDARD": 2308,
                          "LOW_VALUE": 1538},
 "reason_code_frequency": {"PURCHASE_GAP": 2558, "PURCHASE_DECLINE": 1682,
                           "ENGAGEMENT_DECLINE": 1464, "LOGIN_LAPSE": 1031,
                           "DORMANCY": 929, "CART_ABANDONMENT": 596,
                           "SUPPORT_FRICTION": 259},
 "median_days_since_purchase": 51.0}

Interpret this account's churn picture.
"""


@pytest.fixture
def client():
    return OpenAILLMClient(TokenBudget(20_000))


def test_the_configured_model_satisfies_the_analyze_schema(client):
    result = client.parse("analyze", instructions.ANALYZE, SUMMARY, ChurnAnalysis)
    assert result.output.headline
    assert result.output.dominant_patterns
    assert result.output.caveats
    print(f"\n{result.model}: {result.usage.total_tokens} tokens, "
          f"{result.attempts} attempt(s)")
    print(result.output.model_dump_json(indent=2))


def test_the_configured_model_satisfies_the_nested_segment_schema(client):
    """The nested one. If a stage is going to fight a schema, it is this one."""
    prompt = (
        SUMMARY
        + "\nCampaign goal: win back customers who have stopped buying.\n"
        + "Propose the segments."
    )
    result = client.parse("segment", instructions.SEGMENT, prompt, SegmentationResult)
    assert 1 <= len(result.output.segments) <= 6
    for segment in result.output.segments:
        assert segment.hypothesis
    print(f"\n{result.model}: {result.usage.total_tokens} tokens")
    print(result.output.model_dump_json(indent=2))
