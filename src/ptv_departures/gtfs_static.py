"""Static GTFS loader for Victorian metro trains.

Provides:
- ``StaticGTFS`` — parses a GTFS zip and answers upcoming-departure queries.
- ``download_gtfs_zip`` — async helper that downloads the zip when missing or stale.

Only stdlib is used (zipfile, csv, datetime, dataclasses, pathlib).
No pandas; stop_times rows are streamed and filtered at parse time.
"""
from __future__ import annotations

import csv
import io
import time
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

__all__ = ["ScheduledDeparture", "StaticGTFS", "download_gtfs_zip"]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduledDeparture:
    trip_id: str
    route_id: str
    route_name: str          # routes.route_short_name or route_long_name
    trip_headsign: str       # trips.trip_headsign
    direction_id: int        # 0 or 1
    stop_id: str             # platform stop_id
    scheduled_local: datetime  # naive local datetime


# ---------------------------------------------------------------------------
# GTFS zip helpers
# ---------------------------------------------------------------------------


def _csv_rows(zf: zipfile.ZipFile, prefix: str, filename: str) -> list[dict[str, str]]:
    """Read a CSV from the zip, looking under *prefix* first, then zip root."""
    candidates = [f"{prefix}{filename}", filename] if prefix else [filename]
    for name in candidates:
        if name in zf.namelist():
            with zf.open(name) as fh:
                text = fh.read().decode("utf-8-sig")  # handle optional BOM
                return list(csv.DictReader(io.StringIO(text)))
    raise FileNotFoundError(f"{filename!r} not found in zip (tried: {candidates})")


def _detect_prefix(zf: zipfile.ZipFile) -> str:
    """Return the subdirectory prefix (e.g. '2/') or '' for root-level files."""
    names = set(zf.namelist())
    if "stops.txt" in names:
        return ""
    # Look one level deep for a subdir that contains stops.txt
    for name in names:
        parts = name.split("/")
        if len(parts) == 2 and parts[1] == "stops.txt":
            return parts[0] + "/"
    return ""


def _parse_gtfs_time(time_str: str, base_date: date) -> datetime:
    """Convert a GTFS time string (HH:MM:SS, may be >=24:00) to a naive datetime.

    GTFS allows hours >= 24 for trips that cross midnight; e.g. '25:10:00'
    means 01:10 the next day.
    """
    parts = time_str.strip().split(":")
    h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
    return datetime(base_date.year, base_date.month, base_date.day) + timedelta(
        hours=h, minutes=m, seconds=s
    )


def _build_services_by_date(
    calendar_rows: list[dict[str, str]],
    calendar_dates_rows: list[dict[str, str]],
) -> dict[date, set[str]]:
    """Pre-compute service_ids active on each date within the calendar range.

    Applies calendar_dates exceptions (type 1 = added, type 2 = removed).
    """
    DOW = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    services_by_date: dict[date, set[str]] = {}

    for row in calendar_rows:
        sid = row["service_id"]
        start = date(int(row["start_date"][:4]), int(row["start_date"][4:6]), int(row["start_date"][6:]))
        end = date(int(row["end_date"][:4]), int(row["end_date"][4:6]), int(row["end_date"][6:]))
        d = start
        while d <= end:
            if row[DOW[d.weekday()]] == "1":
                services_by_date.setdefault(d, set()).add(sid)
            d += timedelta(days=1)

    for row in calendar_dates_rows:
        sid = row["service_id"]
        ds = row["date"]
        d = date(int(ds[:4]), int(ds[4:6]), int(ds[6:]))
        exc_type = int(row["exception_type"])
        if exc_type == 1:
            services_by_date.setdefault(d, set()).add(sid)
        elif exc_type == 2 and d in services_by_date:
            services_by_date[d].discard(sid)

    return services_by_date


# ---------------------------------------------------------------------------
# StaticGTFS
# ---------------------------------------------------------------------------


class StaticGTFS:
    """Parsed GTFS schedule supporting upcoming-departure queries."""

    def __init__(
        self,
        *,
        stops: dict[str, dict[str, str]],
        routes: dict[str, dict[str, str]],
        trips: dict[str, dict[str, str]],
        stop_times_by_stop: dict[str, list[dict[str, str]]],
        services_by_date: dict[date, set[str]],
    ) -> None:
        self._stops = stops
        self._routes = routes
        self._trips = trips
        self._stop_times_by_stop = stop_times_by_stop
        self._services_by_date = services_by_date

    @property
    def stops(self) -> dict[str, dict[str, str]]:
        return self._stops

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_zip(cls, zip_path: Path, *, known_stop_ids: set[str]) -> StaticGTFS:
        """Parse *zip_path*; filter stop_times to rows for *known_stop_ids* only.

        Handles both flat GTFS zips and the Victorian zip-of-zips format where
        each transport mode is nested as e.g. ``2/google_transit.zip``.
        """
        with zipfile.ZipFile(zip_path) as outer:
            names = outer.namelist()
            # Flat layout: stops.txt at root OR one subdir deep
            if "stops.txt" in names or any(
                n.endswith("/stops.txt") and n.count("/") == 1 for n in names
            ):
                return cls._parse_zip(outer, known_stop_ids=known_stop_ids)
            # Zip-of-zips: find the metro train inner zip (mode 2)
            inner_zip_name = cls._find_metro_train_inner_zip(outer)
            with outer.open(inner_zip_name) as blob:
                data = blob.read()
        with zipfile.ZipFile(io.BytesIO(data)) as inner:
            return cls._parse_zip(inner, known_stop_ids=known_stop_ids)

    @staticmethod
    def _find_metro_train_inner_zip(outer: zipfile.ZipFile) -> str:
        """Return the name of the metro train inner zip within *outer*.

        Prefers the well-known Victorian GTFS path ``2/google_transit.zip``.
        Falls back to scanning all nested zips for one whose routes.txt contains
        a ``route_type=2`` row.
        """
        if "2/google_transit.zip" in outer.namelist():
            return "2/google_transit.zip"
        for name in outer.namelist():
            if not name.endswith(".zip"):
                continue
            with outer.open(name) as blob, zipfile.ZipFile(io.BytesIO(blob.read())) as inner:
                if "routes.txt" not in inner.namelist():
                    continue
                with inner.open("routes.txt") as rf:
                    reader = csv.DictReader(
                        line.decode("utf-8-sig") for line in rf
                    )
                    for row in reader:
                        if row.get("route_type") == "2":
                            return name
        raise ValueError("No metro train inner zip found in outer zip")

    @classmethod
    def _parse_zip(cls, zf: zipfile.ZipFile, *, known_stop_ids: set[str]) -> StaticGTFS:
        """Parse GTFS files from *zf* (a flat or single-subdir zip)."""
        prefix = _detect_prefix(zf)

        stops_rows = _csv_rows(zf, prefix, "stops.txt")
        routes_rows = _csv_rows(zf, prefix, "routes.txt")
        trips_rows = _csv_rows(zf, prefix, "trips.txt")

        # calendar_dates.txt is optional
        cal_rows = _csv_rows(zf, prefix, "calendar.txt")
        try:
            cal_dates_rows = _csv_rows(zf, prefix, "calendar_dates.txt")
        except FileNotFoundError:
            cal_dates_rows = []

        # Stream stop_times; skip rows whose stop_id is not known.
        stop_times_by_stop: dict[str, list[dict[str, str]]] = {}
        candidates = [f"{prefix}stop_times.txt", "stop_times.txt"]
        st_name = next(n for n in candidates if n in zf.namelist())
        with zf.open(st_name) as fh:
            text = fh.read().decode("utf-8-sig")
            for row in csv.DictReader(io.StringIO(text)):
                sid = row["stop_id"]
                if sid in known_stop_ids:
                    stop_times_by_stop.setdefault(sid, []).append(row)

        stops = {r["stop_id"]: r for r in stops_rows}
        routes = {r["route_id"]: r for r in routes_rows}
        trips = {r["trip_id"]: r for r in trips_rows}
        services_by_date = _build_services_by_date(cal_rows, cal_dates_rows)

        return cls(
            stops=stops,
            routes=routes,
            trips=trips,
            stop_times_by_stop=stop_times_by_stop,
            services_by_date=services_by_date,
        )

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def upcoming_departures(
        self,
        *,
        stop_id: str,
        after_local: datetime,
        max_results: int = 20,
    ) -> list[ScheduledDeparture]:
        """Return scheduled departures from *stop_id* strictly after *after_local*.

        *after_local* must be a naive local datetime.
        Results are in chronological order, capped at *max_results*.
        """
        query_date = after_local.date()
        active_services = self._services_by_date.get(query_date, set())

        st_rows = self._stop_times_by_stop.get(stop_id, [])
        departures: list[ScheduledDeparture] = []

        for row in st_rows:
            trip_id = row["trip_id"]
            trip = self._trips.get(trip_id)
            if trip is None:
                continue
            if trip["service_id"] not in active_services:
                continue

            scheduled_local = _parse_gtfs_time(row["departure_time"], query_date)
            if scheduled_local <= after_local:
                continue

            route_id = trip["route_id"]
            route = self._routes.get(route_id, {})
            route_name = route.get("route_short_name") or route.get("route_long_name", "")

            departures.append(
                ScheduledDeparture(
                    trip_id=trip_id,
                    route_id=route_id,
                    route_name=route_name,
                    trip_headsign=trip.get("trip_headsign", ""),
                    direction_id=int(trip.get("direction_id", 0)),
                    stop_id=stop_id,
                    scheduled_local=scheduled_local,
                )
            )

        departures.sort(key=lambda d: d.scheduled_local)
        return departures[:max_results]

    def get_platform(self, stop_id: str) -> str:
        """Return the platform_code for the stop, or empty string if not set."""
        stop = self._stops.get(stop_id, {})
        return stop.get("platform_code", "") or ""


# ---------------------------------------------------------------------------
# Download helper
# ---------------------------------------------------------------------------


async def download_gtfs_zip(
    url: str,
    dest: Path,
    *,
    max_age_days: int = 7,
    timeout_seconds: float = 60.0,
) -> Path:
    """Download GTFS zip to *dest* if missing or older than *max_age_days*.

    Returns *dest* (always).
    """
    if dest.exists():
        age_seconds = time.time() - dest.stat().st_mtime
        if age_seconds < max_age_days * 86_400:
            return dest  # file is fresh — skip download

    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.get(url)
        response.raise_for_status()
        dest.write_bytes(response.content)

    return dest
