"""Sending `[FR-45]`, `[FR-49]`-`[FR-55]`, `[EC-09]`, `[EC-10]`, `[EC-27]`.

Two claims carry this file: nothing reaches a customer unless the approved hash
still matches, and the send-time gates can only ever remove recipients.
"""

import pytest
from fastapi.testclient import TestClient

from texting_agent.config import settings
from texting_agent.database import app_db
from texting_agent.database.repositories.campaign_repo import CampaignRepository
from texting_agent.schemas.campaign import CampaignState as S
from texting_agent.services.communication_service import assign_variant
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
def approved(client, wired) -> dict:
    """A campaign signed off and ready to go."""
    script(wired, *full_run("Critical lapsed"))
    campaign = client.post("/campaigns", headers=ops(),
                           json={"account_id": "ACC_A", "goal": "win them back"}).json()
    client.post(f"/campaigns/{campaign['campaign_id']}/approve",
                headers=approver(), json={})
    return campaign


def send(client, campaign_id: str):
    return client.post(f"/campaigns/{campaign_id}/send", headers=ops())


# --- the hash gate ---------------------------------------------------------


def test_an_approved_campaign_sends(client, approved):
    response = send(client, approved["campaign_id"])
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "SENT"
    assert body["sent"] == approved["frozen_audience"]
    assert body["failed"] == 0


def test_a_changed_audience_aborts_the_entire_send(client, approved, repo):
    """FR-45, EC-28. Not a partial send: a send under a changed approval is a
    send nobody authorised."""
    segment_id = repo.list_segments(approved["campaign_id"])[0]["segment_id"]
    repo.freeze_targets(approved["campaign_id"], "ACC_A", segment_id,
                        [("SMUGGLED_IN", False)])
    response = send(client, approved["campaign_id"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "HASH_MISMATCH"
    assert repo.list_sends(approved["campaign_id"]) == []


def test_changed_content_aborts_the_send(client, approved, repo):
    segment_id = repo.list_segments(approved["campaign_id"])[0]["segment_id"]
    repo.add_variant(segment_id, channel="EMAIL", label="Z",
                     body_template="Copy nobody approved")
    assert send(client, approved["campaign_id"]).status_code == 409
    assert repo.list_sends(approved["campaign_id"]) == []


def test_a_failed_hash_check_leaves_the_campaign_failed(client, approved, repo):
    segment_id = repo.list_segments(approved["campaign_id"])[0]["segment_id"]
    repo.freeze_targets(approved["campaign_id"], "ACC_A", segment_id,
                        [("SMUGGLED_IN", False)])
    send(client, approved["campaign_id"])
    row = repo.get("ACC_A", approved["campaign_id"])
    assert row["state"] == "FAILED"
    assert row["failure_code"] == "HASH_MISMATCH"


def test_an_unapproved_campaign_cannot_be_sent(client, wired):
    """FR-41: no dispatch without an approval, by any code path."""
    script(wired, *full_run("Critical lapsed"))
    campaign = client.post("/campaigns", headers=ops(),
                           json={"account_id": "ACC_A", "goal": "g"}).json()
    assert send(client, campaign["campaign_id"]).status_code == 409


def test_another_tenant_cannot_send_a_campaign(client, approved):
    assert send(client, approved["campaign_id"]) is not None
    other_response = client.post(f"/campaigns/{approved['campaign_id']}/send",
                                 headers=other())
    assert other_response.status_code == 404


# --- replay ----------------------------------------------------------------


def test_sending_twice_produces_no_duplicate_messages(client, approved, repo):
    """UNIQUE(campaign, customer, channel) makes a replay a no-op."""
    send(client, approved["campaign_id"])
    first = len(repo.list_sends(approved["campaign_id"]))
    second = send(client, approved["campaign_id"])
    assert second.status_code == 409          # already SENT
    assert len(repo.list_sends(approved["campaign_id"])) == first


# --- the gates -------------------------------------------------------------


def test_a_suppressed_customer_is_skipped_with_a_reason(client, approved, repo):
    """EC-10, EC-27: the frozen list is unchanged and the hash still matches;
    the recipient is skipped at send."""
    target = repo.list_targets(approved["campaign_id"])[0]
    repo.suppress("ACC_A", target["customer_id"], "EMAIL", "UNSUBSCRIBED")
    body = send(client, approved["campaign_id"]).json()
    assert body["skip_reasons"]["SUPPRESSED"] == 1
    assert body["sent"] == approved["frozen_audience"] - 1


def test_a_customer_without_consent_is_skipped(client, wired, repo, monkeypatch):
    """EC-09."""
    import sqlite3
    from tests.test_api_campaigns import seed_agent_db

    path = settings.agent_db_path
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE customer_agent_records SET email_consent = 0 "
                     "WHERE customer_id = 'C000'")
    script(wired, *full_run("Critical lapsed"))
    campaign = client.post("/campaigns", headers=ops(),
                           json={"account_id": "ACC_A", "goal": "g"}).json()
    client.post(f"/campaigns/{campaign['campaign_id']}/approve",
                headers=approver(), json={})
    body = send(client, campaign["campaign_id"]).json()
    assert body["skip_reasons"].get("NO_CONSENT") == 1


def test_the_frequency_cap_is_counted_across_campaigns(client, wired, repo):
    """FR-54. A per-campaign cap would let ten campaigns each send politely and
    the customer receive ten messages."""
    for _ in range(2):
        script(wired, *full_run("Critical lapsed"))
        campaign = client.post("/campaigns", headers=ops(),
                               json={"account_id": "ACC_A", "goal": "g"}).json()
        client.post(f"/campaigns/{campaign['campaign_id']}/approve",
                    headers=approver(), json={})
        send(client, campaign["campaign_id"])

    script(wired, *full_run("Critical lapsed"))
    third = client.post("/campaigns", headers=ops(),
                        json={"account_id": "ACC_A", "goal": "g"}).json()
    client.post(f"/campaigns/{third['campaign_id']}/approve",
                headers=approver(), json={})
    body = send(client, third["campaign_id"]).json()
    assert body["sent"] == 0
    assert body["skip_reasons"]["FREQUENCY_CAP"] == third["frozen_audience"]


def test_gates_can_only_remove_recipients(client, approved, repo):
    """RV-C3: the frozen list is the ceiling. That asymmetry is what lets the
    hash cover the audience without breaking on every unsubscribe."""
    frozen = {row["customer_id"] for row in repo.list_targets(approved["campaign_id"])}
    send(client, approved["campaign_id"])
    reached = {row["customer_id"] for row in repo.list_sends(approved["campaign_id"])}
    assert reached <= frozen


def test_every_recipient_gets_a_terminal_status(client, approved, repo):
    """AC-6: a run where 400 of 900 recipients silently vanished looks exactly
    like a broken one."""
    target = repo.list_targets(approved["campaign_id"])[0]
    repo.suppress("ACC_A", target["customer_id"], "EMAIL", "UNSUBSCRIBED")
    send(client, approved["campaign_id"])
    rows = repo.list_sends(approved["campaign_id"])
    assert len(rows) == approved["frozen_audience"]
    assert all(row["status"] in {"SENT", "FAILED", "SKIPPED"} for row in rows)
    assert all(row["skip_reason"] for row in rows if row["status"] == "SKIPPED")


# --- provider failure ------------------------------------------------------


def test_a_provider_failure_is_recorded_as_failed_never_sent(client, wired,
                                                             repo, monkeypatch):
    """FR-52."""
    monkeypatch.setattr(settings, "provider_failure_rate", 1.0)
    script(wired, *full_run("Critical lapsed"))
    campaign = client.post("/campaigns", headers=ops(),
                           json={"account_id": "ACC_A", "goal": "g"}).json()
    client.post(f"/campaigns/{campaign['campaign_id']}/approve",
                headers=approver(), json={})
    body = send(client, campaign["campaign_id"]).json()
    assert body["sent"] == 0
    assert body["failed"] + body["skipped"] == campaign["frozen_audience"]
    statuses = {row["status"] for row in repo.list_sends(campaign["campaign_id"])}
    assert "SENT" not in statuses


def test_the_circuit_breaker_stops_hammering_a_dead_provider(client, wired,
                                                             repo, monkeypatch):
    """EH-12: continuing to retry a provider that is plainly down turns one
    outage into a longer one."""
    monkeypatch.setattr(settings, "provider_failure_rate", 1.0)
    script(wired, *full_run("Critical lapsed"))
    campaign = client.post("/campaigns", headers=ops(),
                           json={"account_id": "ACC_A", "goal": "g"}).json()
    client.post(f"/campaigns/{campaign['campaign_id']}/approve",
                headers=approver(), json={})
    body = send(client, campaign["campaign_id"]).json()
    assert body["skip_reasons"].get("PROVIDER_UNAVAILABLE", 0) > 0
    assert body["failed"] == 5          # the breaker threshold


# --- variant assignment ----------------------------------------------------


def test_assignment_is_deterministic():
    """FR-53: the same customer gets the same variant on every run, or an A/B
    result means nothing."""
    first = [assign_variant("camp-1", "EMAIL", f"C{i}", 2) for i in range(50)]
    second = [assign_variant("camp-1", "EMAIL", f"C{i}", 2) for i in range(50)]
    assert first == second


def test_channels_are_assigned_independently():
    """RV-C9: with the channel outside the hash, a customer on both channels
    would get label A in both, the experiments would be correlated, and neither
    result would be clean."""
    email = [assign_variant("camp-1", "EMAIL", f"C{i}", 2) for i in range(200)]
    sms = [assign_variant("camp-1", "SMS", f"C{i}", 2) for i in range(200)]
    assert email != sms
    disagreements = sum(1 for e, s in zip(email, sms, strict=True) if e != s)
    assert 60 < disagreements < 140      # roughly independent, not identical


def test_assignment_splits_roughly_evenly():
    assignments = [assign_variant("camp-1", "EMAIL", f"C{i}", 2) for i in range(1000)]
    share = assignments.count(0) / len(assignments)
    assert 0.45 < share < 0.55


def test_a_variant_is_recorded_on_every_sent_row(client, approved, repo):
    send(client, approved["campaign_id"])
    sent = [r for r in repo.list_sends(approved["campaign_id"])
            if r["status"] == "SENT"]
    assert sent
    assert all(row["variant_id"] for row in sent)
    assert len({row["variant_id"] for row in sent}) == 2      # both labels used


# --- the send log ----------------------------------------------------------


def test_the_send_log_carries_no_pii(client, approved):
    response = client.get(f"/campaigns/{approved['campaign_id']}/sends",
                          headers=ops())
    send(client, approved["campaign_id"])
    after = client.get(f"/campaigns/{approved['campaign_id']}/sends", headers=ops())
    for text in (response.text, after.text):
        assert "Name 0" not in text
        assert "c0@example.test" not in text
        assert "+15550000001" not in text


def test_the_send_log_is_scope_checked(client, approved):
    url = f"/campaigns/{approved['campaign_id']}/sends"
    assert client.get(url, headers=ops()).status_code == 200
    assert client.get(url, headers=other()).status_code == 404
