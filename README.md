# Transit Sentinel

Transit Sentinel is a live public-transit operations engine. It archives public
GTFS and GTFS-RT feeds, normalizes them into rolling corridor and vehicle state,
scores service instability, and serves the result through an API, public status
surface, and React operations console.

The current public deployment is the MBTA live lane behind `sepdynamics.co`.
The repo also contains LA Metro rail and bus collection paths, replay tooling,
case-pack calibration, notification dispatch, and benchmark artifact generation.

## Current Stack

- Feed archive: `scripts/transit/archive.py` for HTTP feeds and
  `scripts/transit/archive_ws.py` for websocket feeds.
- Ingest and store: `scripts/transit/ingest.py` writes live state and rolling
  history into Valkey.
- API: `scripts/transit/api.py` serves `/api/transit/*`, `/api/status/*`, and
  `/health`.
- Frontend: `apps/frontend/` serves the public status page and operations
  console through nginx in Docker.
- Live operations: `scripts/transit/live_health.py` reports host/container/API
  health, and `scripts/transit/prune_history.py` trims rolling Valkey history.
- Proof and calibration: `data/case-packs/`, `scripts/transit/replay.py`,
  `scripts/transit/grade_calibration.py`, and
  `scripts/transit/benchmark_artifacts.py`.

## Documentation Map

- [Architecture](/sep/transit-sentinel/docs/ARCHITECTURE.md): how archive,
  ingest, Valkey, scoring, API, and frontend fit together.
- [Live Deployment](/sep/transit-sentinel/docs/LIVE_DEPLOYMENT.md): how the
  hosted MBTA stack is configured, verified, and recovered.
- [Data And Calibration](/sep/transit-sentinel/docs/DATA_AND_CALIBRATION.md):
  supported public-data lanes, case packs, replay, and grading workflow.
- [Roadmap](/sep/transit-sentinel/docs/ROADMAP.md): current state, boundaries,
  and the next sensible work.
- [Repo Scope](/sep/transit-sentinel/docs/REPO_SCOPE.md): what belongs in this
  repository.
- [Systemd Backend Runtime](/sep/transit-sentinel/ops/systemd/README.md):
  optional host-supervised backend process path.

## Local Development

Start Valkey:

```bash
docker run --rm -p 6379:6379 redis:7-alpine
```

Capture one MBTA working set:

```bash
make transit-mbta-archive ARGS="--once"
```

Ingest that working set into Valkey:

```bash
make transit-ingest ARGS="--once --redis redis://localhost:6379/0"
```

Start the API:

```bash
make transit-api ARGS="--redis redis://localhost:6379/0"
```

Start the frontend:

```bash
cd apps/frontend
npm install
npm run dev
```

Run the container stack:

```bash
docker compose -f docker-compose.transit.yml up --build
```

Run the live-host shape used by the public deployment:

```bash
docker compose -f docker-compose.transit.yml -f docker-compose.live-host.yml up -d --build valkey archive ingest api frontend
```

## Checks

Run the main transit gate:

```bash
make check
```

Run all tests and frontend checks:

```bash
make check-all
```

Run committed case packs:

```bash
make check-transit-case-packs
```

Build frontend production assets:

```bash
make frontend-build
```

## Common Operations

Import archived MBTA snapshots as a replay trace:

```bash
make transit-replay ARGS="--redis redis://localhost:6379/0 --archive-root data/feeds/mbta --trace-id mbta-proof --max-snapshots 20"
```

Seed a deterministic fallback state from archive data or committed case packs:

```bash
make transit-demo-seed ARGS="--redis redis://localhost:6379/0 --clear-store"
```

Run notifications against a local API:

```bash
make transit-notify ARGS="--api http://localhost:8000"
```

Check the live host:

```bash
make transit-live-health
```

Trim rolling history keys:

```bash
make transit-prune-history ARGS="--redis redis://localhost:6379/0 --retention 120"
```

Generate benchmark artifacts:

```bash
make transit-benchmark-artifacts ARGS="--archive-root data/case-packs --labels data/case-packs --artifact-name cross-city-suite"
```

## Product Boundary

This repo is strongest today as a public-data service-status layer, live
operations console, and replayable proof system. It is not a dispatch
replacement: public feeds do not include internal constraints such as crew,
signals, supervisor assignments, or internal incident response state.
