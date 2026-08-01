# ptv-departures

Sidecar HTTP service that bridges [Victoria's Open Data
transport feeds](https://opendata.transport.vic.gov.au/) to a
Terminus-Exchange-friendly JSON shape.

Designed to run alongside a self-hosted [Terminus](https://github.com/usetrmnl/terminus)
BYOS stack and feed TRMNL e-paper devices with next-train departures.

## How it works

```
┌──────────────────────────────────────────────┐
│ ptv-departures sidecar                       │
│                                              │
│  ┌──────────────────────┐                    │
│  │ Static GTFS schedule │  (anonymous, ~233MB │
│  │ /tmp/vic-gtfs.zip    │  weekly refresh)    │
│  └──────────┬───────────┘                    │
│             │                                │
│  ┌──────────▼──────────┐  ┌──────────────┐   │
│  │ StaticGTFS in-mem   │  │ GTFS-R fetch │   │
│  │ (filtered to known  │  │ (KeyID auth, │   │
│  │  stops only)        │  │  per request)│   │
│  └──────────┬──────────┘  └──────┬───────┘   │
│             │                    │           │
│           ┌─▼────────────────────▼─┐         │
│           │ build_departures_      │         │
│           │ payload                │         │
│           └────────────┬───────────┘         │
└────────────────────────┼─────────────────────┘
                         ▼
                  Terminus Exchange JSON
```

## API

```
GET /health                         → {"status": "ok"}
GET /departures/{stop_slug}         → JSON below
```

### Response shape

```json
{
  "stop": "Ringwood",
  "fetched_at": "2026-05-21T02:04:32+00:00",
  "departures": [
    {
      "scheduled":       "2026-05-21T12:10:00+10:00",
      "estimated":       "2026-05-21T12:10:00+10:00",
      "minutes_until":   6,
      "scheduled_hhmm":  "12:10",
      "platform":        "2",
      "line":            "Belgrave",
      "destination":     "City Loop",
      "status":          "on_time",
      "delay_minutes":   0
    }
  ]
}
```

`status` is one of `on_time`, `delayed`, `early`, `scheduled`, `canceled`, `skipped`.

## Configuration (env)

| Var | Default | Purpose |
|---|---|---|
| `PTV_API_KEY` | required | Opendata Transport Vic API key (sent as `KeyID` header) |
| `GTFS_STATIC_URL` | OpenData zip URL | Static GTFS schedule zip URL |
| `GTFS_REALTIME_URL` | metro/trip-updates URL | GTFS-Realtime Trip Updates endpoint |
| `GTFS_CACHE_DIR` | `/tmp` (Docker: `/app/data`) | Directory for the cached GTFS zip |
| `CACHE_TTL_SECONDS` | `60` | Hot cache TTL for the combined JSON output |
| `STALE_TTL_SECONDS` | `1800` | Stale-on-error fallback TTL |
| `LOG_LEVEL` | `INFO` | structured JSON logs |

### Getting an API key

Self-service at https://opendata.transport.vic.gov.au/ — register, create a key. The key goes in the `KeyID` header on each request to the realtime feeds. No HMAC signing.

## Adding new stops

1. Download or reuse the cached GTFS zip and search:
   ```bash
   PTV_API_KEY=... python scripts/discover.py search "Ringwood"
   ```
   This prints all stop_ids whose name contains "Ringwood" — each platform is a separate stop_id in the Victorian GTFS. Pick the one for the direction you want (city-bound = the platform that serves trains to the city).
2. Add the resolved stop_id to `src/ptv_departures/stops.py`:
   ```python
   STOPS: dict[str, str] = {
       "ringwood": "<city-bound stop_id>",
   }
   ```
3. Rebuild and redeploy.

## Local dev

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
PTV_API_KEY=... uvicorn ptv_departures.main:app --reload
```

## Docker

```bash
docker build -t ptv-departures .
docker run --rm -p 8000:8000 \
  -e PTV_API_KEY=... \
  -v ptv-departures-cache:/app/data \
  ptv-departures
```

Mount a volume at `/app/data` to cache the 233MB GTFS zip across restarts.

## License

MIT.
