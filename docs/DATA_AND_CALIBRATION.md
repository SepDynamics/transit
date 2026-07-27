# Data And Calibration

Transit Sentinel should stay grounded in MBTA public data that can be
collected, replayed, and regression-tested from this repo.

## Supported Public Data

### MBTA

MBTA is the supported live lane.

Configured inputs:

- static GTFS: `https://cdn.mbta.com/MBTA_GTFS.zip`
- vehicle positions: `https://cdn.mbta.com/realtime/VehiclePositions_enhanced.json`
- trip updates: `https://cdn.mbta.com/realtime/TripUpdates_enhanced.json`
- alerts: `https://cdn.mbta.com/realtime/Alerts_enhanced.json`

MBTA is the only lane for continuous live operation, case-pack expansion,
replay imports, scorecard validation, and public status behavior.

## Archive And Replay Commands

Capture MBTA once:

```bash
make transit-mbta-archive ARGS="--once"
```

Timestamped raw GTFS-RT capture and on-disk pruning are separate, explicit
controls. Before enabling capture on a host, verify feed authorization and disk
capacity, then set both a history mode and a finite retention window. For
example, a 90-day continuous capture uses:

```bash
TRANSIT_ARCHIVE_CURRENT_ONLY=0 \
TRANSIT_ARCHIVE_RETENTION_DAYS=90 \
python3 scripts/transit/archive.py
```

`TRANSIT_ARCHIVE_RETENTION_DAYS` (CLI: `--retention-days`) has an application
fallback of `0`, which disables deletion; the standard Compose stack explicitly
sets an environment-overridable 90-day window. Pruning only runs after a
successful timestamped capture, so the live-host current-only override neither
captures nor prunes raw history. A static GTFS anchor snapshot remains
protected while any retained manifest references it, so a replayable window
can extend slightly beyond the raw cutoff by one static refresh interval.

Ingest the current MBTA working set:

```bash
make transit-ingest ARGS="--once --redis redis://localhost:6379/0"
```

Each scored history write also emits compact `sentinel.prediction_evidence.v1`
data into the durable operational JSONL record. It contains the already
filtered/deduplicated trip updates as deterministic per-stop events, including
published or schedule-plus-delay arrival/departure times, skipped stops, feed
timestamps, and source/trace metadata. It also retains each GTFS-RT trip
descriptor and its schedule relationship separately, so a canceled or deleted
trip remains replayable even when the agency publishes no stop updates.
Alternative advisories exclude canceled and deleted trips. They also fail
closed for `REPLACEMENT`, `DUPLICATED`, `NEW`, or unknown trip relationships because
those require feed-specific trip-linkage semantics that Sentinel does not
infer. Keep these partitions bounded with a matching policy:

```bash
TRANSIT_EVIDENCE_RETENTION_DAYS=90 python3 scripts/transit/ingest.py
```

`TRANSIT_EVIDENCE_RETENTION_DAYS` (CLI: `--evidence-retention-days`) also has an
application fallback of `0`; the standard Compose stack sets it to 90 days.
When enabled, pruning removes expired `agency=<key>/date=<UTC day>` partitions
after a successful evidence write. Back up any evidence that must outlive the
window before rolling out this setting: the next successful write prunes older
partitions.

Import an MBTA archive window as replay:

```bash
make transit-replay ARGS="--redis redis://localhost:6379/0 --archive-root data/feeds/mbta --trace-id mbta-proof --max-snapshots 20"
```

Seed a deterministic fallback state from MBTA case packs:

```bash
make transit-demo-seed ARGS="--redis redis://localhost:6379/0 --clear-store --replay-case-pack-catalog data/case-packs/mbta"
```

## Case Packs

Committed proof packs live under `data/case-packs/mbta/`.

They should include both:

- positive MBTA incidents that Sentinel should detect
- MBTA quiet controls that should remain quiet

Run the committed MBTA suite:

```bash
PYTHONPATH=. python3 scripts/transit/grade_calibration.py \
  --archive-root data/case-packs/mbta \
  --labels data/case-packs/mbta \
  --strict
```

Run the same suite through Make:

```bash
make check-transit-case-packs
```

Run a specific subtree:

```bash
make transit-calibration-report ARGS="--archive-root data/case-packs/mbta/daytime_red_line_delay_spike --labels data/case-packs/mbta/daytime_red_line_delay_spike/labels"
```

## Label Shape

Positive incident:

```json
{
  "dataset_id": "mbta-bunching-proof",
  "incidents": [
    {
      "incident_id": "red-bunching-001",
      "snapshot_path": "archive/2026/04/04/010000Z",
      "route_id": "Red",
      "direction_id": 0,
      "expected_regime": "bunching_onset",
      "expected_action": "hold",
      "note": "Vehicles compress at the same stop cluster with growing delay."
    }
  ]
}
```

Quiet control:

```json
{
  "dataset_id": "mbta-overnight-controls",
  "incidents": [
    {
      "incident_id": "orange-dir0-future-single-track-control",
      "snapshot_path": "archive/2026/04/04/071140Z",
      "entity_id": "route:Orange:0",
      "route_id": "Orange",
      "direction_id": 0,
      "expected_detection": false,
      "note": "A future advisory should not create a live incident."
    }
  ]
}
```

## Proof Standard

A scoring change is useful when:

- it matches at least as many labeled incidents as the naive baseline
- it creates no more extra alerts than the naive baseline
- it recommends a more useful operator action than a generic rider warning
- quiet control packs stay quiet
- API and frontend behavior remain consistent between live and replay scopes

Route-level zero delay should be interpreted as no measured delay burden in the
current sample, not as proof that every route is healthy. Alerts, bunching,
headway compression, and telemetry quality can still justify a route ranking.

## Reporting And Artifacts

Generate a corridor report:

```bash
make transit-history-report ARGS="--root-dir data/feeds/mbta --max-snapshots 20"
```

Generate MBTA benchmark artifacts:

```bash
make transit-benchmark-artifacts ARGS="--archive-root data/case-packs/mbta --labels data/case-packs/mbta --artifact-name mbta-suite"
```

Keep generated benchmark outputs under `artifacts/benchmarks/<artifact-name>/`.
