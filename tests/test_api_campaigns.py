"""The HTTP surface `[FR-63]`-`[FR-68]`, `[AZ-05]`, `[EC-01]`, `[EC-02]`.

Offline: the one place a model is constructed is `campaigns.llm_client`, and
these tests replace it. What is under test is scope enforcement, state and
shape - not what a model says.
"""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from texting_agent import deps
from texting_agent.api import campaigns as campaigns_api
from texting_agent.config import ApiKey, Role, settings
from texting_agent.database import agent_db
from texting_agent.main import app
from texting_agent.schemas.agent_io import (
    ChurnAnalysis,
    MessageVariant,
    MessageVariantSet,
    Offer,
    Pattern,
    ProposedSegment,
    RetentionPlan,
    SegmentationResult,
)
from texting_agent.schemas.campaign import (
    Channel,
    OfferType,
    PlaybookId,
    SegmentPredicate,
)
from texting_agent.schemas.churn import ReasonCode, RiskLevel
from tests.stub_llm import StubLLMClient

_real_llm_client = campaigns_api.llm_client

# The workflow uses the real clock, so fixture data has to as well:
# a fixed past date would make every record stale and nothing targetable.
NOW = datetime.now(UTC)
OPS_SECRET = "operator-secret-for-routes"
APPROVER_SECRET = "approver-secret-for-routes"
OTHER_SECRET = "other-tenant-secret-value"
OTHER_APPROVER_SECRET = "other-tenant-approver-secret"

ANALYSIS = ChurnAnalysis(
    headline="Buyers have gone quiet.",
    dominant_patterns=[Pattern(code=ReasonCode.PURCHASE_GAP, share_of_at_risk=0.6,
                               interpretation="Overdue purchases dominate.")],
    cohorts_of_concern=["lapsed buyers"],
    caveats=["churn_score is a heuristic ranking, not a probability"],
)
SEGMENTS = SegmentationResult(segments=[
    ProposedSegment(name="Critical lapsed", priority=1,
                    predicate=SegmentPredicate(risk_levels=[RiskLevel.CRITICAL]),
                    hypothesis="They stopped buying and stopped visiting."),
    ProposedSegment(name="Everyone else", priority=2,
                    predicate=SegmentPredicate(), hypothesis="Catch-all."),
])


def seed_agent_db(path, account_id="ACC_A", count=30):
    agent_db.create(path)
    rows = [
        (account_id, f"C{i:03d}", f"Name {i}", f"c{i}@example.test", "+15550000001",
         (NOW - timedelta(days=600)).isoformat(),
         (NOW - timedelta(days=150)).isoformat(),
         (NOW - timedelta(days=150)).isoformat(),
         (NOW - timedelta(days=400)).isoformat(),
         8, 800.0, 20.0, 2, NOW.isoformat())
        for i in range(count)
    ]
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO customer_agent_records (account_id, customer_id, "
            "customer_name, email, phone, registration_date, last_activity_at, "
            "last_login_at, last_purchase_at, total_orders, total_spend, "
            "purchase_frequency_days, support_issue_count_90d, data_as_of) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Real app, throwaway databases, scripted model."""
    agent_path = tmp_path / "customer_agent.db"
    seed_agent_db(agent_path)
    monkeypatch.setattr(settings, "agent_db_path", str(agent_path))
    monkeypatch.setattr(settings, "app_db_path", str(tmp_path / "app.db"))
    monkeypatch.setattr(settings, "api_keys", [
        ApiKey(key_id="ops-1", secret=OPS_SECRET, role=Role.OPERATOR,
               accounts=["ACC_A"]),
        ApiKey(key_id="appr-1", secret=APPROVER_SECRET, role=Role.APPROVER,
               accounts=["ACC_A"]),
        ApiKey(key_id="other-1", secret=OTHER_SECRET, role=Role.OPERATOR,
               accounts=["ACC_OTHER"]),
        ApiKey(key_id="other-appr-1", secret=OTHER_APPROVER_SECRET,
               role=Role.APPROVER, accounts=["ACC_OTHER"]),
    ])
    deps.reset_rate_limits()

    stub = StubLLMClient()
    monkeypatch.setattr(campaigns_api, "llm_client", lambda _budget: stub)
    return stub


@pytest.fixture
def client(wired):
    with TestClient(app) as test_client:
        yield test_client


def ops() -> dict:
    return {"X-API-Key": OPS_SECRET}


def other() -> dict:
    return {"X-API-Key": OTHER_SECRET}


def plan_for(segment_name: str) -> RetentionPlan:
    return RetentionPlan(
        segment_name=segment_name, playbook_id=PlaybookId.DORMANT,
        offer=Offer(type=OfferType.PERCENTAGE_DISCOUNT, value=10, code="BACK10"),
        channels=[Channel.EMAIL],
        channel_rationale="email open rate 0.31 against no measured SMS response",
    )


def variants_for(segment_name: str) -> MessageVariantSet:
    return MessageVariantSet(segment_name=segment_name, variants=[
        MessageVariant(channel=Channel.EMAIL, label="A",
                       subject_template="{{first_name}}, come back",
                       body_template="We miss you. {{offer_value}}% off.",
                       cta_text="Shop now", cta_url_key="shop_now"),
        MessageVariant(channel=Channel.EMAIL, label="B",
                       subject_template="A little something, {{first_name}}",
                       body_template="Here is {{offer_value}}% off your next order.",
                       cta_text="View offer", cta_url_key="view_offer"),
    ])


def full_run(*segment_names: str) -> list:
    """ANALYZE, SEGMENT, then a PLAN and a GENERATE per surviving segment."""
    queue = [ANALYSIS, SEGMENTS]
    for name in segment_names:
        queue += [plan_for(name), variants_for(name)]
    return queue


def script(stub, *outputs):
    stub.queue = list(outputs)


# --- creating a campaign ---------------------------------------------------


def test_a_campaign_runs_through_to_awaiting_approval(client, wired):
    script(wired, *full_run("Critical lapsed", "Everyone else"))
    body = client.post("/campaigns", headers=ops(),
                       json={"account_id": "ACC_A", "goal": "win them back"}).json()
    assert body["state"] == "AWAITING_APPROVAL"
    assert body["analysis"]["headline"] == "Buyers have gone quiet."
    assert sum(s["size"] for s in body["segments"]) == body["targetable_customers"]
    # Every fixture customer is CRITICAL, so the catch-all segment matches
    # nobody and is dropped: one segment survives, hence four calls.
    assert [s["name"] for s in body["segments"]] == ["Critical lapsed"]
    assert body["dropped_segments"][0]["name"] == "Everyone else"
    assert body["tokens_used"] == 600
    assert body["variant_count"] == 2
    assert body["plans"][0]["playbook_id"] == "DORMANT"
    assert body["violations"] == []
    assert body["frozen_audience"] == body["targetable_customers"]
    assert len(body["content_hash"]) == 64


def test_the_response_reports_what_was_excluded(client, wired):
    """FR-04c, FR-10a: counted on the campaign, not silently dropped."""
    script(wired, *full_run("Critical lapsed", "Everyone else"))
    body = client.post("/campaigns", headers=ops(),
                       json={"account_id": "ACC_A", "goal": "g"}).json()
    assert set(body["excluded"]) == {"unknown_risk", "stale_data"}


def test_an_empty_segment_is_reported_as_dropped(client, wired):
    """EC-06."""
    script(wired, ANALYSIS, SegmentationResult(segments=[
        ProposedSegment(name="VIP only", priority=1,
                        predicate=SegmentPredicate(risk_levels=[RiskLevel.LOW]),
                        hypothesis="none of these exist"),
        ProposedSegment(name="Everyone", priority=2,
                        predicate=SegmentPredicate(), hypothesis="catch-all"),
    ]), plan_for("Everyone"), variants_for("Everyone"))
    body = client.post("/campaigns", headers=ops(),
                       json={"account_id": "ACC_A", "goal": "g"}).json()
    assert [d["name"] for d in body["dropped_segments"]] == ["VIP only"]


def test_no_llm_call_is_made_for_an_account_with_no_customers(client, wired,
                                                              monkeypatch, tmp_path):
    """EC-01: the short-circuit is before the spend, not after it."""
    empty = tmp_path / "empty.db"
    agent_db.create(empty)
    monkeypatch.setattr(settings, "agent_db_path", str(empty))
    monkeypatch.setattr(settings, "api_keys", settings.api_keys)
    response = client.post("/campaigns", headers=ops(),
                           json={"account_id": "ACC_A", "goal": "g"})
    body = response.json()
    assert body["failure"]["code"] == "NO_TARGETABLE_CUSTOMERS"
    assert body["state"] == "FAILED"
    assert wired.calls == []


def test_the_campaign_records_the_key_that_created_it(client, wired):
    """AU-06."""
    script(wired, *full_run("Critical lapsed", "Everyone else"))
    created = client.post("/campaigns", headers=ops(),
                          json={"account_id": "ACC_A", "goal": "g"}).json()
    fetched = client.get(f"/campaigns/{created['campaign_id']}", headers=ops()).json()
    assert fetched["campaign"]["created_by"] == "ops-1"
    assert fetched["campaign"]["prompt_version"] == "v1"


def test_agent_runs_are_persisted_by_the_orchestrator(client, wired):
    """SEC-09: the agent could not have written these itself."""
    script(wired, *full_run("Critical lapsed", "Everyone else"))
    created = client.post("/campaigns", headers=ops(),
                          json={"account_id": "ACC_A", "goal": "g"}).json()
    runs = client.get(f"/campaigns/{created['campaign_id']}", headers=ops()).json()
    assert [r["stage"] for r in runs["agent_runs"]] == [
        "analyze", "segment", "plan", "generate",
    ]
    assert all(r["status"] == "OK" for r in runs["agent_runs"])


# --- scope -----------------------------------------------------------------


def test_account_id_is_required(client, wired):
    """FR-63b: 400, because it is the caller's mistake."""
    response = client.post("/campaigns", headers=ops(), json={"goal": "g"})
    assert response.status_code == 422    # the body did not even parse
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_an_out_of_scope_account_is_403(client, wired):
    """FR-66: a refusal, not a mistake."""
    response = client.post("/campaigns", headers=ops(),
                           json={"account_id": "ACC_OTHER", "goal": "g"})
    assert response.status_code == 403


def test_another_tenants_campaign_is_404_not_403(client, wired):
    """AZ-05: 403 would confirm the id exists."""
    script(wired, *full_run("Critical lapsed", "Everyone else"))
    created = client.post("/campaigns", headers=ops(),
                          json={"account_id": "ACC_A", "goal": "g"}).json()
    response = client.get(f"/campaigns/{created['campaign_id']}", headers=other())
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_a_campaign_that_never_existed_looks_the_same(client, wired):
    invented = client.get("/campaigns/00000000-0000-0000-0000-000000000000",
                          headers=other())
    script(wired, *full_run("Critical lapsed", "Everyone else"))
    created = client.post("/campaigns", headers=ops(),
                          json={"account_id": "ACC_A", "goal": "g"}).json()
    real_but_foreign = client.get(f"/campaigns/{created['campaign_id']}",
                                  headers=other())
    assert invented.json()["error"] | {"correlation_id": None} == \
           real_but_foreign.json()["error"] | {"correlation_id": None}


@pytest.mark.parametrize("path", ["", "/segments", "/customers"])
def test_every_sub_resource_is_scope_checked(client, wired, path):
    """FR-64, FR-68: not one of them returns a filtered result instead."""
    script(wired, *full_run("Critical lapsed", "Everyone else"))
    created = client.post("/campaigns", headers=ops(),
                          json={"account_id": "ACC_A", "goal": "g"}).json()
    url = f"/campaigns/{created['campaign_id']}{path}"
    assert client.get(url, headers=ops()).status_code == 200
    assert client.get(url, headers=other()).status_code == 404


def test_listing_shows_only_the_callers_campaigns(client, wired):
    script(wired, *full_run("Critical lapsed", "Everyone else"))
    client.post("/campaigns", headers=ops(), json={"account_id": "ACC_A", "goal": "g"})
    assert client.get("/campaigns", headers=ops()).json()["count"] == 1
    assert client.get("/campaigns", headers=other()).json()["count"] == 0


def test_no_route_is_reachable_without_a_key(client, wired):
    """AC-15, now against real routes rather than a probe."""
    assert client.get("/campaigns").status_code == 401
    assert client.get("/campaigns/x").status_code == 401
    assert client.post("/campaigns", json={}).status_code == 401
    assert client.post("/agent/query", json={}).status_code == 401


def test_an_approver_key_cannot_create_a_campaign(client, wired):
    response = client.post("/campaigns", headers={"X-API-Key": APPROVER_SECRET},
                           json={"account_id": "ACC_A", "goal": "g"})
    assert response.status_code == 403


# --- the query endpoint ----------------------------------------------------


def test_agent_query_returns_a_grounded_answer(client, wired):
    from texting_agent.agent.texting_agent import _ToolStep
    from texting_agent.schemas.agent_io import AgentAnswer

    script(wired,
           _ToolStep(tool="get_churn_summary", arguments_json="{}"),
           _ToolStep(tool=None),
           AgentAnswer(answer="30 customers, most at critical risk.",
                       grounded_in=["get_churn_summary"]))
    body = client.post("/agent/query", headers=ops(),
                       json={"account_id": "ACC_A",
                             "query": "who is likely to churn?"}).json()
    assert body["tools_called"] == ["get_churn_summary"]
    assert body["truncated"] is False
    assert body["tokens_used"] > 0


def test_agent_query_is_scope_checked(client, wired):
    response = client.post("/agent/query", headers=ops(),
                           json={"account_id": "ACC_OTHER", "query": "anything"})
    assert response.status_code == 403


def test_no_customer_pii_appears_in_any_response(client, wired):
    """RV-C8: ids and behaviour only."""
    script(wired, *full_run("Critical lapsed", "Everyone else"))
    created = client.post("/campaigns", headers=ops(),
                          json={"account_id": "ACC_A", "goal": "g"})
    listed = client.get("/campaigns", headers=ops())
    fetched = client.get(f"/campaigns/{created.json()['campaign_id']}/customers",
                         headers=ops())
    for response in (created, listed, fetched):
        assert "Name 0" not in response.text
        assert "c0@example.test" not in response.text
        assert "+15550000001" not in response.text


def test_a_missing_model_key_names_the_variable_rather_than_500ing(client, wired,
                                                                   monkeypatch):
    """EH-11: a configuration problem should say which setting is missing."""
    # Put the real factory back, so the configuration check actually runs.
    monkeypatch.setattr(campaigns_api, "llm_client", _real_llm_client)
    monkeypatch.setattr(settings, "openai_api_key", "")
    response = client.post("/campaigns", headers=ops(),
                           json={"account_id": "ACC_A", "goal": "g"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LLM_NOT_CONFIGURED"
    assert "OPENAI_API_KEY" in response.json()["error"]["message"]


def test_the_deterministic_routes_work_without_a_model_key(client, wired, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "")
    assert client.get("/campaigns", headers=ops()).status_code == 200
    assert client.get("/health").status_code == 200


# --- messages and previews -------------------------------------------------


def test_messages_come_back_with_a_preview_rendered_for_a_real_customer(client, wired):
    """FR-34: a preview against an invented customer would prove nothing."""
    script(wired, *full_run("Critical lapsed"))
    created = client.post("/campaigns", headers=ops(),
                          json={"account_id": "ACC_A", "goal": "g"}).json()
    body = client.get(f"/campaigns/{created['campaign_id']}/messages",
                      headers=ops()).json()
    assert body["count"] == 2
    for variant in body["variants"]:
        assert variant["preview"]["subject"]
        assert "{{" not in variant["preview"]["body"]
        assert variant["preview"]["cta_url"].startswith("https://")


def test_a_preview_carries_the_unsubscribe_footer(client, wired):
    """FR-32: appended by code, so the operator can see it is really there."""
    script(wired, *full_run("Critical lapsed"))
    created = client.post("/campaigns", headers=ops(),
                          json={"account_id": "ACC_A", "goal": "g"}).json()
    body = client.get(f"/campaigns/{created['campaign_id']}/messages",
                      headers=ops()).json()
    assert all("Unsubscribe:" in v["preview"]["body"] for v in body["variants"])


def test_messages_are_scope_checked(client, wired):
    script(wired, *full_run("Critical lapsed"))
    created = client.post("/campaigns", headers=ops(),
                          json={"account_id": "ACC_A", "goal": "g"}).json()
    url = f"/campaigns/{created['campaign_id']}/messages"
    assert client.get(url, headers=ops()).status_code == 200
    assert client.get(url, headers=other()).status_code == 404


def test_a_template_using_an_unknown_placeholder_fails_the_campaign(client, wired):
    """VR-08, FR-38: failed with a reason, never silently rewritten to fit."""
    bad = MessageVariantSet(segment_name="Critical lapsed", variants=[
        MessageVariant(channel=Channel.EMAIL, label="A",
                       subject_template="Hi", body_template="Your {{account_balance}}"),
        MessageVariant(channel=Channel.EMAIL, label="B",
                       subject_template="Hi", body_template="Come back"),
    ])
    script(wired, ANALYSIS, SEGMENTS, plan_for("Critical lapsed"), bad)
    body = client.post("/campaigns", headers=ops(),
                       json={"account_id": "ACC_A", "goal": "g"}).json()
    assert body["failure"]["code"] == "INVALID_TEMPLATE"
    assert "account_balance" in body["failure"]["detail"]
    assert body["state"] == "FAILED"


def test_no_preview_ever_contains_a_raw_placeholder(client, wired):
    script(wired, *full_run("Critical lapsed"))
    created = client.post("/campaigns", headers=ops(),
                          json={"account_id": "ACC_A", "goal": "g"}).json()
    response = client.get(f"/campaigns/{created['campaign_id']}/messages", headers=ops())
    previews = [v["preview"]["body"] for v in response.json()["variants"]
                if v["preview"]]
    assert previews
    assert all("{{" not in preview for preview in previews)
