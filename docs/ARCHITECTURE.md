# Architecture

Transit Sentinel turns public feed activity into ranked operating context:

`GTFS / GTFS-RT -> archive -> ingest -> Valkey -> scoring -> API -> console/status`

The live deployment currently favors a bounded, low-memory MBTA lane. Replay and
LA Metro collection remain in the repo, but the public host is live-first and
does not serve replay traces.

## Runtime Flow

### Archive

- `scripts/transit/archive.py` polls HTTP GTFS and GTFS-RT feeds.
- `scripts/transit/archive_ws.py` collects websocket realtime feeds.
- `data/feeds/<agency>/current/` is the ingest working set.
- `data/feeds/<agency>/archive/...` is the replay and proof corpus when
  history capture is enabled.

The live host override sets `TRANSIT_ARCHIVE_CURRENT_ONLY=1`, so the public
deployment refreshes the current MBTA working set without growing local archive
directories on every capture.

### Ingest

`scripts/transit/ingest.py` reads the current working set, normalizes routes,
vehicles, alerts, and trip updates, scores corridor state, and writes the
latest state plus rolling history into Valkey.

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

- latest network health
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

The frontend should lead with operator language instead of raw regime tokens:

- `Service irregularity`
- `Severe bunching / service gap`
- `Terminal congestion`
- `Confirmed disruption`
- `Telemetry degraded`
- `Immediate`, `High`, `Watch`, and `Monitor`

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

Full entities, history, map, and large scorecard payloads are intentionally not
kept in the generic API cache on the small live host.

### Frontend

`apps/frontend/` is a React/Vite app served by nginx in the production
container. It provides:

- public status page
- operations console
- technical stack summary
- priority corridor queue
- map view
- vehicle and corridor drilldowns
- trend and scorecard panels

The console uses a consolidated dashboard endpoint for the main polling path.
Slower scorecard, map, source, and history polls are kept separate so the main
dashboard can stay responsive without overloading the API. The MapLibre bundle
is lazy-loaded from the map panel instead of being pulled into the first app
chunk.

### Replay And Calibration

Replay imports archived snapshots into the same Valkey shape as live ingest.
Case packs under `data/case-packs/` keep scoring changes grounded in labeled
public incidents and quiet controls.

Use replay and calibration for proof, regression, and demos. Do not treat a
seeded replay state as the primary public deployment while the live MBTA lane is
healthy.

## Boundaries

- MBTA is the primary live lane.
- LA Metro rail and bus collection exist, but public alert quality is weaker
  than MBTA.
- There is no Caltrans adapter in the repo today.
- Auth/RBAC exists but is optional by default. Require auth before exposing ops
  endpoints beyond a trusted deployment.
- Public feeds can prove service instability and rider-facing status; they do
  not expose internal dispatch constraints.
