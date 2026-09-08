from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["paper_mode"] is True
    assert body["tz"] == "America/New_York"


def test_plugins_endpoint_matches_registry():
    r = client.get("/plugins")
    assert r.status_code == 200
    body = r.json()
    assert len(body["detectors"]) == 23
    assert len(body["exits"]) == 9
    assert body["total"] == 32


def test_plugin_detail_404():
    assert client.get("/plugins/does_not_exist").status_code == 404
    assert client.get("/plugins/synthetic_premarket_stop").status_code == 200


def test_seed_endpoints():
    assert len(client.get("/seed/strategies").json()) == 8
    assert {f["code"] for f in client.get("/seed/frameworks").json()} == {"FA", "FB"}
    assert client.get("/seed/settings").json()["kill_switch"]["value"] is False
