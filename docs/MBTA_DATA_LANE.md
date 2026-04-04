# MBTA Data Lane

Transit Sentinel's first real ingest lane is MBTA.

## Official Feed Sources

Verified on April 3, 2026:

- static GTFS: `https://cdn.mbta.com/MBTA_GTFS.zip`
- vehicle positions: `https://cdn.mbta.com/realtime/VehiclePositions_enhanced.json`
- trip updates: `https://cdn.mbta.com/realtime/TripUpdates_enhanced.json`
- alerts: `https://cdn.mbta.com/realtime/Alerts_enhanced.json`

Relevant official references:

- MBTA V3 API portal: <https://api-v3.mbta.com/>
- MBTA LAMP public data: <https://performancedata.mbta.com/>
- MBTA API repository: <https://github.com/mbta/api>

## Archive Shape

The archive service writes to:

- `data/feeds/mbta/current/`
- `data/feeds/mbta/archive/YYYY/MM/DD/HHMMSSZ/`

The `current/` directory is the ingest working set for the transit runtime.
These runtime outputs are gitignored.

Each snapshot directory contains:

- realtime feed files for that capture
- per-file metadata
- one manifest for the entire capture

If the static GTFS bundle has not changed, the manifest may reuse the latest
archived static copy instead of refetching it on every snapshot.

## Run Once

```bash
PYTHONPATH=. python3 scripts/transit/archive.py --once
```

Persist the current feed set into Valkey:

```bash
PYTHONPATH=. python3 scripts/transit/ingest.py --redis redis://localhost:6379/0 --once
```

Import archived snapshots into Valkey as a replay trace:

```bash
PYTHONPATH=. python3 scripts/transit/replay.py --redis redis://localhost:6379/0 --archive-root data/feeds/mbta --trace-id mbta-proof --max-snapshots 20
```

## Run Continuously

```bash
PYTHONPATH=. python3 scripts/transit/archive.py
```

```bash
PYTHONPATH=. python3 scripts/transit/ingest.py --redis redis://localhost:6379/0
```

## Historical Corridor Report

```bash
PYTHONPATH=. python3 scripts/transit/report.py --root-dir data/feeds/mbta --max-snapshots 20
```

This summarizes which corridors repeatedly show incidents or high hazard across
archived snapshots.

## Current API Behavior

The transit API now reads from Valkey, not directly from `current/`.
That keeps dashboard requests cheap and gives the transit surface a rolling
vehicle/regime history instead of a single file-backed snapshot.
Archived MBTA snapshots can also be imported as replay traces, which show up as
`scope=replay` plus concrete `trace_id` values on the dashboard.

## Compose Runtime

```bash
docker compose -f docker-compose.transit.yml up --build
```
