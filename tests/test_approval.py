"""Approval `[FR-41]`-`[FR-48a]`, `[SEC-10]`, `[VR-10]`, `[EC-11]`, `[EC-12]`.

The claim under test: an approver signs off on a specific campaign - this copy,
this offer, to these people - and nothing can change any of the three afterwards
without the send noticing.
"""

import pytest
from fastapi.testclient import TestClient

from texting_agent.database import app_db
from texting_agent.database.repositories.campaign_repo import CampaignRepository
from texting_agent.main import app
from texting_agent.orchestrator.approval import content_hash
from texting_agent.schemas.campaign import CampaignState as S
from tests.test_api_campaigns import (  # noqa: F401  (fixtures)
    APPROVER_SECRET,
    OTHER_APPROVER_SECRET,
    ANALYSIS,
    SEGMENTS,
    client,
    full_run,
    ops,
    other,
    plan_for,
    script,
    variants_for,
    wired,
)


def approver() -> dict:
    return {"X-API-Key": APPROVER_SECRET}


def foreign_approver() -> dict:
    return {"X-API-Key": OTHER_APPROVER_SECRET}


@pytest.fixture
def campaign(client, wired) -> dict:
    script(wired, *full_run("Critical lapsed"))
    return client.post("/campaigns", headers=ops(),
                       json={"account_id": "ACC_A", "goal": "win them back"}).json()


@pytest.fixture
def repo():
    from texting_agent.config import settings
    return CampaignRepository(app_db.connect(settings.app_db_path))


# --- the hash --------------------------------------------------------------


def test_a_campaign_awaiting_approval_carries_a_hash(campaign):
    assert campaign["state"] == "AWAITING_APPROVAL"
    assert len(campaign["content_hash"]) == 64


def test_the_hash_is_stable_across_recomputation(campaign, repo):
    """It has to be, or the send-time check would fail every campaign."""
    recomputed = content_hash(repo, campaign["campaign_id"])
    assert recomputed == campaign["content_hash"]
    assert content_hash(repo, campaign["campaign_id"]) == recomputed


def test_the_audience_is_inside_the_hash(campaign, repo):
    """EC-28, RV-C-audience. Hashing only the content would let the audience be
    re-scored between approval and send, and the campaign an approver saw would
    not be the one that went out."""
    before = content_hash(repo, campaign["campaign_id"])
    segment_id = repo.list_segments(campaign["campaign_id"])[0]["segment_id"]
    repo.freeze_targets(campaign["campaign_id"], "ACC_A", segment_id,
                        [("SMUGGLED_IN", False)])
    assert content_hash(repo, campaign["campaign_id"]) != before


def test_the_content_is_inside_the_hash(campaign, repo):
    before = content_hash(repo, campaign["campaign_id"])
    segment_id = repo.list_segments(campaign["campaign_id"])[0]["segment_id"]
    repo.add_variant(segment_id, channel="EMAIL", label="C",
                     body_template="A variant nobody approved")
    assert content_hash(repo, campaign["campaign_id"]) != before


def test_the_offer_is_inside_the_hash(campaign, repo):
    before = content_hash(repo, campaign["campaign_id"])
    segment_id = repo.list_segments(campaign["campaign_id"])[0]["segment_id"]
    repo.set_plan(segment_id, playbook_id="DORMANT",
                  offer={"type": "PERCENTAGE_DISCOUNT", "value": 90},
                  channels=["EMAIL"], rationale="unchanged")
    assert content_hash(repo, campaign["campaign_id"]) != before


def test_the_frozen_audience_matches_the_targeted_customers(campaign, repo):
    assert repo.count_targets(campaign["campaign_id"]) == campaign["frozen_audience"]
    assert campaign["frozen_audience"] == campaign["targetable_customers"]


# --- approving -------------------------------------------------------------


def test_an_approver_can_approve(client, campaign):
    response = client.post(f"/campaigns/{campaign['campaign_id']}/approve",
                           headers=approver(), json={"note": "looks right"})
    assert response.status_code == 200
    assert response.json()["state"] == "APPROVED"
    assert response.json()["approved_by"] == "appr-1"


def test_an_operator_cannot_approve(client, campaign):
    """FR-46, AZ-04: the person who built the campaign does not sign it off."""
    response = client.post(f"/campaigns/{campaign['campaign_id']}/approve",
                           headers=ops(), json={})
    assert response.status_code == 403


def test_the_approval_records_who_when_and_over_which_hash(client, campaign, repo):
    client.post(f"/campaigns/{campaign['campaign_id']}/approve",
                headers=approver(), json={"note": "fine"})
    decision = repo.list_decisions(campaign["campaign_id"])[0]
    assert decision["decision"] == "APPROVED"
    assert decision["approver_id"] == "appr-1"
    assert decision["content_hash"] == campaign["content_hash"]
    assert decision["decided_at"]


def test_approving_twice_is_a_409_with_no_second_approval(client, campaign, repo):
    """FR-44."""
    first = client.post(f"/campaigns/{campaign['campaign_id']}/approve",
                        headers=approver(), json={})
    second = client.post(f"/campaigns/{campaign['campaign_id']}/approve",
                         headers=approver(), json={})
    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "INVALID_STATE"
    assert len(repo.list_decisions(campaign["campaign_id"])) == 1


def test_only_one_of_two_concurrent_approvals_wins(campaign, repo):
    """EC-12: guarded by a conditional UPDATE, so two approvers pressing the
    button together cannot both approve."""
    campaign_id = campaign["campaign_id"]
    first = repo.try_transition(campaign_id, S.AWAITING_APPROVAL, S.APPROVED)
    second = repo.try_transition(campaign_id, S.AWAITING_APPROVAL, S.APPROVED)
    assert [first, second] == [True, False]


def test_a_campaign_not_awaiting_approval_cannot_be_approved(client, campaign, repo):
    repo.try_transition(campaign["campaign_id"], S.AWAITING_APPROVAL, S.CANCELLED)
    response = client.post(f"/campaigns/{campaign['campaign_id']}/approve",
                           headers=approver(), json={})
    assert response.status_code == 409
    assert "CANCELLED" in response.json()["error"]["message"]


def test_another_tenants_approver_gets_404_not_403(client, campaign):
    """A 403 here would confirm the campaign exists. The caller has the approver
    role, so the refusal has to come from the lookup, not the role gate."""
    response = client.post(f"/campaigns/{campaign['campaign_id']}/approve",
                           headers=foreign_approver(), json={})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_the_role_gate_runs_before_the_lookup(client, campaign):
    """An operator is refused for being an operator, whichever campaign it is,
    so a key that could never approve learns nothing about what exists."""
    real = client.post(f"/campaigns/{campaign['campaign_id']}/approve",
                       headers=other(), json={})
    invented = client.post("/campaigns/does-not-exist/approve",
                           headers=other(), json={})
    assert real.status_code == invented.status_code == 403


# --- rejecting and cancelling ---------------------------------------------


def test_a_rejection_records_its_reason(client, campaign, repo):
    response = client.post(f"/campaigns/{campaign['campaign_id']}/reject",
                           headers=approver(),
                           json={"reason": "25% is too generous here"})
    assert response.status_code == 200
    assert response.json()["state"] == "REJECTED"
    assert repo.list_decisions(campaign["campaign_id"])[0]["reason"] == (
        "25% is too generous here")


def test_a_rejection_needs_a_reason(client, campaign):
    """A rejection with no reason tells the next run nothing."""
    assert client.post(f"/campaigns/{campaign['campaign_id']}/reject",
                       headers=approver(), json={}).status_code == 422


def test_rejected_is_terminal(client, campaign):
    client.post(f"/campaigns/{campaign['campaign_id']}/reject",
                headers=approver(), json={"reason": "no"})
    assert client.post(f"/campaigns/{campaign['campaign_id']}/approve",
                       headers=approver(), json={}).status_code == 409


def test_an_operator_can_cancel(client, campaign):
    """FR-48: cancelling is not a decision about content, so it needs no
    approver."""
    response = client.post(f"/campaigns/{campaign['campaign_id']}/cancel",
                           headers=ops())
    assert response.status_code == 200
    assert response.json()["state"] == "CANCELLED"


def test_cancellation_beats_a_later_approval(client, campaign):
    """EC-11."""
    client.post(f"/campaigns/{campaign['campaign_id']}/cancel", headers=ops())
    assert client.post(f"/campaigns/{campaign['campaign_id']}/approve",
                       headers=approver(), json={}).status_code == 409


def test_a_terminal_campaign_cannot_be_cancelled_again(client, campaign):
    client.post(f"/campaigns/{campaign['campaign_id']}/cancel", headers=ops())
    assert client.post(f"/campaigns/{campaign['campaign_id']}/cancel",
                       headers=ops()).status_code == 409


# --- revising --------------------------------------------------------------


def test_revising_a_rejected_campaign_creates_a_new_one(client, campaign, wired):
    """FR-48a: the original stays terminal, because re-running the same id would
    erase the record of what was rejected and why."""
    client.post(f"/campaigns/{campaign['campaign_id']}/reject",
                headers=approver(), json={"reason": "too generous"})
    script(wired, *full_run("Critical lapsed"))
    revised = client.post(f"/campaigns/{campaign['campaign_id']}/revise",
                          headers=ops())
    assert revised.status_code == 200
    body = revised.json()
    assert body["campaign_id"] != campaign["campaign_id"]
    assert body["state"] == "AWAITING_APPROVAL"

    original = client.get(f"/campaigns/{campaign['campaign_id']}", headers=ops())
    assert original.json()["campaign"]["state"] == "REJECTED"


def test_the_rejection_reason_reaches_the_next_run(client, campaign, wired):
    client.post(f"/campaigns/{campaign['campaign_id']}/reject",
                headers=approver(), json={"reason": "25% is too generous"})
    script(wired, *full_run("Critical lapsed"))
    client.post(f"/campaigns/{campaign['campaign_id']}/revise", headers=ops())
    assert any("25% is too generous" in prompt for prompt in wired.prompts)


def test_the_new_campaign_records_what_it_was_revised_from(client, campaign,
                                                           wired, repo):
    client.post(f"/campaigns/{campaign['campaign_id']}/reject",
                headers=approver(), json={"reason": "no"})
    script(wired, *full_run("Critical lapsed"))
    revised = client.post(f"/campaigns/{campaign['campaign_id']}/revise",
                          headers=ops()).json()
    row = repo.get("ACC_A", revised["campaign_id"])
    assert row["revised_from"] == campaign["campaign_id"]


def test_a_live_campaign_cannot_be_revised(client, campaign):
    """EC-29: only a terminal-failed campaign has anything to revise."""
    response = client.post(f"/campaigns/{campaign['campaign_id']}/revise",
                           headers=ops())
    assert response.status_code == 409
    assert response.json()["error"]["details"][0]["current"] == "AWAITING_APPROVAL"


def test_a_failed_campaign_carries_its_failure_as_feedback(client, wired, repo):
    """A policy failure is the most useful feedback there is: it names the rule."""
    bad_plan = plan_for("Critical lapsed")
    bad_plan.offer.value = 90
    script(wired, ANALYSIS, SEGMENTS, bad_plan, variants_for("Critical lapsed"))
    failed = client.post("/campaigns", headers=ops(),
                         json={"account_id": "ACC_A", "goal": "g"}).json()
    assert failed["failure"]["code"] == "POLICY_VIOLATION"

    script(wired, *full_run("Critical lapsed"))
    client.post(f"/campaigns/{failed['campaign_id']}/revise", headers=ops())
    assert any("OFFER_MAX_DISCOUNT" in prompt for prompt in wired.prompts)


# --- nothing sends without approval ---------------------------------------


def test_the_pipeline_stops_at_awaiting_approval(campaign):
    """FR-41: sending is a separate call, made by a person, after a person has
    read the content. The pipeline never runs past this state on its own."""
    assert campaign["state"] == "AWAITING_APPROVAL"


def test_no_send_route_exists_yet():
    """M8 adds it. Until then there is provably no code path to a customer."""
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/campaigns/{campaign_id}/send" not in paths
