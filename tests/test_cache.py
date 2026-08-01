"""Async TTL cache with stale-on-error fallback."""
from datetime import datetime, timedelta, timezone

import pytest

from ptv_departures.cache import TTLCache

_BASE = datetime(2026, 5, 21, 0, 0, 0, tzinfo=timezone.utc)


def at(secs: int) -> datetime:
    return _BASE + timedelta(seconds=secs)


async def test_cache_miss_calls_fetcher_and_stores():
    calls = []

    async def fetcher():
        calls.append(1)
        return {"hello": "world"}

    cache = TTLCache(ttl_seconds=60, stale_ttl_seconds=1800, clock=lambda: at(0))
    result = await cache.get("k", fetcher)

    assert result == {"hello": "world"}
    assert len(calls) == 1


async def test_cache_hit_within_ttl_does_not_call_fetcher():
    calls = []
    clock_time = [at(0)]

    async def fetcher():
        calls.append(1)
        return {"n": len(calls)}

    cache = TTLCache(ttl_seconds=60, stale_ttl_seconds=1800, clock=lambda: clock_time[0])
    await cache.get("k", fetcher)

    clock_time[0] = at(30)
    result = await cache.get("k", fetcher)

    assert result == {"n": 1}
    assert len(calls) == 1


async def test_cache_expired_entry_refetches():
    calls = []
    clock_time = [at(0)]

    async def fetcher():
        calls.append(1)
        return {"n": len(calls)}

    cache = TTLCache(ttl_seconds=60, stale_ttl_seconds=1800, clock=lambda: clock_time[0])
    await cache.get("k", fetcher)

    clock_time[0] = at(120)
    result = await cache.get("k", fetcher)

    assert result == {"n": 2}
    assert len(calls) == 2


async def test_stale_returns_old_value_if_fetcher_raises():
    calls = []
    clock_time = [at(0)]

    async def good_fetcher():
        calls.append("good")
        return {"version": 1}

    async def bad_fetcher():
        calls.append("bad")
        raise RuntimeError("PTV down")

    cache = TTLCache(ttl_seconds=60, stale_ttl_seconds=1800, clock=lambda: clock_time[0])
    await cache.get("k", good_fetcher)

    clock_time[0] = at(120)
    result = await cache.get("k", bad_fetcher)

    assert result == {"version": 1}
    assert calls == ["good", "bad"]


async def test_no_cache_at_all_propagates_fetcher_error():
    async def bad_fetcher():
        raise RuntimeError("PTV down")

    cache = TTLCache(ttl_seconds=60, stale_ttl_seconds=1800, clock=lambda: at(0))
    with pytest.raises(RuntimeError, match="PTV down"):
        await cache.get("k", bad_fetcher)


async def test_stale_beyond_stale_ttl_propagates_error():
    clock_time = [at(0)]

    async def good_fetcher():
        return {"version": 1}

    async def bad_fetcher():
        raise RuntimeError("PTV down")

    cache = TTLCache(ttl_seconds=60, stale_ttl_seconds=1800, clock=lambda: clock_time[0])
    await cache.get("k", good_fetcher)

    clock_time[0] = at(2000)
    with pytest.raises(RuntimeError, match="PTV down"):
        await cache.get("k", bad_fetcher)
