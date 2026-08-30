"""The dev-only event simulator `[FR-55]`, `[FR-40a]`, `[FR-63c]`, `[EC-30]`."""

import pytest

from texting_agent.config import settings
from texting_agent.database import app_db
from texting_agent.database.repositories.campaign_repo import CampaignRepository
from texting_agent.services.event_simulator import simulate
from tests.test_api_campaigns import (  # noqa: F401  (fixtures)
    APPROVER_SECRET,
    client,
    full_run,
    ops,
    other,
    script,
    wired,
)


def approver() -> dict:
    return {"X-API-Key": APPROVER_SECRET}


@pytest.fixture
def repo():
    return CampaignRepository(app_db.connect(settings.app_db_path))


@pytest.fixture
def sent(client, wired) -> dict:
    script(wired, *full_run("Critical lapsed"))
    campaign = client.post("/campaigns", headers=ops(),
                           json={"account_id": "ACC_A", "goal": "g"}).json()
    client.post(f"/campaigns/{campaign['campaign_id']}/approve",
                headers=approver(), json={})
    client.post(f"/campaigns/{campaign['campaign_id']}/send", headers=ops())
    return campaign


def test_events_are_generated_for_sent_messages(client, sent):
    body = client.post(f"/campaigns/{sent['campaign_id']}/simulate-events",
                       headers=ops()).json()
    assert body["total_events"] > 0
    assert "DELIVERED" in body["events"]


def test_the_funnel_narrows(client, sent):
    """A demo showing a 90% conversion rate teaches the operator to distrust the
    numbers, so the rates are plausible and each step is a subset of the last."""
    events = client.post(f"/campaigns/{sent['campaign_id']}/simulate-events",
                         headers=ops()).json()["events"]
    assert events["DELIVERED"] >= events.get("OPENED", 0)
    assert events.get("OPENED", 0) >= events.get("CLICKED", 0)
    assert events.get("CLICKED", 0) >= events.get("CONVERTED", 0)


def test_simulation_is_reproducible(sent, repo):
    """NFR-10: analytics is exercised against numbers that do not move."""
    first = simulate(repo, sent["campaign_id"])
    second = simulate(repo, sent["campaign_id"])
    assert first.events == second.events
    assert first.revenue == second.revenue


def test_only_sent_messages_produce_events(sent, repo):
    simulate(repo, sent["campaign_id"])
    sends = {row["send_id"]: row["status"] for row in repo.list_sends(sent["campaign_id"])}
    for event in repo.list_events(sent["campaign_id"]):
        assert sends[event["send_id"]] == "SENT"


def test_an_unsubscribe_writes_its_suppression_in_the_same_transaction(sent, repo):
    """FR-40a: two statements would leave a window in which we know the customer
    unsubscribed and would still send to them."""
    row = next(r for r in repo.list_sends(sent["campaign_id"]) if r["status"] == "SENT")
    assert not repo.is_suppressed("ACC_A", row["customer_id"], row["channel"])
    repo.record_event(row["send_id"], "UNSUBSCRIBED")
    assert repo.is_suppressed("ACC_A", row["customer_id"], row["channel"])


def test_a_bounce_suppresses_too(sent, repo):
    row = next(r for r in repo.list_sends(sent["campaign_id"]) if r["status"] == "SENT")
    repo.record_event(row["send_id"], "BOUNCED")
    assert repo.is_suppressed("ACC_A", row["customer_id"], row["channel"])


def test_an_open_does_not_suppress(sent, repo):
    row = next(r for r in repo.list_sends(sent["campaign_id"]) if r["status"] == "SENT")
    repo.record_event(row["send_id"], "OPENED")
    assert not repo.is_suppressed("ACC_A", row["customer_id"], row["channel"])


def test_a_simulated_unsubscribe_suppresses_the_customer(sent, repo):
    """The simulated world obeys the same rules as the real one, which is the
    only thing that makes the demo loop worth watching.

    The rate is forced here rather than left at 2%: over 30 sends the default
    produces zero unsubscribes about half the time, and a test that only
    sometimes exercises its subject is not a test.
    """
    simulate(repo, sent["campaign_id"], rates={"UNSUBSCRIBED": 1.0})
    unsubscribed = {row["customer_id"] for row in repo.list_events(sent["campaign_id"])
                    if row["event_type"] in ("UNSUBSCRIBED", "BOUNCED")}
    assert unsubscribed
    for customer_id in unsubscribed:
        assert repo.is_suppressed("ACC_A", customer_id, "EMAIL")


def test_the_default_rates_stay_plausible():
    """A demo showing a 40% conversion rate teaches the operator to distrust
    every number on the page."""
    from texting_agent.services.event_simulator import DEFAULT_RATES

    assert DEFAULT_RATES["CONVERTED"] < 0.25
    assert DEFAULT_RATES["UNSUBSCRIBED"] < 0.05
    assert DEFAULT_RATES["DELIVERED"] > 0.9


def test_the_route_is_404_outside_dev(client, sent, monkeypatch):
    """EC-30: as though it did not exist. A 403 would advertise that a way to
    fabricate engagement data is one configuration flag away."""
    monkeypatch.setattr(settings, "env", "prod")
    response = client.post(f"/campaigns/{sent['campaign_id']}/simulate-events",
                           headers=ops())
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_the_route_is_scope_checked(client, sent):
    assert client.post(f"/campaigns/{sent['campaign_id']}/simulate-events",
                       headers=other()).status_code == 404
