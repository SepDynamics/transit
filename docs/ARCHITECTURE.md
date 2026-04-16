# Architecture

Transit Sentinel turns MBTA public feed activity into ranked operating context:

`MBTA GTFS / GTFS-RT -> archive -> ingest -> Valkey -> scoring -> API -> console/status`

The live deployment is a bounded, low-memory MBTA stack. Replay remains a proof
and demo tool, but the public host serves current MBTA feed state and does not
serve replay traces.

## Runtime Flow

### Archive

- `scripts/transit/archive.py` polls MBTA HTTP GTFS and GTFS Realtime feeds.
- `data/feeds/mbta/current/` is the ingest working set.
- `data/feeds/mbta/archive/...` is the replay and proof corpus when history
  capture is enabled.

The live host override sets `TRANSIT_ARCHIVE_CURRENT_ONLY=1`, so the public
deployment refreshes the current MBTA working set without growing local archive
directories on every capture.

### Ingest

`scripts/transit/ingest.py` reads the current MBTA working set, normalizes
routes, vehicles, alerts, and trip updates, scores corridor state, and writes
the latest state plus rolling history into Valkey.

The public host uses conservative ingest settings:

- `TRANSIT_INGEST_INTERVAL_SECONDS=20`
- `TRANSIT_HISTORY_RETENTION=120`
- `TRANSIT_HISTORY_INTERVAL_SECONDS=60`
- `TRANSIT_SNAPSHOT_CACHE_TTL_SECONDS=120`
- `TRANSIT_READ_MODELS_ENABLED=1`
- `TRANSIT_READ_MODEL_SCORECARD_LIMIT=60`

That keeps a useful live scorecard window without allowing rolling history to
consume the host.

### Valkey

`scripts/transit/store.py` is the operational memory layer. It stores:

- latest MBTA network health
- latest corridor, vehicle, regime, incident, feed-status, and error payloads
- rolling vehicle and corridor history
- replay trace metadata when replay is enabled
- scorecard and trend source data
- materialized live read models:
  - `transit:scorecard:live:last`
  - `transit:trends:live:last`
  - `transit:dashboard:live:last`
  - `transit:status:network:last`

The live compose override runs Valkey with AOF disabled and RDB snapshots:

```bash
redis-server --appendonly no --save 300 1
```

Valkey has a container memory limit of `900m` in the compose stack.

Rolling history keys are bounded twice: ingest trims sorted sets to
`TRANSIT_HISTORY_RETENTION`, and each history key receives a native Valkey
expiration. On the live host `TRANSIT_HISTORY_TTL_SECONDS=7200`, matching the
120 samples written every 60 seconds. `scripts/transit/prune_history.py` is a
manual recovery tool for old or misconfigured keys, not a scheduled runtime
dependency.

### Scoring

The scoring layer emits internal regimes such as:

- `healthy`
- `bunching_onset`
- `headway_collapse`
- `terminal_congestion`
- `stop_dwell_instability`
- `corridor_unstable`
- `service_degraded`
- `feed_incoherent`

The C++ byte-stream manifold engine has a bounded analysis window. Its default
`max_windows` is `4096`; a caller-provided `0` maps back to that default instead
of becoming unbounded, and excessive requested caps are clamped at `16384`.

The frontend leads with operator language instead of raw regime tokens:

- `Service irregularity`
- `Severe bunching / service gap`
- `Terminal congestion`
- `Confirmed disruption`
- `Telemetry degraded`
- `Immediate`, `High`, `Watch`, and `Monitor`

Route-level zero delay means there is no measured delay burden in the current
trip-update sample. A route can still rank because alerts, headway compression,
vehicle bunching, or telemetry quality are the active evidence.

Internal tokens remain useful for replay, regression tests, and API consumers
that need exact classifier output.

### API

`scripts/transit/api.py` serves:

- `/health`
- `/api/status/network`
- `/api/status/routes`
- `/api/status/alerts`
- `/api/status/scorecard`
- `/api/transit/dashboard`
- `/api/transit/health`
- `/api/transit/entities`
- `/api/transit/regimes`
- `/api/transit/incidents`
- `/api/transit/trends`
- `/api/transit/history`
- `/api/transit/sources`
- `/api/transit/map`
- `/api/transit/scorecard`

The API reads from Valkey rather than raw archive files. For normal live
frontend paths, it serves the materialized read models first and only falls
back to cold rollups when a request asks for a different scope, trace, or
scorecard window. On the live host, expensive scorecard reads are capped and
cached:

- `TRANSIT_API_CACHE_TTL_SECONDS=15`
- `TRANSIT_API_CACHE_MAX_ENTRIES=6`
- `TRANSIT_API_SCORECARD_MAX_LIMIT=60`
- `TRANSIT_API_SCORECARD_CACHE_TTL_SECONDS=60`
- `TRANSIT_API_MAX_CONCURRENT_REQUESTS=4`
- `TRANSIT_API_REQUEST_QUEUE_SIZE=8`
- `TRANSIT_API_REQUIRE_AUTH=1`

Full entities, history, map, and large scorecard payloads are intentionally not
kept in the generic API cache on the small live host.

JSON `GET` responses include `ETag` and support `If-None-Match`. The frontend
reuses those validators on status and console polling reads, so unchanged
dashboard, map, history, scorecard, and status payloads can return
`304 Not Modified` instead of retransmitting large JSON bodies.

`scripts/transit/api_parity.py` is the migration gate for any future FastAPI
sidecar. It captures and compares status codes, JSON shapes, ETag support, and
conditional GET behavior for public status and frontend-consumed operations
endpoints before any routing changes.

### Notifications

`scripts/transit/notify.py` is available as the `notify` Compose profile. It
polls the protected operations API from inside the Docker network, sends a
bearer token when `TRANSIT_NOTIFY_API_BEARER_TOKEN` is configured, and can write
webhook, SMTP, log-file, or proof-window outputs. It is opt-in so the live host
does not add internal API polling unless a notification target is configured.

### Frontend

`apps/frontend/` is a React/Vite app served by nginx in the production
container. It provides:

- public MBTA status page
- protected operations console
- priority corridor queue
- selected-corridor evidence drawer
- lazy-loaded map view
- vehicle and corridor drilldowns
- trend and scorecard panels

The live public frontend defaults to status-only because `/api/transit/*`
requires bearer auth. In trusted deployments, the console uses a consolidated
dashboard endpoint for the main polling path. Slower scorecard, map, source,
and history polls are kept separate so the main dashboard can stay responsive
without overloading the API. Map and history polls run every 30 seconds, while
the dashboard poll remains 10 seconds. The MapLibre bundle is lazy-loaded from
the map panel instead of being pulled into the first app chunk. Polling GETs use
the shared conditional JSON client so unchanged responses reuse the last parsed
payload.

### Replay And Calibration

Replay imports archived MBTA snapshots into the same Valkey shape as live
ingest. Case packs under `data/case-packs/mbta/` keep scoring changes grounded
in labeled Boston incidents and quiet controls.

Use replay and calibration for proof, regression, and demos. Do not treat a
seeded replay state as the primary public deployment while the live MBTA lane is
healthy.

## Boundaries

- MBTA is the only supported live lane.
- The public frontend uses `/api/status/*`; operations endpoints require bearer
  auth on the live host.
- Replay is disabled on the hosted live stack.
- Public feeds can prove service instability and rider-facing status; they do
  not expose internal dispatch constraints.
- Documentation and committed proof assets should stay Boston-focused unless a
  future scope change is implemented end to end.
