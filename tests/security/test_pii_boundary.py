"""SEC-06, FR-14, AC-13: no PII value can leave the toolset.

This runs against the real seeded database rather than a fixture, because the
claim being tested is about every name, email and phone in the data, not about
three invented ones. If `data/customer_agent.db` is missing the test fails rather
than skipping - a PII test that quietly does not run is the worst kind.
"""

import json
from pathlib import Path

import pytest

from texting_agent.agent.tools import ScopedToolset
from texting_agent.config import settings
from texting_agent.database import agent_db
from texting_agent.database.repositories.customer_repo import CustomerRepository
from texting_agent.schemas.customer import CustomerFacts

ACCOUNT = "ACC_A"


@pytest.fixture(scope="module")
def seeded_repo() -> CustomerRepository:
    path = Path(settings.agent_db_path)
    if not path.exists():
        pytest.fail(
            f"{path} is missing. Run: uv run python scripts/seed_data.py "
            "- this test must never be skipped."
        )
    return CustomerRepository(agent_db.connect(path))


@pytest.fixture(scope="module")
def toolset(seeded_repo) -> ScopedToolset:
    return ScopedToolset(ACCOUNT, seeded_repo)


@pytest.fixture(scope="module")
def pii_values(seeded_repo) -> set[str]:
    """Every name, email and phone in the account, as the model would see them."""
    values = set()
    for record in seeded_repo.list_for_account(ACCOUNT):
        values.update(v for v in (record.customer_name, record.email, record.phone) if v)
    return values


def test_the_fixture_actually_found_pii(pii_values):
    """Guards against the assertions below passing over an empty set."""
    assert len(pii_values) > 1000


def test_customer_facts_has_no_field_that_could_hold_pii():
    """Absent by construction, not filtered later: there is no assignment to
    remove and no code path that could forget to."""
    forbidden = {"customer_name", "name", "email", "phone", "address",
                 "first_name", "last_name"}
    assert forbidden & set(CustomerFacts.model_fields) == set()


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("get_churn_summary", {}),
        ("get_churn_candidates", {"limit": 50}),
        ("get_churn_candidates", {"risk_level": "CRITICAL", "limit": 50}),
        ("get_segment_statistics", {"predicate": {"risk_levels": ["HIGH", "CRITICAL"]}}),
    ],
)
def test_no_tool_output_contains_a_pii_value(toolset, pii_values, tool, arguments):
    serialised = json.dumps(toolset.call(tool, arguments))
    leaked = [value for value in pii_values if value in serialised]
    assert leaked == []


def test_a_single_customer_lookup_carries_no_pii(toolset, seeded_repo, pii_values):
    record = seeded_repo.list_for_account(ACCOUNT)[0]
    serialised = json.dumps(toolset.call("get_customer_behavior",
                                         {"customer_id": record.customer_id}))
    assert record.customer_id in serialised          # the right customer...
    assert record.customer_name not in serialised    # ...without their identity
    assert record.email not in serialised
    assert record.phone not in serialised
    assert [value for value in pii_values if value in serialised] == []


def test_every_candidate_is_a_customer_facts_not_a_record(toolset):
    result = toolset.get_churn_candidates(limit=5)
    assert all(isinstance(c, CustomerFacts) for c in result.candidates)


def test_reason_evidence_carries_only_numbers_and_channel_names(toolset):
    """Evidence is the factual basis handed to the model, so it is the one place
    a stray free-text field would be easy to miss."""
    allowed_strings = {"EMAIL", "SMS"}
    for candidate in toolset.get_churn_candidates(limit=50).candidates:
        for reason in candidate.reasons:
            for key, value in reason.evidence.items():
                assert isinstance(value, int | float | type(None)) or value in allowed_strings, (
                    f"{reason.code}.{key} = {value!r}"
                )
