# MBTA Data Lane

MBTA remains the primary public-data lane in this repository.

## Why MBTA Matters Here

- it supports the cleanest end-to-end HTTP archive workflow in the repo
- it is the best lane for continuous archive growth and replay imports
- it is the strongest source of additional case packs and KPI reporting work

## Repo-Configured Feed Inputs

- static GTFS: `https://cdn.mbta.com/MBTA_GTFS.zip`
- vehicle positions: `https://cdn.mbta.com/realtime/VehiclePositions_enhanced.json`
- trip updates: `https://cdn.mbta.com/realtime/TripUpdates_enhanced.json`
- alerts: `https://cdn.mbta.com/realtime/Alerts_enhanced.json`

## Archive Shape

The archive lane writes to:

- `data/feeds/mbta/current/`
- `data/feeds/mbta/archive/YYYY/MM/DD/HHMMSSZ/`

`current/` is the ingest working set. `archive/` is the replay and proof corpus.

Each snapshot directory contains:

- raw feed files for that capture
- per-file metadata
- a capture manifest

## Typical Workflow

Archive one snapshot:

```bash
make transit-mbta-archive ARGS="--once"
```

Persist the current working set into Valkey:

```bash
make transit-ingest ARGS="--once --redis redis://localhost:6379/0"
```

Import archived snapshots as a replay trace:

```bash
make transit-replay ARGS="--redis redis://localhost:6379/0 --archive-root data/feeds/mbta --trace-id mbta-proof --max-snapshots 20"
```

Start the API:

```bash
make transit-api ARGS="--redis redis://localhost:6379/0"
```

Generate an archive-based corridor report:

```bash
make transit-history-report ARGS="--root-dir data/feeds/mbta --max-snapshots 20"
```

Run notifications against the live API:

```bash
make transit-notify ARGS="--api http://localhost:8000"
```

## Role In The Product

MBTA is the repo's best lane for:

- validating live archive and replay behavior
- generating more labeled case packs
- testing map and scorecard surfaces against real public data
- producing repeatable proof artifacts from archived service disruptions
