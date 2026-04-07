# Transit Sentinel Architecture

## Goal

Transit Sentinel watches public transit operations data, detects service
instability early, and turns that state into operator-facing incidents,
recommended actions, and proof artifacts.

## Runtime Flow

### 1. Archive Collectors

The repo currently supports two archive paths:

- `scripts/transit/archive.py`
  Polls configured HTTP feed URLs and writes snapshots to `data/feeds/<agency>/`.
- `scripts/transit/archive_ws.py`
  Collects websocket realtime feeds for agencies that do not expose the same
  data over simple HTTP polling. This is the current LA Metro rail and bus
  realtime lane.

Each archive lane maintains:

- a `current/` working set for ingest
- timestamped `archive/YYYY/MM/DD/HHMMSSZ/` snapshots
- feed metadata and capture manifests

### 2. Ingest And Replay

- `scripts/transit/ingest.py`
  Normalizes the current working set and persists the latest state to Valkey.
- `scripts/transit/replay.py`
  Imports archived snapshots into Valkey as named replay traces.

Live ingest and replay share the same store shape so the dashboard and API can
switch between `scope=live`, `scope=replay`, and `scope=all`.

### 2.5. Runtime Supervision

For host-based live MBTA operation, the committed `systemd --user` assets under
`ops/systemd/user/` supervise:

- archive collection
- the ingest loop
- the API

The grouped target `transit-sentinel-mbta-live.target` is the preferred
non-container runtime path for a durable live backend.

### 3. Rolling Store

`scripts/transit/store.py` is the repo's operational memory layer. It retains:

- latest network health
- latest corridor and vehicle entities
- regime history
- incident memory
- rolling corridor trends
- replay trace inventory
- network and corridor scorecards

This keeps dashboard reads cheap and lets the frontend drill into corridor and
vehicle history without rereading raw archive files.

### 4. Scoring And Incidents

The transit scorer converts rolling public-feed windows into transit-native
regimes such as:

- `healthy`
- `bunching_onset`
- `headway_collapse`
- `terminal_congestion`
- `stop_dwell_instability`
- `corridor_unstable`
- `service_degraded`
- `feed_incoherent`

Those regimes remain available in the API and store, but the live console maps
them into operator-facing labels such as:

- `Service irregularity`
- `Severe bunching / service gap`
- `Terminal congestion`
- `Confirmed disruption`
- `Telemetry degraded`

Actions are surfaced with an explicit operational priority queue:

- `hold`
- `short_turn`
- `dispatch_relief`
- `inspect_terminal`
- `warn_riders`
- `mark_feed_degraded`
- `monitor`

Each scored output carries the raw hazard value, confidence, provenance, and
feature evidence. The operator UI renders that hazard value as `Risk score` and
assigns a priority tier of `Immediate`, `High`, `Watch`, or `Monitor`.

### 5. API Surface

`scripts/transit/api.py` serves `/api/transit/*` endpoints for:

- `health`
- `entities`
- `regimes`
- `incidents`
- `trends`
- `history`
- `sources`
- `map`
- `scorecard`

The API reads from Valkey, not directly from raw files, so live and replay views
share the same contract.

### 6. Frontend

The React console under `apps/frontend/` is the main user-facing surface. It
currently includes:

- network overview metrics
- corridor overview cards
- corridor trend watch
- incident feed
- vehicle inventory and drilldown
- replay scope and trace selection
- map view backed by `/api/transit/map`
- KPI scorecard backed by `/api/transit/scorecard`

### 7. Notifications And Reports

The repo also supports operational sidecars and proof outputs:

- `scripts/transit/demo_seed.py` for deterministic hosted-demo seeding from committed case packs
- `scripts/transit/notify.py` for webhook, SMTP, and JSONL notifications
- `scripts/transit/report.py` for archive-based corridor summaries
- `scripts/transit/grade_calibration.py` and `render_calibration_summary.py`
  for case-pack grading and report generation
- `scripts/transit/benchmark_artifacts.py` for repeatable artifact bundles under `artifacts/benchmarks/`

### 8. Case Packs

Committed proof data lives under `data/case-packs/`. These packs are used to:

- replay known scenarios
- compare Sentinel against a naive baseline
- keep scoring changes regression-tested
- demonstrate public-data proof of value

## Supported Public-Data Lanes

- MBTA: HTTP GTFS and GTFS-RT archive lane
- LA Metro rail: static GTFS plus websocket realtime lane
- LA Metro bus: static GTFS plus websocket realtime lane

There is no Caltrans-specific adapter in the repo today.

## Architectural Boundaries

This repository is transit-only. Documentation, runtime code, test data, and
case packs should describe the current transit product rather than stale repo
history or unrelated infrastructure domains.
