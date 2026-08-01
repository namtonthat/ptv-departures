"""Tests for gtfs_realtime.py — TDD suite.

All tests use synthetic protobuf payloads built with gtfs_realtime_pb2.
No real network requests are made.
"""
from __future__ import annotations

import pytest
import httpx
from httpx import Response, Request

from google.transit import gtfs_realtime_pb2

from ptv_departures.gtfs_realtime import GTFSRealtimeError, TripUpdate, fetch_trip_updates


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FEED_URL = "https://example.test/gtfs/realtime"


def make_feed(entities: list) -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.incrementality = 0  # FULL_DATASET
    feed.header.timestamp = 1748840000
    for e in entities:
        feed.entity.append(e)
    return feed.SerializeToString()


def make_trip_entity(
    entity_id: str,
    trip_id: str,
    stop_updates: list[dict],
    trip_schedule_relationship: int = 0,  # SCHEDULED
) -> gtfs_realtime_pb2.FeedEntity:
    """Build a FeedEntity containing a TripUpdate."""
    entity = gtfs_realtime_pb2.FeedEntity()
    entity.id = entity_id
    tu = entity.trip_update
    tu.trip.trip_id = trip_id
    tu.trip.schedule_relationship = trip_schedule_relationship
    for stu_data in stop_updates:
        stu = tu.stop_time_update.add()
        stu.stop_id = stu_data["stop_id"]
        if "departure_delay" in stu_data:
            stu.departure.delay = stu_data["departure_delay"]
        if "arrival_delay" in stu_data:
            stu.arrival.delay = stu_data["arrival_delay"]
        if "stop_schedule_relationship" in stu_data:
            stu.schedule_relationship = stu_data["stop_schedule_relationship"]
    return entity


def _make_client(payload: bytes, *, status_code: int = 200, api_key: str = "test-key") -> httpx.AsyncClient:
    """Return an AsyncClient backed by a MockTransport serving the given payload."""
    def handler(request: Request) -> Response:
        assert request.headers.get("KeyID") == api_key
        if status_code != 200:
            return Response(status_code, content=b"error", headers={"content-type": "text/plain"})
        return Response(200, content=payload, headers={"content-type": "application/x-protobuf"})

    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_feed_returns_empty_list():
    """A feed with no entities returns an empty list."""
    payload = make_feed([])
    async with _make_client(payload) as client:
        updates = await fetch_trip_updates(api_key="test-key", url=FEED_URL, client=client)
    assert updates == []


@pytest.mark.asyncio
async def test_one_trip_one_stop_update_with_departure_delay():
    """A single trip entity with one stop update returns one TripUpdate."""
    entity = make_trip_entity(
        "e1",
        "trip-001",
        [{"stop_id": "stop-42", "departure_delay": 120}],
    )
    payload = make_feed([entity])
    async with _make_client(payload) as client:
        updates = await fetch_trip_updates(api_key="test-key", url=FEED_URL, client=client)
    assert len(updates) == 1
    assert updates[0] == TripUpdate(
        trip_id="trip-001",
        stop_id="stop-42",
        delay_seconds=120,
        scheduled_relationship="scheduled",
    )


@pytest.mark.asyncio
async def test_prefers_departure_delay_over_arrival_delay():
    """When both departure and arrival delays are present, departure wins."""
    entity = make_trip_entity(
        "e1",
        "trip-002",
        [{"stop_id": "stop-10", "departure_delay": 60, "arrival_delay": 300}],
    )
    payload = make_feed([entity])
    async with _make_client(payload) as client:
        updates = await fetch_trip_updates(api_key="test-key", url=FEED_URL, client=client)
    assert len(updates) == 1
    assert updates[0].delay_seconds == 60


@pytest.mark.asyncio
async def test_falls_back_to_arrival_delay_when_no_departure():
    """When only arrival.delay is set, that value is used."""
    entity = make_trip_entity(
        "e1",
        "trip-003",
        [{"stop_id": "stop-20", "arrival_delay": 180}],
    )
    payload = make_feed([entity])
    async with _make_client(payload) as client:
        updates = await fetch_trip_updates(api_key="test-key", url=FEED_URL, client=client)
    assert len(updates) == 1
    assert updates[0].delay_seconds == 180


@pytest.mark.asyncio
async def test_canceled_trip_marks_all_stop_updates_canceled():
    """Trip-level CANCELED (3) propagates to every stop's scheduled_relationship."""
    # TripDescriptor.CANCELED = 3
    entity = make_trip_entity(
        "e1",
        "trip-004",
        [
            {"stop_id": "stop-1", "departure_delay": 0},
            {"stop_id": "stop-2", "departure_delay": 0},
        ],
        trip_schedule_relationship=3,  # CANCELED
    )
    payload = make_feed([entity])
    async with _make_client(payload) as client:
        updates = await fetch_trip_updates(api_key="test-key", url=FEED_URL, client=client)
    assert len(updates) == 2
    assert all(u.scheduled_relationship == "canceled" for u in updates)


@pytest.mark.asyncio
async def test_skipped_stop_overrides_scheduled_trip_relationship():
    """Stop-level SKIPPED (1) overrides a SCHEDULED trip relationship."""
    entity = make_trip_entity(
        "e1",
        "trip-005",
        [
            {"stop_id": "stop-A", "departure_delay": 0, "stop_schedule_relationship": 1},  # SKIPPED
            {"stop_id": "stop-B", "departure_delay": 0},  # SCHEDULED (default 0)
        ],
        trip_schedule_relationship=0,  # SCHEDULED
    )
    payload = make_feed([entity])
    async with _make_client(payload) as client:
        updates = await fetch_trip_updates(api_key="test-key", url=FEED_URL, client=client)
    assert len(updates) == 2
    by_stop = {u.stop_id: u for u in updates}
    assert by_stop["stop-A"].scheduled_relationship == "skipped"
    assert by_stop["stop-B"].scheduled_relationship == "scheduled"


@pytest.mark.asyncio
async def test_includes_keyid_header_in_request():
    """The KeyID header must be set on the outgoing HTTP request."""
    received_headers: dict = {}
    api_key = "my-secret-key-xyz"

    def handler(request: Request) -> Response:
        received_headers.update(dict(request.headers))
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.header.gtfs_realtime_version = "2.0"
        feed.header.incrementality = 0
        feed.header.timestamp = 1748840000
        return Response(200, content=feed.SerializeToString())

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await fetch_trip_updates(api_key=api_key, url=FEED_URL, client=client)

    assert received_headers.get("keyid") == api_key


@pytest.mark.asyncio
async def test_5xx_raises_gtfs_realtime_error():
    """A 500 response must raise GTFSRealtimeError."""

    def handler(request: Request) -> Response:
        return Response(500, content=b"internal server error")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(GTFSRealtimeError, match="500"):
            await fetch_trip_updates(api_key="test-key", url=FEED_URL, client=client)


@pytest.mark.asyncio
async def test_network_error_raises_gtfs_realtime_error():
    """An httpx.HTTPError during transport must raise GTFSRealtimeError."""

    def handler(request: Request) -> Response:
        raise httpx.ConnectError("connection refused")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(GTFSRealtimeError, match="transport error"):
            await fetch_trip_updates(api_key="test-key", url=FEED_URL, client=client)
