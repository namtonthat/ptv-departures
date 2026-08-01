"""Stop IDs (per platform — direction is implicit in the GTFS stop_id).

Resolved once from the real Victorian GTFS zip via scripts/discover.py and committed.
"""
from __future__ import annotations

# slug → list of GTFS stop_ids (one per platform, aggregated into a single feed).
# To resolve a new stop, run: python scripts/discover.py search "Ringwood"
STOPS: dict[str, list[str]] = {
    "ringwood": ["12236", "12237"],  # Platform 1 (Belgrave/Lilydale) + Platform 2 (Lilydale) city-bound
}

# Optional per-stop direction filter, if a stop_id serves multiple
# directions (most platforms don't; leave empty for now).
DIRECTION_FILTER: dict[str, int] = {}

# Per-stop keyword filter on `trip_headsign` / `destination`. If a stop has an
# entry here, only departures whose destination contains ANY of the listed
# substrings (case-insensitive) are kept. Use this to filter out terminating
# trains, services going the wrong way, etc.
DESTINATION_FILTER: dict[str, list[str]] = {
    # Ringwood city-bound: keep services that touch the city (Flinders Street
    # or any City Loop variant). Skip terminating arrivals at Ringwood and
    # outbound-only services like "Lilydale" or "Belgrave".
    "ringwood": ["Flinders Street", "City Loop"],
}
