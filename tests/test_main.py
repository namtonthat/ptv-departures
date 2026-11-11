"""End-to-end FastAPI tests — GTFS-based pipeline."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import ptv_departures.main as main_module
from ptv_departures.gtfs_realtime import GTFSRealtimeError
from ptv_departures.gtfs_static import StaticGTFS
from ptv_departures.main import create_app

FIXTURE_ZIP = Path(__file__).parent / "fixtures" / "test_gtfs.zip"

# now_utc such that local Melbourne time is 2026-05-21 02:00:00 AEST (UTC+10)
# 2026-05-21 02:00 AEST = 2026-05-20 16:00 UTC — both T1 (02:10) and T2 (02:25) are upcoming
FIXED_NOW_UTC = datetime(2026, 5, 20, 16, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def reset_static_state(monkeypatch):
    """Reset the module-level static GTFS state before each test."""
    monkeypatch.setitem(main_module._static_state, "value", None)


@pytest.fixture
def prebuilt_static() -> StaticGTFS:
    return StaticGTFS.from_zip(FIXTURE_ZIP, known_stop_ids={"RWD-2", "RWD-3"})


def test_health_returns_ok():
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_unknown_stop_returns_404(monkeypatch):
    monkeypatch.setattr("ptv_departures.main.STOPS", {})
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/departures/atlantis")
    assert r.status_code == 404
    assert r.json() == {"detail": "unknown_stop"}


def test_missing_creds_returns_500(monkeypatch):
    monkeypatch.delenv("PTV_API_KEY", raising=False)
    monkeypatch.setattr("ptv_departures.main.STOPS", {"ringwood": ["RWD-2"]})
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/departures/ringwood")
    assert r.status_code == 500
    assert r.json()["detail"] == "missing_credentials"


def test_known_stop_returns_payload(monkeypatch, prebuilt_static):
    monkeypatch.setenv("PTV_API_KEY", "test-key")
    monkeypatch.setattr("ptv_departures.main.STOPS", {"ringwood": ["RWD-2"]})
    monkeypatch.setattr("ptv_departures.main._utcnow", lambda: FIXED_NOW_UTC)
    monkeypatch.setitem(main_module._static_state, "value", prebuilt_static)

    async def fake_fetch_trip_updates(**_kwargs):
        return []

    monkeypatch.setattr("ptv_departures.main.fetch_trip_updates", fake_fetch_trip_updates)

    app = create_app()
    with TestClient(app) as c:
        r = c.get("/departures/ringwood")

    assert r.status_code == 200
    body = r.json()
    assert body["stop"] == "Ringwood"
    assert len(body["departures"]) >= 1
    # Both T1 (City Loop, Belgrave line) and T2 (Flinders Street, Lilydale line) are at RWD-2
    lines = {d["line"] for d in body["departures"]}
    assert "Belgrave" in lines or "Lilydale" in lines


def test_realtime_failure_falls_back_to_scheduled(monkeypatch, prebuilt_static):
    monkeypatch.setenv("PTV_API_KEY", "test-key")
    monkeypatch.setattr("ptv_departures.main.STOPS", {"ringwood": ["RWD-2"]})
    monkeypatch.setattr("ptv_departures.main._utcnow", lambda: FIXED_NOW_UTC)
    monkeypatch.setitem(main_module._static_state, "value", prebuilt_static)

    async def bad_fetch(**_kwargs):
        raise GTFSRealtimeError("feed unavailable")

    monkeypatch.setattr("ptv_departures.main.fetch_trip_updates", bad_fetch)

    app = create_app()
    with TestClient(app) as c:
        r = c.get("/departures/ringwood")

    assert r.status_code == 200
    body = r.json()
    assert len(body["departures"]) >= 1
    # Without realtime data, all departures should be "scheduled"
    for dep in body["departures"]:
        assert dep["status"] == "scheduled"
