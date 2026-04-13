# Transit Sentinel

Transit Sentinel is a public-transit operations intelligence repo. It archives
public GTFS and GTFS-RT feeds, persists rolling transit state in Valkey, scores
corridor/service regimes, exposes incidents through an HTTP API and React
console, and supports replay plus calibration on archived case packs.

## Current Repo Surface

- live archive lanes for MBTA and LA Metro
- Valkey-backed ingest, replay, rolling history, incident memory, and scorecard aggregation
- HTTP API for health, entities, regimes, incidents, trends, history, sources, map, and scorecard
- React operations console with replay scope switching, map, trend watch, priority-ranked incident feed, and network scorecard
- calibration and case-pack grading against committed public scenarios
- notification dispatch to webhook, SMTP, and JSONL sinks
- `systemd --user` assets for durable MBTA archive, ingest, and API supervision on Linux hosts

## Supported Agency Lanes

- `mbta`
- `lametro-rail`
- `lametro-bus`

MBTA is the primary HTTP polling lane. LA Metro rail and bus use static GTFS
plus websocket realtime collection for vehicle positions and trip updates.

## Docs

- [`docs/ARCHITECTURE.md`](/sep/transit-sentinel/docs/ARCHITECTURE.md)
- [`docs/REPOSITORY_STATUS.md`](/sep/transit-sentinel/docs/REPOSITORY_STATUS.md)
- [`docs/FRONTEND_VALUE_ADD_PLAN.md`](/sep/transit-sentinel/docs/FRONTEND_VALUE_ADD_PLAN.md)
- [`docs/REPO_SCOPE.md`](/sep/transit-sentinel/docs/REPO_SCOPE.md)
- [`docs/PUBLIC_DATA_OUTLINE.md`](/sep/transit-sentinel/docs/PUBLIC_DATA_OUTLINE.md)
- [`docs/MBTA_DATA_LANE.md`](/sep/transit-sentinel/docs/MBTA_DATA_LANE.md)
- [`docs/CALIBRATION_USE_CASE.md`](/sep/transit-sentinel/docs/CALIBRATION_USE_CASE.md)
- [`docs/HOSTED_DEMO_RUNBOOK.md`](/sep/transit-sentinel/docs/HOSTED_DEMO_RUNBOOK.md)
- [`docs/LIVE_OPERATIONS_RUNBOOK.md`](/sep/transit-sentinel/docs/LIVE_OPERATIONS_RUNBOOK.md)
- [`docs/PROJECT_OUTLINE.md`](/sep/transit-sentinel/docs/PROJECT_OUTLINE.md)
- [`docs/EXECUTION_BACKLOG.md`](/sep/transit-sentinel/docs/EXECUTION_BACKLOG.md)
- [`docs/ROADMAP_90_DAYS.md`](/sep/transit-sentinel/docs/ROADMAP_90_DAYS.md)
- [`ops/systemd/README.md`](/sep/transit-sentinel/ops/systemd/README.md)

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

Durable host-side live runtime with `systemd --user`:

```bash
mkdir -p ~/.config/systemd/user ~/.config/transit-sentinel
cp ops/systemd/user/*.service ~/.config/systemd/user/
cp ops/systemd/user/*.target ~/.config/systemd/user/
cp ops/systemd/user/transit-sentinel-mbta.env.example \
  ~/.config/transit-sentinel/transit-sentinel-mbta.env
systemctl --user daemon-reload
loginctl enable-linger "$USER"
systemctl --user enable --now transit-sentinel-mbta-live.target
```

If the host is already running manual `archive.py`, `ingest.py`, or `api.py`
loops, stop those first so the `systemd --user` target becomes the only owner
of the feed collectors and port `8000`.

Live operator surfaces now expose operator-facing service-state labels and an
explicit action priority queue. Internal regime tokens still exist in the API
for replay and scoring, but the live console should be read through labels such
as `Service irregularity`, `Severe bunching / service gap`, and priority tiers
`Immediate`, `High`, `Watch`, and `Monitor`.

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

Seed a repeatable demo state from the committed case packs:

```bash
make transit-demo-seed ARGS="--redis redis://localhost:6379/0 --clear-store"
```

`transit-demo-seed` now prefers recent archive windows from `data/feeds/mbta`
and other available live archive roots. If no archive corpus is present, it
falls back to the richer committed MBTA overnight pack before loading the
smaller proof fixtures.

Persist a proof window around a detected incident:

```bash
make transit-proof-window ARGS="--archive-root data/feeds/mbta --incident-json path/to/incident.json"
```

Generate benchmark artifacts under `artifacts/benchmarks/`:

```bash
make transit-benchmark-artifacts ARGS="--archive-root data/case-packs --labels data/case-packs --artifact-name cross-city-suite"
```
