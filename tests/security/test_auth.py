"""AU-01..AU-06, AZ-01..AZ-06, SEC-13: who may call, as what, for which account."""

import json

import pytest
from fastapi import Depends, Request
from fastapi.testclient import TestClient

from texting_agent import deps
from texting_agent.config import ApiKey, Role, settings
from texting_agent.deps import (
    APIError,
    RequestContext,
    get_context,
    require_account,
    require_approver,
    require_operator,
)
from texting_agent.main import app, create_app

OPERATOR_SECRET = "operator-secret-value"
APPROVER_SECRET = "approver-secret-value"

KEYS = [
    ApiKey(key_id="ops-1", secret=OPERATOR_SECRET, role=Role.OPERATOR,
           accounts=["ACC_A", "ACC_B"]),
    ApiKey(key_id="appr-1", secret=APPROVER_SECRET, role=Role.APPROVER,
           accounts=["ACC_A"]),
]


@pytest.fixture(autouse=True)
def configured_keys(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", KEYS)
    deps.reset_rate_limits()


@pytest.fixture
def probe_app():
    """A real app from the same factory, with routes that do nothing but report
    what the dependencies decided."""
    probe = create_app()

    @probe.get("/_probe")
    def _probe(request: Request):
        context = get_context(request)
        return {"key_id": context.key_id, "role": context.role.value,
                "accounts": list(context.accounts)}

    @probe.post("/_operator_only")
    def _operator_only(context: RequestContext = Depends(require_operator)):
        return {"ok": context.key_id}

    @probe.post("/_approver_only")
    def _approver_only(context: RequestContext = Depends(require_approver)):
        return {"ok": context.key_id}

    @probe.post("/_scoped")
    def _scoped(request: Request, body: dict):
        return {"account_id": require_account(get_context(request),
                                              body.get("account_id"))}

    @probe.post("/_limited", dependencies=[Depends(deps.rate_limit)])
    def _limited():
        return {"ok": True}

    return probe


@pytest.fixture
def client(probe_app):
    return TestClient(probe_app)


def operator() -> dict[str, str]:
    return {"X-API-Key": OPERATOR_SECRET}


def approver() -> dict[str, str]:
    return {"X-API-Key": APPROVER_SECRET}


# --- authentication --------------------------------------------------------


def test_a_route_added_later_is_protected_without_opting_in(client):
    """AC-15. The point of the global dependency: the failure mode of per-route
    decorators is a forgotten one, and it fails silently."""
    assert client.get("/_probe").status_code == 401


@pytest.mark.parametrize("key", ["", "wrong-secret", "operator-secret-valu"])
def test_an_absent_or_wrong_key_is_401_with_no_hint_why(client, key):
    """AU-02: the same answer either way, so a wrong key cannot be told from an
    unknown one."""
    response = client.get("/_probe", headers={"X-API-Key": key} if key else {})
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "UNAUTHORIZED"
    assert body["error"]["message"] == "A valid API key is required."


def test_a_valid_key_resolves_to_its_identity_and_scope(client):
    body = client.get("/_probe", headers=operator()).json()
    assert body == {"key_id": "ops-1", "role": "operator",
                    "accounts": ["ACC_A", "ACC_B"]}


def test_health_is_reachable_without_a_key(client):
    assert client.get("/health").status_code == 200


def test_the_real_app_carries_the_same_global_dependency():
    """The probe app proves the dependency works; this proves the shipped app is
    wired to it, which is what protects every route M5 adds."""
    registered = {dependency.dependency for dependency in app.router.dependencies}
    assert deps.authenticate in registered
    with TestClient(app) as real:
        assert real.get("/health").status_code == 200


def test_health_is_the_only_non_public_route_that_needs_no_key():
    """If a route is ever added to PUBLIC_PATHS, this test is where that shows
    up as a deliberate decision rather than a quiet one."""
    assert deps.PUBLIC_PATHS == {"/health", "/docs", "/redoc", "/openapi.json"}


def test_key_comparison_is_constant_time():
    """AU-04: hmac.compare_digest, not ==, so a secret cannot be recovered one
    character at a time."""
    import inspect
    source = inspect.getsource(deps._match_key)
    assert "compare_digest" in source
    assert "return" not in source.split("for key in")[1].split("found = key")[0]


def test_no_response_ever_carries_key_material(client):
    for response in (client.get("/_probe"), client.get("/_probe", headers=operator())):
        assert OPERATOR_SECRET not in response.text
        assert APPROVER_SECRET not in response.text


def test_the_context_is_frozen(client):
    """AZ-01: resolved once, then unmodifiable, so nothing downstream can widen
    scope after the decision was made."""
    context = RequestContext(key_id="k", role=Role.OPERATOR, accounts=("ACC_A",))
    with pytest.raises(Exception):
        context.accounts = ("ACC_A", "ACC_B")


# --- roles -----------------------------------------------------------------


def test_an_operator_key_is_refused_on_approver_routes(client):
    """AZ-04. Separation of duties: the person who builds the campaign is not
    the person who signs it off."""
    response = client.post("/_approver_only", headers=operator())
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_an_approver_key_is_refused_on_operator_routes(client):
    assert client.post("/_operator_only", headers=approver()).status_code == 403


def test_each_role_can_do_its_own_work(client):
    assert client.post("/_operator_only", headers=operator()).json() == {"ok": "ops-1"}
    assert client.post("/_approver_only", headers=approver()).json() == {"ok": "appr-1"}


# --- account scope ---------------------------------------------------------


def test_a_missing_account_is_400_and_an_out_of_scope_one_is_403(client):
    """FR-63b. One is the caller's mistake; the other is a refusal."""
    assert client.post("/_scoped", headers=operator(), json={}).status_code == 400
    out_of_scope = client.post("/_scoped", headers=operator(),
                               json={"account_id": "ACC_Z"})
    assert out_of_scope.status_code == 403


def test_an_in_scope_account_is_accepted(client):
    response = client.post("/_scoped", headers=operator(), json={"account_id": "ACC_B"})
    assert response.json() == {"account_id": "ACC_B"}


def test_scope_comes_from_the_key_not_the_body(client):
    """AZ-06. The approver key carries only ACC_A, so asking for ACC_B in the
    body changes nothing - which is why an injected instruction cannot either."""
    assert client.post("/_scoped", headers=approver(),
                       json={"account_id": "ACC_B"}).status_code == 403
    assert client.post("/_scoped", headers=approver(),
                       json={"account_id": "ACC_A"}).status_code == 200


def test_require_account_is_a_function_not_a_body_field(client):
    with pytest.raises(APIError):
        require_account(RequestContext("k", Role.OPERATOR, ("ACC_A",)), "ACC_B")


# --- rate limiting ---------------------------------------------------------


def test_the_expensive_routes_are_rate_limited(client, monkeypatch):
    """SEC-13: bounds cost and abuse on the routes that call a model."""
    monkeypatch.setattr(settings, "rate_limit_per_minute", 3)
    for _ in range(3):
        assert client.post("/_limited", headers=operator()).status_code == 200
    blocked = client.post("/_limited", headers=operator())
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"


def test_the_limit_is_per_key_not_global(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
    for _ in range(2):
        client.post("/_limited", headers=operator())
    assert client.post("/_limited", headers=operator()).status_code == 429
    assert client.post("/_limited", headers=approver()).status_code == 200


# --- error envelope --------------------------------------------------------


def test_every_error_uses_the_same_envelope(client):
    for response in (client.get("/_probe"),
                     client.post("/_approver_only", headers=operator()),
                     client.get("/nope", headers=operator()),
                     client.post("/_scoped", headers=operator(), json={})):
        body = response.json()
        assert set(body) == {"error"}
        assert set(body["error"]) == {"code", "message", "details", "correlation_id"}


def test_the_correlation_id_in_the_body_matches_the_header(client):
    """One identifier for the caller and the logs, not two."""
    response = client.get("/_probe")
    assert response.json()["error"]["correlation_id"] == response.headers["X-Request-ID"]


def test_a_validation_error_names_the_field_without_dumping_internals(client):
    response = client.post("/_scoped", headers=operator(), content="not json")
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "INVALID_REQUEST"
    assert "Traceback" not in json.dumps(body)


def test_no_key_material_reaches_the_logs(client, capsys):
    """AU-05, SEC-08: a secret in a log line is a secret in every log sink that
    line is shipped to."""
    from texting_agent.observability.logging import configure_logging

    configure_logging("INFO")
    client.get("/_probe", headers=operator())
    client.get("/_probe", headers={"X-API-Key": "brute-force-attempt"})
    written = capsys.readouterr()
    combined = written.out + written.err
    assert OPERATOR_SECRET not in combined
    assert APPROVER_SECRET not in combined
    assert "brute-force-attempt" not in combined


def test_every_route_is_accounted_for():
    """A route inventory, asserted rather than assumed.

    `app.routes` does not enumerate included routers in this Starlette version,
    so an earlier version of this check passed vacuously. The OpenAPI spec is
    the one view that sees everything, which makes it the one worth asserting
    on: a route added without a test lands here first.
    """
    paths = set(app.openapi()["paths"])
    assert paths == {
        "/health",
        "/campaigns",
        "/campaigns/{campaign_id}",
        "/campaigns/{campaign_id}/segments",
        "/campaigns/{campaign_id}/customers",
        "/campaigns/{campaign_id}/messages",
        "/campaigns/{campaign_id}/approve",
        "/campaigns/{campaign_id}/reject",
        "/campaigns/{campaign_id}/cancel",
        "/campaigns/{campaign_id}/revise",
        "/campaigns/{campaign_id}/send",
        "/campaigns/{campaign_id}/sends",
        "/campaigns/{campaign_id}/simulate-events",
        "/agent/query",
    }


def test_no_route_but_health_is_public():
    """Every path above except /health sits behind the global dependency."""
    with TestClient(app) as unauthenticated:
        for path in app.openapi()["paths"]:
            if path == "/health":
                continue
            url = path.replace("{campaign_id}", "some-id")
            for method in app.openapi()["paths"][path]:
                response = getattr(unauthenticated, method)(url)
                assert response.status_code == 401, (method, path)
