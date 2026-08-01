"""Combiner: joins static GTFS schedule with GTFS-RT trip updates.

Produces the Terminus JSON contract consumed by the Liquid template.
"""
from __future__ import annotations

import zoneinfo
from datetime import datetime, timedelta
from typing import Any

from .gtfs_realtime import TripUpdate
from .gtfs_static import StaticGTFS

__all__ = ["build_departures_payload"]

MELBOURNE = zoneinfo.ZoneInfo("Australia/Melbourne")


def build_departures_payload(
    static: StaticGTFS,
    realtime: list[TripUpdate],
    *,
    stop_ids: list[str],
    stop_label: str,
    direction_id: int | None = None,
    destination_contains: list[str] | None = None,
    max_results: int = 20,
    now_utc: datetime,
) -> dict[str, Any]:
    """Build the Terminus-compatible JSON contract.

    Parameters
    ----------
    static:
        Loaded StaticGTFS instance for schedule lookups.
    realtime:
        List of TripUpdate objects from the GTFS-RT feed.
    stop_ids:
        List of GTFS stop_ids to query (platform-level, e.g. ["RWD-2", "RWD-3"]).
        Results from all stop_ids are merged and sorted chronologically.
    stop_label:
        Human-readable stop name for the outer ``stop`` field.
    direction_id:
        0 or 1 — only departures matching this direction are included.
        If None, no direction filter is applied (platform stop_id already
        encodes direction for most Victorian metro stops).
    destination_contains:
        Optional list of substrings (case-insensitive). If provided, only
        departures whose ``trip_headsign`` contains at least one of the
        keywords are kept. Use this to restrict a stop to city-bound services
        only (e.g. ["Flinders Street", "City Loop"]).
    max_results:
        Maximum number of departures to return.
    now_utc:
        Timezone-aware UTC datetime used as the reference time.

    Returns
    -------
    dict
        ``{stop, fetched_at, departures: [...]}`` matching the Terminus contract.
    """
    # Step 1: convert now to local Melbourne time; strip tz for static lookup
    now_local = now_utc.astimezone(MELBOURNE)
    now_local_naive = now_local.replace(tzinfo=None)

    # Step 2: overfetch from all stop_ids (2× headroom for direction filter + merge)
    all_scheduled = []
    for stop_id in stop_ids:
        all_scheduled.extend(
            static.upcoming_departures(
                stop_id=stop_id,
                after_local=now_local_naive,
                max_results=max_results * 2,
            )
        )

    # Sort by scheduled_local across all stop_ids
    all_scheduled.sort(key=lambda s: s.scheduled_local)

    # Step 3: filter by direction (skip if direction_id is None)
    if direction_id is not None:
        filtered = [s for s in all_scheduled if s.direction_id == direction_id]
    else:
        filtered = list(all_scheduled)

    # Step 3b: filter by destination keyword (skip if destination_contains is None)
    if destination_contains:
        keywords = [kw.lower() for kw in destination_contains]
        filtered = [
            s for s in filtered
            if any(kw in (s.trip_headsign or "").lower() for kw in keywords)
        ]

    # Step 4: cap at max_results
    filtered = filtered[:max_results]

    # Step 5: build realtime index keyed by (trip_id, stop_id)
    rt_index: dict[tuple[str, str], TripUpdate] = {
        (tu.trip_id, tu.stop_id): tu for tu in realtime
    }

    # Step 6: resolve platform per departure (may differ across stop_ids)
    platform_cache: dict[str, str | None] = {}

    # Step 7: build departure dicts
    departures: list[dict[str, Any]] = []
    for sched in filtered:
        # Resolve platform per departure (cached per stop_id)
        if sched.stop_id not in platform_cache:
            platform_cache[sched.stop_id] = static.get_platform(sched.stop_id)
        platform = platform_cache[sched.stop_id]

        # Make scheduled_local tz-aware (Melbourne)
        scheduled_aware = sched.scheduled_local.replace(tzinfo=MELBOURNE)

        # Look up realtime data
        tu = rt_index.get((sched.trip_id, sched.stop_id))

        if tu is not None:
            delay_seconds = tu.delay_seconds
            estimated_aware: datetime | None = scheduled_aware + timedelta(seconds=delay_seconds)
            relationship = tu.scheduled_relationship
        else:
            delay_seconds = 0
            estimated_aware = None
            relationship = "scheduled"

        # minutes_until from estimated (or scheduled if no realtime)
        reference_time = estimated_aware if estimated_aware is not None else scheduled_aware
        minutes_until = int((reference_time - now_utc).total_seconds() // 60)

        delay_minutes = round(delay_seconds / 60)

        # Determine status
        if relationship == "canceled":
            status = "canceled"
        elif relationship == "skipped":
            status = "skipped"
        elif tu is None:
            status = "scheduled"
        elif delay_minutes > 1:
            status = "delayed"
        elif delay_minutes < -1:
            status = "early"
        else:
            status = "on_time"

        departures.append({
            "scheduled": scheduled_aware.isoformat(),
            "estimated": estimated_aware.isoformat() if estimated_aware is not None else None,
            "minutes_until": minutes_until,
            "scheduled_hhmm": sched.scheduled_local.strftime("%H:%M"),
            "platform": platform,
            "line": sched.route_name,
            "destination": sched.trip_headsign,
            "status": status,
            "delay_minutes": delay_minutes,
        })

    return {
        "stop": stop_label,
        "fetched_at": now_utc.isoformat(),
        "departures": departures,
    }
