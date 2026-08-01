"""Tests for departures.py — TDD suite.

All tests use the committed miniature fixture at tests/fixtures/test_gtfs.zip.
Synthetic TripUpdate lists are built inline; no network calls needed.

Fixture stop-times recap (all times are naive local, AEST = UTC+10):
  T1 — RWD-2, direction 0, 02:10 local  (scheduled/city-bound)
  T2 — RWD-2, direction 0, 02:25 local  (scheduled/city-bound)
  T3 — RWD-3, direction 1, 02:30 local  (outbound)

now_utc = 2026-05-21T02:00:00Z  →  2026-05-21T12:00:00+10:00 (AEST)
But the fixture uses 02:xx times (i.e. 02:10 local = 16:10 UTC the day before).
So we use now_utc that makes 02:00 local, i.e. 2026-05-20T16:00:00Z.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ptv_departures.gtfs_realtime import TripUpdate
from ptv_departures.gtfs_static import StaticGTFS

FIXTURE_ZIP = Path(__file__).parent / "fixtures" / "test_gtfs.zip"

# now_utc such that local Melbourne time is 2026-05-21 02:00:00 AEST (UTC+10)
# 2026-05-21 02:00 AEST = 2026-05-20 16:00 UTC
NOW_UTC = datetime(2026, 5, 20, 16, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def load_static_fixture():
    """Factory fixture: returns a callable that loads StaticGTFS with given known_stop_ids."""
    def _load(known_stop_ids=None):
        if known_stop_ids is None:
            known_stop_ids = {"RWD-2", "RWD-3"}
        return StaticGTFS.from_zip(FIXTURE_ZIP, known_stop_ids=known_stop_ids)
    return _load


def load_static() -> StaticGTFS:
    return StaticGTFS.from_zip(FIXTURE_ZIP, known_stop_ids={"RWD-2", "RWD-3"})


# ---------------------------------------------------------------------------
# Lazy import — tests must fail with ImportError before implementation
# ---------------------------------------------------------------------------


def _build(*args, **kwargs):
    from ptv_departures.departures import build_departures_payload
    return build_departures_payload(*args, **kwargs)


# ---------------------------------------------------------------------------
# Helper: baseline call with no realtime, city-bound (direction 0) at RWD-2
# ---------------------------------------------------------------------------


def _baseline(static=None, realtime=None, *, stop_ids=None, stop_label="Ringwood",
               direction_id=0, max_results=20, now_utc=NOW_UTC):
    if static is None:
        static = load_static()
    if realtime is None:
        realtime = []
    if stop_ids is None:
        stop_ids = ["RWD-2"]
    return _build(
        static,
        realtime,
        stop_ids=stop_ids,
        stop_label=stop_label,
        direction_id=direction_id,
        max_results=max_results,
        now_utc=now_utc,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_realtime_matches_returns_scheduled_status():
    """Empty realtime list → every departure has status='scheduled', estimated=None."""
    payload = _baseline()
    deps = payload["departures"]
    assert len(deps) == 2  # T1 and T2 both at RWD-2, direction 0
    for dep in deps:
        assert dep["status"] == "scheduled"
        assert dep["estimated"] is None


def test_realtime_match_with_on_time_delay():
    """Delay of 30s → rounds to 0 min (banker's rounding) → status='on_time', estimated populated."""
    realtime = [TripUpdate(trip_id="T1", stop_id="RWD-2", delay_seconds=30,
                           scheduled_relationship="scheduled")]
    payload = _baseline(realtime=realtime)
    deps = payload["departures"]
    t1 = next(d for d in deps if d["scheduled_hhmm"] == "02:10")
    assert t1["status"] == "on_time"
    assert t1["estimated"] is not None
    assert t1["delay_minutes"] == 0  # round(30/60) = round(0.5) = 0 (banker's rounding)


def test_realtime_match_with_delayed_status():
    """Delay of 120s → 2 min → status='delayed'."""
    realtime = [TripUpdate(trip_id="T1", stop_id="RWD-2", delay_seconds=120,
                           scheduled_relationship="scheduled")]
    payload = _baseline(realtime=realtime)
    t1 = next(d for d in payload["departures"] if d["scheduled_hhmm"] == "02:10")
    assert t1["status"] == "delayed"
    assert t1["delay_minutes"] == 2


def test_realtime_match_with_early_status():
    """Delay of -90s → -2 min → status='early'."""
    realtime = [TripUpdate(trip_id="T1", stop_id="RWD-2", delay_seconds=-90,
                           scheduled_relationship="scheduled")]
    payload = _baseline(realtime=realtime)
    t1 = next(d for d in payload["departures"] if d["scheduled_hhmm"] == "02:10")
    assert t1["status"] == "early"
    assert t1["delay_minutes"] == -2  # round(-90/60) = -2


def test_canceled_trip_marked_canceled():
    """relationship='canceled' → status='canceled' regardless of delay."""
    realtime = [TripUpdate(trip_id="T1", stop_id="RWD-2", delay_seconds=0,
                           scheduled_relationship="canceled")]
    payload = _baseline(realtime=realtime)
    t1 = next(d for d in payload["departures"] if d["scheduled_hhmm"] == "02:10")
    assert t1["status"] == "canceled"


def test_skipped_stop_marked_skipped():
    """relationship='skipped' → status='skipped'."""
    realtime = [TripUpdate(trip_id="T1", stop_id="RWD-2", delay_seconds=0,
                           scheduled_relationship="skipped")]
    payload = _baseline(realtime=realtime)
    t1 = next(d for d in payload["departures"] if d["scheduled_hhmm"] == "02:10")
    assert t1["status"] == "skipped"


def test_direction_filter_excludes_outbound():
    """direction_id=0 → only T1, T2 at RWD-2 appear; T3 (direction 1) excluded."""
    static = load_static()
    # Use RWD-2 and RWD-3 together — but filter by direction 0
    payload = _build(
        static,
        [],
        stop_ids=["RWD-2"],
        stop_label="Ringwood",
        direction_id=0,
        now_utc=NOW_UTC,
    )
    trip_ids_via_headsign = [d["destination"] for d in payload["departures"]]
    # T3 headsign is "Belgrave" — it must not appear
    assert "Belgrave" not in trip_ids_via_headsign
    # Only city-bound headsigns expected
    for d in payload["departures"]:
        assert d["destination"] in {"City Loop", "Flinders Street"}


def test_max_results_caps_output():
    """max_results=1 → only one departure in results."""
    payload = _baseline(max_results=1)
    assert len(payload["departures"]) == 1
    assert payload["departures"][0]["scheduled_hhmm"] == "02:10"  # T1 first


def test_platform_resolved_from_stop_id():
    """RWD-2 has platform_code='2' in stops.txt → platform field is '2'."""
    payload = _baseline(stop_ids=["RWD-2"])
    for dep in payload["departures"]:
        assert dep["platform"] == "2"


def test_payload_includes_stop_label_and_fetched_at():
    """Outer dict has stop=stop_label and fetched_at=now_utc.isoformat()."""
    payload = _baseline()
    assert payload["stop"] == "Ringwood"
    assert payload["fetched_at"] == NOW_UTC.isoformat()
    assert "departures" in payload


def test_scheduled_hhmm_formats_as_HHMM():
    """scheduled_hhmm is 'HH:MM' (not 'HH:MM:SS')."""
    payload = _baseline()
    for dep in payload["departures"]:
        hhmm = dep["scheduled_hhmm"]
        assert len(hhmm) == 5, f"Expected HH:MM, got {hhmm!r}"
        assert hhmm[2] == ":"


def test_scheduled_isoformat_is_tz_aware_melbourne():
    """scheduled field is a tz-aware ISO string in Melbourne timezone."""
    payload = _baseline()
    dep = payload["departures"][0]
    sched = dep["scheduled"]
    # Should contain +10:00 or +11:00 (AEST/AEDT)
    assert "+" in sched or "-" in sched, f"Expected tz-aware ISO, got {sched!r}"


def test_estimated_isoformat_is_tz_aware_when_present():
    """estimated field is tz-aware when realtime data present."""
    realtime = [TripUpdate(trip_id="T1", stop_id="RWD-2", delay_seconds=60,
                           scheduled_relationship="scheduled")]
    payload = _baseline(realtime=realtime)
    t1 = next(d for d in payload["departures"] if d["scheduled_hhmm"] == "02:10")
    assert t1["estimated"] is not None
    est = t1["estimated"]
    assert "+" in est or "-" in est, f"Expected tz-aware ISO, got {est!r}"


def test_minutes_until_computed_from_estimated_when_present():
    """minutes_until uses estimated time (scheduled + delay) relative to now_utc."""
    # T1 is at 02:10 local = 2026-05-20 16:10 UTC
    # now_utc = 2026-05-20 16:00 UTC → 10 min without delay
    # With 60s delay → 11 min
    realtime = [TripUpdate(trip_id="T1", stop_id="RWD-2", delay_seconds=60,
                           scheduled_relationship="scheduled")]
    payload = _baseline(realtime=realtime)
    t1 = next(d for d in payload["departures"] if d["scheduled_hhmm"] == "02:10")
    assert t1["minutes_until"] == 11


def test_line_and_destination_populated():
    """line and destination are non-empty for fixture trips."""
    payload = _baseline()
    for dep in payload["departures"]:
        assert dep["line"] != "", f"line should not be empty: {dep}"
        assert dep["destination"] != "", f"destination should not be empty: {dep}"


# ---------------------------------------------------------------------------
# Aggregation tests (multi-stop_id)
# ---------------------------------------------------------------------------


def test_aggregates_across_multiple_stop_ids(load_static_fixture):
    """When stop_ids spans multiple platforms, merge results in chronological order."""
    static = load_static_fixture(known_stop_ids={"RWD-2", "RWD-3"})
    now = datetime(2026, 5, 20, 16, 0, 0, tzinfo=timezone.utc)
    result = _build(
        static, [],
        stop_ids=["RWD-2", "RWD-3"],
        stop_label="Ringwood",
        now_utc=now,
    )
    departures = result["departures"]
    # The fixture has T1 (02:10, RWD-2), T2 (02:25, RWD-2), T3 (02:30, RWD-3)
    times = [d["scheduled_hhmm"] for d in departures]
    assert times[:3] == ["02:10", "02:25", "02:30"]


def test_aggregation_respects_max_results(load_static_fixture):
    """max_results caps the merged output across multiple stop_ids."""
    static = load_static_fixture(known_stop_ids={"RWD-2", "RWD-3"})
    now = datetime(2026, 5, 20, 16, 0, 0, tzinfo=timezone.utc)
    result = _build(
        static, [],
        stop_ids=["RWD-2", "RWD-3"],
        stop_label="Ringwood",
        now_utc=now,
        max_results=2,
    )
    assert len(result["departures"]) == 2


# ---------------------------------------------------------------------------
# Destination filter tests
# ---------------------------------------------------------------------------


def test_destination_filter_keeps_matching_keywords(load_static_fixture):
    """destination_contains=['Flinders'] keeps only trips whose headsign contains it."""
    static = load_static_fixture(known_stop_ids={"RWD-2", "RWD-3"})
    now = datetime(2026, 5, 20, 16, 0, 0, tzinfo=timezone.utc)
    result = _build(
        static, [],
        stop_ids=["RWD-2", "RWD-3"],
        stop_label="Ringwood",
        destination_contains=["Flinders"],
        now_utc=now,
    )
    # Fixture has T2 with headsign "Flinders Street" - it should be the only match
    assert all("flinders" in d["destination"].lower() for d in result["departures"])
    assert len(result["departures"]) >= 1


def test_destination_filter_none_keeps_all(load_static_fixture):
    """destination_contains=None acts as no filter."""
    static = load_static_fixture(known_stop_ids={"RWD-2", "RWD-3"})
    now = datetime(2026, 5, 20, 16, 0, 0, tzinfo=timezone.utc)
    no_filter = _build(
        static, [],
        stop_ids=["RWD-2", "RWD-3"],
        stop_label="Ringwood",
        now_utc=now,
    )
    explicit_none = _build(
        static, [],
        stop_ids=["RWD-2", "RWD-3"],
        stop_label="Ringwood",
        destination_contains=None,
        now_utc=now,
    )
    assert len(no_filter["departures"]) == len(explicit_none["departures"])


def test_destination_filter_case_insensitive(load_static_fixture):
    """Filter matching is case-insensitive."""
    static = load_static_fixture(known_stop_ids={"RWD-2", "RWD-3"})
    now = datetime(2026, 5, 20, 16, 0, 0, tzinfo=timezone.utc)
    upper = _build(
        static, [],
        stop_ids=["RWD-2", "RWD-3"],
        stop_label="Ringwood",
        destination_contains=["FLINDERS"],
        now_utc=now,
    )
    lower = _build(
        static, [],
        stop_ids=["RWD-2", "RWD-3"],
        stop_label="Ringwood",
        destination_contains=["flinders"],
        now_utc=now,
    )
    assert len(upper["departures"]) == len(lower["departures"])
