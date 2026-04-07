# Transit Sentinel

Transit Sentinel is a public-transit operations intelligence repo. It archives
public GTFS and GTFS-RT feeds, persists rolling transit state in Valkey, scores
corridor/service regimes, exposes incidents through an HTTP API and React
console, and supports replay plus calibration on archived case packs.

## Current Repo Surface

- live archive lanes for MBTA and LA Metro
- Valkey-backed ingest, replay, rolling history, incident memory, and scorecard aggregation
- HTTP API for health, entities, regimes, incidents, trends, history, sources, map, and scorecard
- React operations console with replay scope switching, map, trend watch, incident feed, and network scorecard
- calibration and case-pack grading against committed public scenarios
- notification dispatch to webhook, SMTP, and JSONL sinks

## Supported Agency Lanes

- `mbta`
- `lametro-rail`
- `lametro-bus`

MBTA is the primary HTTP polling lane. LA Metro rail and bus use static GTFS
plus websocket realtime collection for vehicle positions and trip updates.

## Docs

- [`docs/ARCHITECTURE.md`](/sep/transit-sentinel/docs/ARCHITECTURE.md)
- [`docs/REPOSITORY_STATUS.md`](/sep/transit-sentinel/docs/REPOSITORY_STATUS.md)
- [`docs/REPO_SCOPE.md`](/sep/transit-sentinel/docs/REPO_SCOPE.md)
- [`docs/PUBLIC_DATA_OUTLINE.md`](/sep/transit-sentinel/docs/PUBLIC_DATA_OUTLINE.md)
- [`docs/MBTA_DATA_LANE.md`](/sep/transit-sentinel/docs/MBTA_DATA_LANE.md)
- [`docs/CALIBRATION_USE_CASE.md`](/sep/transit-sentinel/docs/CALIBRATION_USE_CASE.md)
- [`docs/PROJECT_OUTLINE.md`](/sep/transit-sentinel/docs/PROJECT_OUTLINE.md)
- [`docs/EXECUTION_BACKLOG.md`](/sep/transit-sentinel/docs/EXECUTION_BACKLOG.md)
- [`docs/ROADMAP_90_DAYS.md`](/sep/transit-sentinel/docs/ROADMAP_90_DAYS.md)

## Quick Start

Start Valkey:

```bash
docker run --rm -p 6379:6379 redis:7-alpine
```

Archive one MBTA snapshot:

```bash
make transit-mbta-archive ARGS="--once"
```

Persist the current working set into Valkey:

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

Import archived snapshots as a replay trace:

```bash
make transit-replay ARGS="--redis redis://localhost:6379/0 --archive-root data/feeds/mbta --trace-id mbta-proof --max-snapshots 20"
```

LA Metro realtime archive targets:

```bash
make transit-lametro-rail-archive
make transit-lametro-bus-archive
```

Notification sidecar example:

```bash
make transit-notify ARGS="--api http://localhost:8000 --webhook https://hooks.example.com/transit"
```

Containerized runtime:

```bash
docker compose -f docker-compose.transit.yml up --build
```

## Calibration And Checks

Run the main repo checks:

```bash
make check
```

Run the full repo sweep:

```bash
make check-all
```

Run the committed cross-city case-pack gate:

```bash
make check-transit-case-packs
```
