"""Async TTL cache with stale-on-error fallback."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TTLCache:
    """Per-key TTL cache. On fetcher error, returns stale value if not too old."""

    def __init__(
        self,
        *,
        ttl_seconds: int,
        stale_ttl_seconds: int,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._ttl = ttl_seconds
        self._stale_ttl = stale_ttl_seconds
        self._clock = clock
        self._store: dict[str, tuple[datetime, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def get(self, key: str, fetcher: Callable[[], Awaitable[Any]]) -> Any:
        now = self._clock()
        cached = self._store.get(key)

        if cached:
            stored_at, value = cached
            age = (now - stored_at).total_seconds()
            if age <= self._ttl:
                return value

        async with self._lock(key):
            now = self._clock()
            cached = self._store.get(key)
            if cached:
                stored_at, value = cached
                age = (now - stored_at).total_seconds()
                if age <= self._ttl:
                    return value

            try:
                fresh = await fetcher()
            except Exception:
                if cached:
                    stored_at, value = cached
                    age = (now - stored_at).total_seconds()
                    if age <= self._stale_ttl:
                        return value
                raise

            self._store[key] = (now, fresh)
            return fresh
