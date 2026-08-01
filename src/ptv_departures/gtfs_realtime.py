"""GTFS-Realtime trip update fetcher and decoder.

Fetches the GTFS-RT Trip Updates protobuf feed from the VIC Open Data portal
and returns a flat list of TripUpdate dataclasses for the combiner.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx
from google.transit import gtfs_realtime_pb2

DEFAULT_URL = (
    "https://api.opendata.transport.vic.gov.au"
    "/opendata/public-transport/gtfs/realtime/v1/metro/trip-updates"
)

# Trip-level ScheduleRelationship enum mapping (TripDescriptor)
_TRIP_REL: dict[int, str] = {
    0: "scheduled",
    1: "added",
    2: "unscheduled",
    3: "canceled",
    5: "scheduled",  # REPLACEMENT — treat as scheduled for our purposes
    6: "scheduled",  # DUPLICATED — treat as scheduled for our purposes
}

# Stop-time-level ScheduleRelationship enum mapping (StopTimeUpdate)
_STOP_REL: dict[int, str] = {
    0: "scheduled",
    1: "skipped",
    2: "no_data",
    3: "unscheduled",
}


@dataclass(frozen=True)
class TripUpdate:
    trip_id: str
    stop_id: str
    delay_seconds: int
    scheduled_relationship: str


class GTFSRealtimeError(Exception):
    """Raised when the GTFS-R fetch or decode fails."""


def _decode(payload: bytes) -> list[TripUpdate]:
    """Parse a raw protobuf payload and return one TripUpdate per (trip, stop)."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(payload)
    out: list[TripUpdate] = []

    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        trip_rel = _TRIP_REL.get(tu.trip.schedule_relationship, "scheduled")

        for stu in tu.stop_time_update:
            # Prefer departure delay, fall back to arrival delay, else 0
            if stu.HasField("departure"):
                delay = stu.departure.delay
            elif stu.HasField("arrival"):
                delay = stu.arrival.delay
            else:
                delay = 0

            # Stop-level relationship overrides trip-level only when explicitly
            # non-zero (protobuf default 0 = SCHEDULED means "not overriding")
            if stu.schedule_relationship != 0:
                relationship = _STOP_REL.get(stu.schedule_relationship, trip_rel)
            else:
                relationship = trip_rel

            # Trip-level CANCELED always wins — it propagates to every stop
            if trip_rel == "canceled":
                relationship = "canceled"

            out.append(TripUpdate(
                trip_id=tu.trip.trip_id,
                stop_id=stu.stop_id,
                delay_seconds=delay,
                scheduled_relationship=relationship,
            ))

    return out


async def fetch_trip_updates(
    *,
    api_key: str,
    url: str = DEFAULT_URL,
    timeout_seconds: float = 8.0,
    client: httpx.AsyncClient | None = None,
) -> list[TripUpdate]:
    """Fetch and parse GTFS-RT trip updates.

    Returns an empty list if the feed contains no entities.
    Raises GTFSRealtimeError on non-200 responses or transport failures.
    """
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_seconds)
    try:
        response = await client.get(url, headers={"KeyID": api_key})
        if response.status_code != 200:
            raise GTFSRealtimeError(
                f"GTFS-R {response.status_code}: {response.text[:200]}"
            )
        return _decode(response.content)
    except httpx.HTTPError as exc:
        raise GTFSRealtimeError(f"GTFS-R transport error: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()
