"""App-state persistence and its account scoping `[SEC-04]`, `[AZ-05]`."""

import pytest

from texting_agent.database import app_db
from texting_agent.database.repositories import campaign_repo
from texting_agent.database.repositories.campaign_repo import CampaignRepository


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "app.db"
    app_db.bootstrap(path)
    return CampaignRepository(app_db.connect(path))


@pytest.fixture
def campaign(repo) -> str:
    return repo.create_campaign(account_id="ACC_A", goal="reactivate",
                                created_by="ops-1", prompt_version="v1",
                                config_version="1")


def test_a_new_campaign_starts_in_received(repo, campaign):
    assert repo.get("ACC_A", campaign)["state"] == "RECEIVED"


def test_a_campaign_is_invisible_to_another_account(repo, campaign):
    """AZ-05: not found, rather than found-and-refused, so the id itself
    confirms nothing."""
    assert repo.get("ACC_B", campaign) is None


def test_listing_is_scoped(repo, campaign):
    repo.create_campaign(account_id="ACC_B", goal="other", created_by="ops-2",
                         prompt_version="v1", config_version="1")
    assert len(repo.list_for_account("ACC_A")) == 1
    assert len(repo.list_for_account("ACC_B")) == 1


@pytest.mark.parametrize("bad", ["", None])
def test_no_read_works_without_an_account(repo, campaign, bad):
    with pytest.raises(ValueError):
        repo.get(bad, campaign)
    with pytest.raises(ValueError):
        repo.list_for_account(bad)


def test_usage_accumulates_across_stages(repo, campaign):
    repo.add_usage(campaign, tokens_in=100, tokens_out=50, model_id="gpt-5-nano")
    repo.add_usage(campaign, tokens_in=200, tokens_out=80, model_id="gpt-5-nano")
    row = repo.get("ACC_A", campaign)
    assert (row["tokens_in"], row["tokens_out"]) == (300, 130)


def test_segments_and_their_frozen_audience_persist(repo, campaign):
    segment_id = repo.add_segment(campaign, name="Lapsed", priority=1,
                                  predicate={"risk_levels": ["HIGH"]},
                                  customer_count=2)
    repo.freeze_targets(campaign, "ACC_A", segment_id,
                        [("C1", True), ("C2", False)])
    assert repo.count_targets(campaign) == 2
    assert [r["customer_id"] for r in repo.list_targets(campaign)] == ["C1", "C2"]
    assert repo.list_segments(campaign)[0]["predicate_json"] == '{"risk_levels": ["HIGH"]}'


def test_a_customer_cannot_be_frozen_into_a_campaign_twice(repo, campaign):
    """The primary key is what stops a second freeze from doubling an audience
    the approver already signed off."""
    import sqlite3
    segment_id = repo.add_segment(campaign, name="S", priority=1, predicate={},
                                  customer_count=1)
    repo.freeze_targets(campaign, "ACC_A", segment_id, [("C1", False)])
    with pytest.raises(sqlite3.IntegrityError):
        repo.freeze_targets(campaign, "ACC_A", segment_id, [("C1", False)])


def test_agent_runs_are_recorded_by_the_caller_not_the_agent(repo, campaign):
    """SEC-09: the agent has no connection to write this with."""
    repo.record_run(campaign, "ACC_A", stage="analyze", model_id="gpt-5-nano",
                    tokens_in=100, tokens_out=50, status="OK")
    runs = repo.list_runs(campaign)
    assert [r["stage"] for r in runs] == ["analyze"]
    assert runs[0]["status"] == "OK"


def test_a_failure_is_recorded_with_its_code(repo, campaign):
    repo.record_failure(campaign, "BUDGET_EXCEEDED", "60000 token cap reached")
    row = repo.get("ACC_A", campaign)
    assert row["failure_code"] == "BUDGET_EXCEEDED"


def test_statements_are_static_strings_fixed_at_import():
    for key, statement in campaign_repo._SQL.items():
        assert "{" not in statement and "%" not in statement, key
