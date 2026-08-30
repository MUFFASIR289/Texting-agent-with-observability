"""FR-12, FR-13, SEC-03: what the model can call, and what it can say.

The surface is the security boundary. A tool that accepts an account, a table, a
column or a free-text query hands back exactly the control the two-database split
was built to take away.
"""

import inspect
import json

import pytest

from texting_agent.agent.tools import (
    MAX_CANDIDATE_LIMIT,
    CandidateFilters,
    CustomerLookup,
    ScopedToolset,
    SegmentQuery,
)

EXPECTED_TOOLS = {
    "get_churn_summary",
    "get_churn_candidates",
    "get_customer_behavior",
    "get_segment_statistics",
}

FORBIDDEN_PARAMETER_NAMES = {
    "account_id", "account", "table", "table_name", "column", "columns",
    "sql", "query", "where", "filter_sql", "order_by",
}


@pytest.fixture
def toolset(agent_conn):
    from texting_agent.database.repositories.customer_repo import CustomerRepository
    return ScopedToolset("ACC_1", CustomerRepository(agent_conn))


def test_exactly_these_tools_are_model_callable():
    """FR-12. `search_web` is deferred to M10 with the Serper integration; until
    it exists it must not appear here."""
    assert set(ScopedToolset.TOOL_PARAMETERS) == EXPECTED_TOOLS
    assert set(ScopedToolset.TOOL_DESCRIPTIONS) == EXPECTED_TOOLS


def test_no_parameter_model_can_name_an_account_a_table_or_sql():
    for model in (CandidateFilters, CustomerLookup, SegmentQuery):
        assert FORBIDDEN_PARAMETER_NAMES & set(model.model_fields) == set(), model


def test_the_generated_json_schemas_expose_no_forbidden_parameter():
    """The definitions are what the model actually reads, so assert on those and
    not only on the Python models."""
    serialised = json.dumps(ScopedToolset.tool_definitions()).lower()
    for name in FORBIDDEN_PARAMETER_NAMES:
        assert f'"{name}"' not in serialised, name


def test_the_generated_definitions_cover_every_tool():
    definitions = ScopedToolset.tool_definitions()
    assert {d["name"] for d in definitions} == EXPECTED_TOOLS
    assert all(d["description"] for d in definitions)


def test_the_account_is_a_constructor_argument_not_a_tool_argument(toolset):
    for name in EXPECTED_TOOLS:
        signature = inspect.signature(getattr(toolset, name))
        assert "account_id" not in signature.parameters, name


def test_an_empty_account_is_rejected_at_construction(agent_conn):
    from texting_agent.database.repositories.customer_repo import CustomerRepository
    with pytest.raises(ValueError):
        ScopedToolset("", CustomerRepository(agent_conn))


def test_the_candidate_limit_has_a_hard_ceiling(toolset):
    """FR-15: whatever the model asks for, it does not get the whole account."""
    assert toolset.call("get_churn_candidates", {"limit": 5000})["error"]["code"] == (
        "INVALID_ARGUMENTS"
    )
    assert CandidateFilters().limit == 20
    with pytest.raises(ValueError):
        CandidateFilters(limit=MAX_CANDIDATE_LIMIT + 1)
    with pytest.raises(ValueError):
        CandidateFilters(limit=0)


def test_an_unknown_tool_name_is_a_structured_error(toolset):
    payload = toolset.call("execute_sql", {"query": "SELECT 1"})
    assert payload == {"error": {"code": "UNKNOWN_TOOL",
                                 "message": "No tool named 'execute_sql' is available."}}


def test_an_invalid_enum_value_is_a_structured_error_not_a_traceback(toolset):
    payload = toolset.call("get_churn_candidates", {"risk_level": "SUPER_CRITICAL"})
    assert payload["error"]["code"] == "INVALID_ARGUMENTS"


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [("get_churn_candidates", {"risk_level": "NOPE"}),
     ("get_customer_behavior", {"customer_id": ""}),
     ("get_customer_behavior", {"customer_id": "does-not-exist"}),
     ("get_segment_statistics", {"predicate": {"risk_levels": ["NOPE"]}}),
     ("nonsense_tool", {})],
)
def test_no_error_payload_describes_our_internals(toolset, tool, arguments):
    """FR-17: no stack trace, no SQL, no file path - all of which describe the
    system to something we do not trust."""
    payload = json.dumps(toolset.call(tool, arguments))
    for leak in ("Traceback", "sqlite3", "SELECT", "customer_agent_records",
                 ".py", "\\\\", "src/texting_agent"):
        assert leak not in payload, (leak, payload)


def test_an_unknown_customer_looks_the_same_as_another_accounts_customer(toolset):
    """Distinguishable replies would turn the lookup into an enumeration oracle."""
    missing = toolset.call("get_customer_behavior", {"customer_id": "no-such-id"})
    other_account = toolset.call("get_customer_behavior", {"customer_id": "C900"})
    assert missing == other_account
    assert missing["error"]["code"] == "NOT_FOUND"
