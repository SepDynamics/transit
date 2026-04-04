# Calibration Use Case

## First Valuable Use Case

The first proof-of-value target should be narrow:

- detect corridor bunching and headway collapse from official public data
- do it with fewer false positives than naive alert-or-delay thresholds
- explain the recommendation in operator language

This is the right first use case because it is:

- operationally meaningful
- visible in GTFS + GTFS-RT without requiring internal dispatch systems
- easier to prove than a full all-incidents network monitor

## Calibration Workflow

1. Archive one or more MBTA snapshots.
2. Create one or more labels JSON files for bunching, collapse, congestion, or control cases.
3. Group those files into a case-pack directory when you want a batch proof run.
4. Run the calibration report.
5. Compare Transit Sentinel against the naive baseline.

## Label Shape

```json
{
  "dataset_id": "mbta-bunching-proof",
  "use_case": "Detect bunching onset on a corridor before naive alert-or-delay thresholds would escalate it.",
  "incidents": [
    {
      "incident_id": "red-bunching-001",
      "snapshot_path": "archive/2026/04/04/010000Z",
      "route_id": "Red",
      "direction_id": 0,
      "expected_regime": "bunching_onset",
      "expected_action": "hold",
      "use_case": "corridor bunching triage",
      "note": "Three vehicles compress at the same stop cluster with growing delay."
    }
  ]
}
```

Negative control labels are also supported:

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
      "note": "A future service advisory should not create a live incident."
    }
  ]
}
```

## Commands

Build JSON report:

```bash
PYTHONPATH=. python3 scripts/transit/grade_calibration.py \
  --archive-root data/feeds/mbta \
  --labels path/to/labels.json
```

Render markdown summary:

```bash
PYTHONPATH=. python3 scripts/transit/render_calibration_summary.py \
  --archive-root data/feeds/mbta \
  --labels path/to/labels.json
```

Batch-grade a directory of MBTA case packs:

```bash
PYTHONPATH=. python3 scripts/transit/grade_calibration.py \
  --archive-root data/feeds/mbta \
  --labels path/to/case-packs
```

Run the committed real-data control suite in this repository:

```bash
PYTHONPATH=. python3 scripts/transit/grade_calibration.py \
  --archive-root data/case-packs/mbta/overnight_advisory_controls \
  --labels data/case-packs/mbta/overnight_advisory_controls/labels
```

Run the committed combined MBTA suite in this repository:

```bash
PYTHONPATH=. python3 scripts/transit/grade_calibration.py \
  --archive-root data/case-packs/mbta \
  --labels data/case-packs/mbta
```

## Proof Standard

For this use case to count as a real proof point:

- Transit Sentinel must match at least as many labeled incidents as the baseline
- Transit Sentinel must produce no more extra alerts than the baseline
- Transit Sentinel should recommend a more useful action than `warn riders`
