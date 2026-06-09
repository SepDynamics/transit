# Transit Sentinel

Transit Sentinel is a Boston-focused public-transit operations engine. It
archives MBTA GTFS and GTFS Realtime feeds, normalizes them into rolling
corridor and vehicle state, scores service instability, and serves the result
through an API, public status page, and protected operations console.

The current public deployment is the MBTA live stack behind `sepdynamics.co`.
This repository should describe and prove the Boston path only.

For investor and partner meetings, the repo source of truth is:

- [Meeting One-Sheet](docs/MEETING_ONE_SHEET.md) — Plain-English explanation for non-technical stakeholders
- [Investor Brief](docs/INVESTOR_BRIEF.md) — Meeting positioning, differentiation, risks, next steps
- [Architecture](docs/ARCHITECTURE.md) — Technical architecture, data flow, and roadmap
- [Live Deployment](docs/LIVE_DEPLOYMENT.md) — Deployment runbook with current stack audit
- [Uptime & Performance Summary](docs/UPTIME_SUMMARY.md) — Live host reliability, latency, and memory trends

## Current Stack

- Feed archive: `scripts/transit/archive.py` polls MBTA static GTFS, vehicle
  positions, trip updates, and alerts.
- Ingest and store: `scripts/transit/ingest.py` writes live state, rolling
  history, and materialized read models into Valkey.
- API: `scripts/transit/api.py` serves `/api/status/*`, protected
  `/api/transit/*`, and `/health`.
- Frontend: `apps/frontend/` serves the public MBTA status page and the
  protected operations console through nginx in Docker.
- Live operations: `scripts/transit/live_health.py` reports
  host/container/API health. `scripts/transit/prune_history.py` is kept for
  manual recovery of old or misconfigured history keys.
- Proof and calibration: MBTA case packs under `data/case-packs/mbta/`,
  `scripts/transit/replay.py`, `scripts/transit/grade_calibration.py`, and
  `scripts/transit/benchmark_artifacts.py`.

## Documentation Map

- [Investor Brief](docs/INVESTOR_BRIEF.md): selling points, value, differentiation, meeting demo path, risks, and next steps.
- [Meeting One-Sheet](docs/MEETING_ONE_SHEET.md): plain-English site explanation, source-feed translation, and comparison for non-technical meetings.
- [Architecture](docs/ARCHITECTURE.md): how archive, ingest, Valkey, scoring, API, and frontend fit together, plus data calibration, case packs, and roadmap.
- [Live Deployment](docs/LIVE_DEPLOYMENT.md): how the hosted MBTA stack is configured, verified, and recovered, with embedded current stack audit.
- [Uptime & Performance Summary](docs/UPTIME_SUMMARY.md): live host reliability, API response times, feed freshness, and container memory trends.
- [Systemd Backend Runtime](ops/systemd/README.md): optional host-supervised MBTA backend process path.

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

Run committed MBTA case packs:

```bash
make check-transit-case-packs
```

Run the MBTA calibration gate directly:

```bash
PYTHONPATH=. python3 scripts/transit/grade_calibration.py \
  --archive-root data/case-packs/mbta \
  --labels data/case-packs/mbta \
  --strict
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

Seed a deterministic fallback state from archived MBTA data or committed MBTA
case packs:

```bash
make transit-demo-seed ARGS="--redis redis://localhost:6379/0 --clear-store --replay-case-pack-catalog data/case-packs/mbta"
```

Run notifications against a local API:

```bash
make transit-notify ARGS="--api http://localhost:8000"
```

Run the opt-in Docker notification sidecar:

```bash
TRANSIT_NOTIFY_API_BEARER_TOKEN=readonly-token \
TRANSIT_NOTIFY_WEBHOOK_URL=https://hooks.example.com/transit \
docker compose -f docker-compose.transit.yml --profile notify up -d notify
```

Check the live host:

```bash
make transit-live-health
```

Capture current API parity fixtures:

```bash
make transit-api-parity ARGS="capture --base-url http://127.0.0.1:8000 --output-dir output/api-parity/current"
```

Manually trim rolling history keys during recovery:

```bash
make transit-prune-history ARGS="--redis redis://localhost:6379/0 --retention 120"
```

Generate MBTA benchmark artifacts:

```bash
make transit-benchmark-artifacts ARGS="--archive-root data/case-packs/mbta --labels data/case-packs/mbta --artifact-name mbta-suite"
```

## Product Boundary

This repo is strongest today as a Boston public-data service-status layer, live
operations console, and replayable MBTA proof system. It is not a dispatch
replacement: public feeds do not include internal constraints such as crew,
signals, supervisor assignments, or internal incident response state.
