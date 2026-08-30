"""SEC-05, AZ-*: account scope is bound by code, never chosen by a caller."""

import inspect

import pytest

from texting_agent.database.repositories import customer_repo
from texting_agent.database.repositories.customer_repo import CustomerRepository


@pytest.fixture
def repo(agent_conn) -> CustomerRepository:
    return CustomerRepository(agent_conn)


def test_listing_returns_only_the_requested_account(repo):
    records = repo.list_for_account("ACC_1")
    assert {r.customer_id for r in records} == {"C001", "C002"}
    assert {r.account_id for r in records} == {"ACC_1"}


def test_a_customer_of_another_account_is_invisible(repo):
    assert repo.get("ACC_1", "C900") is None
    assert repo.get("ACC_2", "C900") is not None


def test_unknown_account_returns_nothing_rather_than_everything(repo):
    assert repo.list_for_account("ACC_DOES_NOT_EXIST") == []
    assert repo.count("ACC_DOES_NOT_EXIST") == 0


@pytest.mark.parametrize("bad", ["", None, 0, ["ACC_1"]])
def test_a_missing_account_id_is_an_error_not_a_wildcard(repo, bad):
    with pytest.raises(ValueError):
        repo.list_for_account(bad)


@pytest.mark.parametrize(
    "injection",
    ["ACC_1' OR '1'='1", "ACC_1'; DROP TABLE customer_agent_records; --", "%", "*"],
)
def test_injection_attempts_are_treated_as_literal_account_ids(repo, injection):
    assert repo.list_for_account(injection) == []
    assert repo.count("ACC_1") == 2  # table still intact


def test_no_public_method_can_be_called_without_an_account(repo):
    """`known_accounts` is the one exception and is administrative only."""
    for name, method in inspect.getmembers(CustomerRepository, inspect.isfunction):
        if name.startswith("_") or name == "known_accounts":
            continue
        assert "account_id" in inspect.signature(method).parameters, name


def test_statements_are_static_strings_fixed_at_import():
    for key, statement in customer_repo._SQL.items():
        assert "{" not in statement and "%" not in statement, key


# --- the same boundary, one layer up: the model-callable toolset -----------


@pytest.fixture
def toolset_factory(agent_conn):
    from texting_agent.agent.tools import ScopedToolset

    def build(account_id: str) -> "ScopedToolset":
        return ScopedToolset(account_id, CustomerRepository(agent_conn))

    return build


def test_a_toolset_sees_only_its_own_account(toolset_factory):
    assert toolset_factory("ACC_1").get_churn_summary().total_customers == 2
    assert toolset_factory("ACC_2").get_churn_summary().total_customers == 1


def test_a_toolset_cannot_look_up_another_accounts_customer(toolset_factory):
    assert toolset_factory("ACC_1").call("get_customer_behavior",
                                         {"customer_id": "C900"})["error"]["code"] == "NOT_FOUND"
    assert "customer_id" in toolset_factory("ACC_2").call("get_customer_behavior",
                                                          {"customer_id": "C900"})


def test_segment_statistics_are_bounded_by_the_account(toolset_factory):
    everything = {"predicate": {}}
    assert toolset_factory("ACC_1").call("get_segment_statistics", everything)["size"] <= 2
    assert toolset_factory("ACC_2").call("get_segment_statistics", everything)["size"] <= 1


def test_an_injection_shaped_customer_id_is_just_a_missing_customer(toolset_factory):
    payload = toolset_factory("ACC_1").call(
        "get_customer_behavior", {"customer_id": "C001' OR '1'='1"}
    )
    assert payload["error"]["code"] == "NOT_FOUND"
