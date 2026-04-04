# Transit Sentinel Architecture

## Goal

Transit Sentinel should watch public transit operations data, detect service
instability early, and recommend operator actions before a control room or
rider-information team has to manually reconstruct the problem from raw feeds.

## Intended Runtime Shape

The architecture mirrors the source stack, but the domain changes from GPU
health to transit service health.

### Feed collector

Target inputs:

- GTFS Schedule
- GTFS Realtime vehicle positions
- GTFS Realtime trip updates
- GTFS Realtime service alerts
- optional weather and event overlays

Target outputs:

- rolling entity timelines for route, trip, block, stop cluster, terminal, and corridor
- persisted current snapshot, vehicle history, and corridor regime history in Valkey

### Regime scorer

The scorer should convert rolling windows into transit regimes such as:

- `healthy`
- `bunching_onset`
- `headway_collapse`
- `terminal_congestion`
- `stop_dwell_instability`
- `corridor_unstable`
- `service_degraded`
- `feed_incoherent`

Each regime should carry:

- hazard
- confidence
- scoring backend
- provenance
- top contributing factors

### Policy engine

The policy layer should map regimes into operator-facing incidents and actions.

Examples:

- `hold_trip`
- `short_turn`
- `dispatch_relief`
- `inspect_terminal`
- `warn_riders`
- `mark_feed_degraded`

### API and dashboard

The product surface should answer:

- which corridor or line is unstable
- where the instability started
- which trips or stops are affected
- what action is recommended
- what evidence supports the recommendation

### Replay and evaluation

Replay remains a core feature.

It should support:

- importing historical GTFS and GTFS-RT windows
- replaying known service failures
- comparing Transit Sentinel against simple baseline rules
- generating case-study summaries for proof-of-value

## Migration Note

Transit is now the default engineering lane for this repository. The legacy
cluster stack still lives under `scripts/cluster` and `tests/cluster`, but it
is maintained only as an explicit compatibility path while the transit product
replaces it.

The intended migration path is:

1. preserve the ingest -> score -> policy -> API -> dashboard pattern
2. swap the domain model from GPU entities to transit entities
3. keep replay and evaluation as first-class product features

Current transit runtime split:

- `scripts/transit/archive.py` writes raw MBTA snapshots to disk
- `scripts/transit/ingest.py` persists the current feed set into Valkey
- `scripts/transit/replay.py` imports archived snapshots into Valkey as named replay traces
- `scripts/transit/api.py` serves only `/api/transit/*`
- `scripts/cluster/api.py` remains the legacy cluster/GPU API surface
- the transit store now retains rolling vehicle history, corridor summaries, corridor regimes, incident memory, and per-trace latest replay state for trend views and replay drilldown
- transit CI and default `make check` now target `tests/transit` plus the frontend, while legacy cluster compatibility runs through explicit `check-legacy-cluster` targets
