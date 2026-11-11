"""API behaviour, including the honest-failure cases."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.api import health, jobs, sources
from app.config import get_settings
from app.main import app
from app.seeds import load_seeds, read_seed


@pytest.fixture
def client(migrated_engine, seeds_dir, monkeypatch):
    load_seeds(migrated_engine, seeds_dir)
    for module in (health, jobs, sources):
        monkeypatch.setattr(module, "get_engine", lambda: migrated_engine)
    return TestClient(app)


def test_health_reports_the_schema_revision(client):
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["database"]["schema_revision"] == "0003"


def test_health_is_503_with_the_real_error_when_the_database_is_down(client, monkeypatch):
    dead = create_engine(get_settings().database_url("no_such_database"))
    monkeypatch.setattr(health, "get_engine", lambda: dead)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["database"]["ok"] is False
    assert "no_such_database" in response.json()["database"]["error"]


def test_sources_lists_every_seeded_source_without_any_key(client, seeds_dir):
    rows = client.get("/api/sources").json()
    seeded = read_seed(seeds_dir, "sources")
    assert {row["id"] for row in rows} == {row["id"] for row in seeded}
    assert all("credentials_ref" not in row for row in rows)
    stooq = next(row for row in rows if row["id"] == "stooq")
    assert stooq["status"] == "dropped"


def test_jobs_reject_a_missing_or_wrong_secret(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "n8n_trigger_secret", "right-secret")
    assert client.post("/jobs/selfcheck").status_code == 401
    wrong = client.post("/jobs/selfcheck", headers={"X-Trigger-Secret": "wrong"})
    assert wrong.status_code == 401


def test_jobs_refuse_everything_when_no_secret_is_configured(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "n8n_trigger_secret", "")
    response = client.post("/jobs/selfcheck", headers={"X-Trigger-Secret": ""})
    assert response.status_code == 401


def test_selfcheck_job_runs_with_the_secret(client, monkeypatch, seeds_dir):
    monkeypatch.setattr(get_settings(), "n8n_trigger_secret", "right-secret")
    headers = {"X-Trigger-Secret": "right-secret"}
    counts = client.post("/jobs/selfcheck", headers=headers).json()["result"]["row_counts"]
    assert counts["driver_taxonomy"] == 7
    assert counts["tickers"] == len(read_seed(seeds_dir, "tickers"))
    assert client.post("/jobs/nope", headers=headers).status_code == 404
