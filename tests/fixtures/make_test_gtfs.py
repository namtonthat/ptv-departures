#!/usr/bin/env python3
"""Build tests/fixtures/test_gtfs.zip — a miniature Victorian-style GTFS fixture.

Run once to regenerate the committed binary:
    python tests/fixtures/make_test_gtfs.py
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

OUTPUT = Path(__file__).parent / "test_gtfs.zip"

# ---------------------------------------------------------------------------
# CSV content for each GTFS file (files placed at zip root — simple fixture)
# ---------------------------------------------------------------------------

STOPS_CSV = """\
stop_id,stop_name,parent_station,stop_lat,stop_lon,platform_code
RWD,Ringwood Station,,145.2272,-37.8135,
RWD-2,Ringwood Platform 2,RWD,145.2272,-37.8135,2
RWD-3,Ringwood Platform 3,RWD,145.2273,-37.8136,3
"""

ROUTES_CSV = """\
route_id,route_short_name,route_long_name,route_type
R-BEL,Belgrave,Belgrave Line,2
R-LIL,Lilydale,Lilydale Line,2
"""

# Three trips:
#   T1 — R-BEL, city-bound (direction 0), headsign "City Loop"
#   T2 — R-LIL, city-bound (direction 0), headsign "Flinders Street"
#   T3 — R-BEL, outbound  (direction 1), headsign "Belgrave"
TRIPS_CSV = """\
route_id,service_id,trip_id,trip_headsign,direction_id
R-BEL,service_FULLWEEK,T1,City Loop,0
R-LIL,service_FULLWEEK,T2,Flinders Street,0
R-BEL,service_FULLWEEK,T3,Belgrave,1
"""

# All times are within 0-23h (no GTFS overflow needed for the fixture).
# T1 and T2 serve RWD-2 (city-bound platform).
# T3 serves RWD-3 (outbound platform).
STOP_TIMES_CSV = """\
trip_id,arrival_time,departure_time,stop_id,stop_sequence
T1,02:10:00,02:10:00,RWD-2,1
T2,02:25:00,02:25:00,RWD-2,1
T3,02:30:00,02:30:00,RWD-3,1
"""

# service_FULLWEEK: active every day Mon-Sun for a generous date range.
CALENDAR_CSV = """\
service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date
service_FULLWEEK,1,1,1,1,1,1,1,20200101,20991231
"""


def build_inner_zip() -> bytes:
    """Build the GTFS data files into an in-memory zip and return bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("stops.txt", STOPS_CSV)
        zf.writestr("routes.txt", ROUTES_CSV)
        zf.writestr("trips.txt", TRIPS_CSV)
        zf.writestr("stop_times.txt", STOP_TIMES_CSV)
        zf.writestr("calendar.txt", CALENDAR_CSV)
        # No calendar_dates.txt — optional in real GTFS
    return buf.getvalue()


def build_zip(dest: Path) -> None:
    """Build a zip-of-zips fixture mirroring the real Victorian GTFS feed structure."""
    inner_bytes = build_inner_zip()
    outer_buf = io.BytesIO()
    with zipfile.ZipFile(outer_buf, mode="w", compression=zipfile.ZIP_DEFLATED) as outer:
        # Metro train (mode 2) inner zip — same path as real Victorian GTFS
        outer.writestr("2/google_transit.zip", inner_bytes)

    dest.write_bytes(outer_buf.getvalue())
    print(f"Written {dest} ({dest.stat().st_size} bytes)")
    # Quick sanity check
    with zipfile.ZipFile(dest) as outer:
        print("Outer contents:", outer.namelist())
        with outer.open("2/google_transit.zip") as blob:
            import io as _io
            with zipfile.ZipFile(_io.BytesIO(blob.read())) as inner:
                print("Inner contents:", inner.namelist())


if __name__ == "__main__":
    build_zip(OUTPUT)
