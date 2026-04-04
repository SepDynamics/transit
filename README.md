# Transit Sentinel

Transit Sentinel is a separate repo scaffold for adapting the
`/sep/cluster-sentinel` architecture to public transit operations.

It keeps the same core product pattern:

- ingest live operational signals
- maintain a rolling state store
- score windows into structural regimes
- convert regimes into incidents and actions
- expose the result through an API, dashboard, replay, and evaluation flow

But the target application is transit, not GPU infrastructure.

## Target Application

Transit Sentinel should detect and explain service instability such as:

- bunching onset
- headway collapse
- terminal congestion
- corridor degradation
- feed incoherence

And recommend actions such as:

- hold
- short-turn
- dispatch relief
- inspect terminal
- warn riders

## Current State

This repo was copied first to protect the source repo.

- source repo: [`/sep/cluster-sentinel`](/sep/cluster-sentinel)
- new repo: [`/sep/transit-sentinel`](/sep/transit-sentinel)

This is intentionally a mirror scaffold right now.

What has changed:

- repo name and top-level product framing
- transit architecture and backlog docs
- transit-only API and dashboard surface
- persisted transit ingest/store runtime
- MBTA archive, history, and calibration helpers
- frontend/package metadata

What has not changed yet:

- the legacy cluster/GPU runtime still lives under `scripts/cluster`
- legacy cluster compatibility tests still live under `tests/cluster`
- the copied runtime still models cluster/GPU incidents

What has changed since the initial mirror:

- transit runtime code now lives under `scripts/transit`
- transit tests now live under `tests/transit`
- transit is now the default engineering and CI lane

That is by design. This repo is the safe place to perform the domain refactor.

## Docs

- [`docs/ARCHITECTURE.md`](/sep/transit-sentinel/docs/ARCHITECTURE.md)
- [`docs/PROJECT_OUTLINE.md`](/sep/transit-sentinel/docs/PROJECT_OUTLINE.md)
- [`docs/EXECUTION_BACKLOG.md`](/sep/transit-sentinel/docs/EXECUTION_BACKLOG.md)
- [`docs/REPO_SCOPE.md`](/sep/transit-sentinel/docs/REPO_SCOPE.md)
- [`docs/MIRROR_STATUS.md`](/sep/transit-sentinel/docs/MIRROR_STATUS.md)
- [`docs/ROADMAP_90_DAYS.md`](/sep/transit-sentinel/docs/ROADMAP_90_DAYS.md)
- [`docs/MBTA_DATA_LANE.md`](/sep/transit-sentinel/docs/MBTA_DATA_LANE.md)

## Verification

Run the repo checks that currently matter:

```bash
make check
```

Run the full repo sweep, including legacy cluster compatibility, only when you
intend to touch both lanes:

```bash
make check-all
```

Run legacy cluster compatibility checks explicitly:

```bash
make check-legacy-cluster
```

If you need the legacy GPU/NVML collector stack under `scripts/cluster`, install
the optional legacy extras:

```bash
make install-cluster
```

## Recommended First Dataset Lane

Start with one official public transit system that supports live ingest and replay.

Recommended first target:

- MBTA

Why:

- strong official GTFS and GTFS-RT coverage
- public historical/performance data
- enough complexity to prove value
- simpler first wedge than MTA-scale scope

## Recommended First Milestones

1. Replace GPU/cluster terminology with transit entity terminology.
2. Add GTFS and GTFS-RT ingest.
3. Define transit service regimes and actions.
4. Build one replayable public case-study set.
5. Compare Transit Sentinel against simple threshold baselines.

## MBTA Quick Start

Start Valkey first, then archive a local MBTA snapshot:

```bash
docker run --rm -p 6379:6379 redis:7-alpine
```

Archive one official transit snapshot locally:

```bash
make transit-archive ARGS="--agency mbta --once"
```

Persist the current feed set into the transit store:

```bash
make transit-ingest ARGS="--once --redis redis://localhost:6379/0"
```

Import archived MBTA snapshots into the transit store as a replay trace:

```bash
make transit-replay ARGS="--redis redis://localhost:6379/0 --archive-root data/feeds/mbta --trace-id mbta-proof --max-snapshots 20"
```

Then start the transit API:

```bash
make transit-api ARGS="--redis redis://localhost:6379/0"
```

The legacy cluster API remains in `scripts/cluster/api.py`, but the frontend
transit surface is now served by `scripts/transit/api.py`.
That transit API now exposes rolling corridor trend memory from the persisted
Valkey store, not just the latest file-backed snapshot. Replay imports now sit
beside live ingest in that same store, so the transit dashboard can switch to
`Replay` scope and select a concrete `trace_id`.
Transit runtime helpers now live under `scripts/shared` so transit modules no
longer import cluster-owned utility modules directly. The old
`scripts/cluster/transit_*` wrappers have been removed, and transit container
entrypoints now invoke `scripts/transit/*` directly.

Containerized transit runtime:

```bash
docker compose -f docker-compose.transit.yml up --build
```

Generate a corridor history report from archived snapshots:

```bash
make transit-history-report ARGS="--root-dir data/feeds/mbta --max-snapshots 20"
```

Build a calibration report for a labeled proof case:

```bash
make transit-calibration-report ARGS="--archive-root data/feeds/mbta --labels path/to/labels.json"
```

Run the committed MBTA overnight advisory control suite:

```bash
make transit-calibration-report ARGS="--archive-root data/case-packs/mbta/overnight_advisory_controls --labels data/case-packs/mbta/overnight_advisory_controls/labels"
```

Run the committed MBTA suite across the overnight controls and daytime positive disruption pack:

```bash
make transit-calibration-report ARGS="--archive-root data/case-packs/mbta --labels data/case-packs/mbta"
```

Run the full committed cross-city case-pack gate before pruning more legacy code:

```bash
make check-transit-case-packs
```
