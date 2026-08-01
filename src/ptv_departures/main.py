"""FastAPI sidecar entrypoint."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException

from ptv_departures.cache import TTLCache
from ptv_departures.departures import build_departures_payload
from ptv_departures.gtfs_realtime import GTFSRealtimeError, fetch_trip_updates
from ptv_departures.gtfs_static import StaticGTFS, download_gtfs_zip
from ptv_departures.stops import DESTINATION_FILTER, DIRECTION_FILTER, STOPS

logger = logging.getLogger("ptv_departures")

DEFAULT_GTFS_STATIC_URL = (
    "https://opendata.transport.vic.gov.au/dataset/3f4e292e-7f8a-4ffe-831f-1953be0fe448"
    "/resource/fb152201-859f-4882-9206-b768060b50ad/download/gtfs.zip"
)
DEFAULT_GTFS_REALTIME_URL = (
    "https://api.opendata.transport.vic.gov.au"
    "/opendata/public-transport/gtfs/realtime/v1/metro/trip-updates"
)

# Module-level static GTFS state — patchable in tests.
_static_state: dict[str, Optional[StaticGTFS]] = {"value": None}
_static_lock = asyncio.Lock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _label_from_slug(slug: str) -> str:
    return slug.replace("-", " ").title()


async def _get_static() -> StaticGTFS:
    if _static_state["value"] is not None:
        return _static_state["value"]
    async with _static_lock:
        if _static_state["value"] is not None:
            return _static_state["value"]
        url = os.getenv("GTFS_STATIC_URL", DEFAULT_GTFS_STATIC_URL)
        cache_dir = Path(os.getenv("GTFS_CACHE_DIR", "/tmp"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        dest = cache_dir / "vic-gtfs.zip"
        await download_gtfs_zip(url, dest)
        logger.info("Loading GTFS zip from %s", dest)
        all_known: set[str] = set()
        for ids in STOPS.values():
            all_known.update(ids)
        _static_state["value"] = StaticGTFS.from_zip(dest, known_stop_ids=all_known)
        logger.info("GTFS static loaded: %d stops indexed", len(STOPS))
        return _static_state["value"]


def create_app() -> FastAPI:
    app = FastAPI(title="ptv-departures", version="0.2.2")
    cache = TTLCache(
        ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "60")),
        stale_ttl_seconds=int(os.getenv("STALE_TTL_SECONDS", "1800")),
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/departures/{stop}")
    async def departures(stop: str) -> dict:
        stop_ids = STOPS.get(stop)
        if not stop_ids:
            raise HTTPException(status_code=404, detail="unknown_stop")

        api_key = os.getenv("PTV_API_KEY")
        if not api_key:
            logger.error("PTV_API_KEY not configured")
            raise HTTPException(status_code=500, detail="missing_credentials")

        async def fetcher() -> dict:
            static = await _get_static()
            rt_url = os.getenv("GTFS_REALTIME_URL", DEFAULT_GTFS_REALTIME_URL)
            try:
                updates = await fetch_trip_updates(api_key=api_key, url=rt_url)
            except GTFSRealtimeError as exc:
                logger.warning(
                    "GTFS-R fetch failed for stop=%s: %s; continuing without realtime",
                    stop,
                    exc,
                )
                updates = []
            direction_id = DIRECTION_FILTER.get(stop)
            destination_contains = DESTINATION_FILTER.get(stop)
            return build_departures_payload(
                static,
                updates,
                stop_ids=stop_ids,
                stop_label=_label_from_slug(stop),
                direction_id=direction_id,
                destination_contains=destination_contains,
                now_utc=_utcnow(),
            )

        try:
            return await cache.get(stop, fetcher)
        except Exception as exc:
            logger.exception("departures failed for stop=%s", stop)
            raise HTTPException(status_code=502, detail="upstream_unavailable") from exc

    return app


app = create_app()
