#!/usr/bin/env python
"""Find Victorian GTFS stop_ids for a name fragment.

Usage:
  python scripts/discover.py search "Ringwood"
  python scripts/discover.py download-zip     # just pre-fetch the zip into /tmp
"""
from __future__ import annotations

import asyncio
import csv
import io
import os
import sys
import zipfile
from pathlib import Path

from ptv_departures.gtfs_static import download_gtfs_zip
from ptv_departures.main import DEFAULT_GTFS_STATIC_URL

CACHE_PATH = Path(os.getenv("GTFS_CACHE_DIR", "/tmp")) / "vic-gtfs.zip"


async def _ensure_zip() -> Path:
    await download_gtfs_zip(DEFAULT_GTFS_STATIC_URL, CACHE_PATH)
    return CACHE_PATH


def _search_in_zipfile(zf: zipfile.ZipFile, term_lower: str, mode_label: str) -> None:
    """Print stops matching *term_lower* from *zf*, annotated with *mode_label*."""
    for name in zf.namelist():
        if not name.endswith("stops.txt"):
            continue
        with zf.open(name) as f:
            reader = csv.DictReader(line.decode("utf-8-sig") for line in f)
            for row in reader:
                if term_lower in row.get("stop_name", "").lower():
                    plat = row.get("platform_code") or ""
                    suffix = f" (platform {plat})" if plat else ""
                    print(
                        f"  {row['stop_id']:>20}  {row['stop_name']}{suffix}"
                        f"  [{mode_label}]"
                    )


async def search(term: str) -> None:
    zip_path = await _ensure_zip()
    term_lower = term.lower()
    with zipfile.ZipFile(zip_path) as outer:
        outer_names = outer.namelist()
        # Flat layout: stops.txt directly in outer zip
        if any(n.endswith("stops.txt") for n in outer_names):
            _search_in_zipfile(outer, term_lower, mode_label="root")
        else:
            # Zip-of-zips: each nested zip is a transport mode
            for name in outer_names:
                if not name.endswith(".zip"):
                    continue
                mode = name.split("/")[0]  # e.g. "2" → metro train
                with outer.open(name) as blob, zipfile.ZipFile(io.BytesIO(blob.read())) as inner:
                    _search_in_zipfile(inner, term_lower, mode_label=f"mode={mode}")


async def download_only() -> None:
    path = await _ensure_zip()
    print(f"GTFS zip at {path} (size {path.stat().st_size} bytes)")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd, *args = sys.argv[1:]
    if cmd == "search" and args:
        asyncio.run(search(args[0]))
    elif cmd == "download-zip":
        asyncio.run(download_only())
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
