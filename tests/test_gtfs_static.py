"""Tests for gtfs_static.py — TDD suite.

All tests use the committed miniature fixture at tests/fixtures/test_gtfs.zip.
Never load the real production zip in tests.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ptv_departures.gtfs_static import StaticGTFS, download_gtfs_zip

FIXTURE_ZIP = Path(__file__).parent / "fixtures" / "test_gtfs.zip"

# Stop IDs used by the fixture
KNOWN_STOP_IDS = {"RWD-2", "RWD-3"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_fixture() -> StaticGTFS:
    return StaticGTFS.from_zip(FIXTURE_ZIP, known_stop_ids=KNOWN_STOP_IDS)


# ---------------------------------------------------------------------------
# StaticGTFS loading
# ---------------------------------------------------------------------------


def test_from_zip_loads_test_fixture():
    """Basic smoke test: loading the fixture zip does not raise."""
    gtfs = load_fixture()
    assert gtfs is not None


# ---------------------------------------------------------------------------
# upcoming_departures — core behaviour
# ---------------------------------------------------------------------------


def test_upcoming_departures_returns_two_for_ringwood_platform_2_city_bound():
    """At 02:00 on a Wednesday, RWD-2 has T1 (02:10) and T2 (02:25).

    T3 is on RWD-3 so must NOT appear.
    """
    gtfs = load_fixture()
    # 2026-05-21 is a Thursday — service_FULLWEEK runs every day
    after = datetime(2026, 5, 21, 2, 0, 0)
    results = gtfs.upcoming_departures(stop_id="RWD-2", after_local=after)
    assert len(results) == 2
    trip_ids = [r.trip_id for r in results]
    assert "T1" in trip_ids
    assert "T2" in trip_ids
    assert "T3" not in trip_ids


def test_upcoming_departures_resolves_route_and_headsign():
    """First result (T1, 02:10) must carry correct metadata."""
    gtfs = load_fixture()
    after = datetime(2026, 5, 21, 2, 0, 0)
    results = gtfs.upcoming_departures(stop_id="RWD-2", after_local=after)
    first = results[0]
    assert first.trip_id == "T1"
    assert first.route_name == "Belgrave"
    assert first.trip_headsign == "City Loop"
    assert first.direction_id == 0
    assert first.scheduled_local == datetime(2026, 5, 21, 2, 10, 0)


def test_upcoming_departures_after_a_departure_excludes_it():
    """At 02:15, T1 (02:10) is in the past — only T2 (02:25) remains."""
    gtfs = load_fixture()
    after = datetime(2026, 5, 21, 2, 15, 0)
    results = gtfs.upcoming_departures(stop_id="RWD-2", after_local=after)
    assert len(results) == 1
    assert results[0].trip_id == "T2"
    assert results[0].scheduled_local == datetime(2026, 5, 21, 2, 25, 0)


def test_upcoming_departures_respects_max_results():
    """max_results=1 caps output to a single departure."""
    gtfs = load_fixture()
    after = datetime(2026, 5, 21, 2, 0, 0)
    results = gtfs.upcoming_departures(stop_id="RWD-2", after_local=after, max_results=1)
    assert len(results) == 1
    assert results[0].trip_id == "T1"


def test_upcoming_departures_returns_empty_when_no_service():
    """A stop with no matching service_id on that date returns nothing."""
    gtfs = load_fixture()
    after = datetime(2026, 5, 21, 2, 0, 0)
    # "RWD-99" doesn't exist in known_stop_ids so stop_times were never loaded
    results = gtfs.upcoming_departures(stop_id="RWD-99", after_local=after)
    assert results == []


def test_upcoming_departures_orders_chronologically():
    """Results must be sorted by scheduled_local ascending."""
    gtfs = load_fixture()
    after = datetime(2026, 5, 21, 2, 0, 0)
    results = gtfs.upcoming_departures(stop_id="RWD-2", after_local=after)
    times = [r.scheduled_local for r in results]
    assert times == sorted(times)


# ---------------------------------------------------------------------------
# download_gtfs_zip
# ---------------------------------------------------------------------------


def test_from_zip_handles_zip_of_zips_structure():
    """Real Victorian GTFS feed is a zip-of-zips. Loader must recurse."""
    # The shipped test_gtfs.zip is zip-of-zips after this PR; this confirms
    # the loader correctly picks the metro train inner zip.
    static = StaticGTFS.from_zip(FIXTURE_ZIP, known_stop_ids={"RWD-2", "RWD-3"})
    assert len(static.stops) >= 2
    # confirm at least one Ringwood platform was loaded
    assert any("Ringwood" in s.get("stop_name", "") for s in static.stops.values())


@pytest.mark.asyncio
async def test_download_skips_when_file_is_fresh(tmp_path):
    """If the dest file exists and is younger than max_age_days, skip download."""
    dest = tmp_path / "gtfs.zip"
    dest.write_bytes(b"fake")
    # File is brand new — no download needed
    with patch("ptv_departures.gtfs_static.httpx") as mock_httpx:
        result = await download_gtfs_zip("https://example.com/gtfs.zip", dest, max_age_days=7)
    mock_httpx.AsyncClient.assert_not_called()
    assert result == dest


@pytest.mark.asyncio
async def test_download_fetches_when_file_missing(tmp_path):
    """If the dest file does not exist, download it."""
    dest = tmp_path / "gtfs.zip"
    fake_content = b"PK fake zip"

    mock_response = MagicMock()
    mock_response.content = fake_content
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("ptv_departures.gtfs_static.httpx") as mock_httpx:
        mock_httpx.AsyncClient.return_value = mock_client
        result = await download_gtfs_zip("https://example.com/gtfs.zip", dest, max_age_days=7)

    mock_client.get.assert_called_once()
    assert result == dest
    assert dest.read_bytes() == fake_content


@pytest.mark.asyncio
async def test_download_refetches_when_stale(tmp_path):
    """If the dest file is older than max_age_days, re-download it."""
    dest = tmp_path / "gtfs.zip"
    dest.write_bytes(b"old content")
    # Make the file 8 days old
    old_mtime = time.time() - (8 * 24 * 3600)
    import os
    os.utime(dest, (old_mtime, old_mtime))

    fake_content = b"PK new zip"
    mock_response = MagicMock()
    mock_response.content = fake_content
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("ptv_departures.gtfs_static.httpx") as mock_httpx:
        mock_httpx.AsyncClient.return_value = mock_client
        await download_gtfs_zip("https://example.com/gtfs.zip", dest, max_age_days=7)

    mock_client.get.assert_called_once()
    assert dest.read_bytes() == fake_content
