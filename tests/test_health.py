from fastapi.testclient import TestClient

from texting_agent.main import app


def test_health_reports_ok(isolated_settings):
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "texting-agent"
    assert body["config_valid"] is True


def test_health_vouches_for_the_database_boundary(isolated_settings):
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["boundary_intact"] is True
    assert body["agent_db"] == {
        "reachable": True,
        "read_only": True,
        "tables": ["customer_agent_records"],
    }
    assert body["app_db"] == {"reachable": True, "writable": True}


def test_health_degrades_rather_than_lying_when_the_agent_db_is_missing(
    isolated_settings, monkeypatch, tmp_path
):
    monkeypatch.setattr(isolated_settings, "agent_db_path", str(tmp_path / "gone.db"))
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert body["boundary_intact"] is False
    assert body["agent_db"]["reachable"] is False


def test_every_response_carries_a_request_id(isolated_settings):
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.headers["X-Request-ID"]
