# Calibration Use Case

## Core Proof Standard

Transit Sentinel should detect corridor instability from public data with fewer
false positives than naive threshold-only rules, while also recommending a more
useful operator action.

## Current Calibration Workflow

1. Archive one or more real transit snapshots.
2. Create label files for positive incidents and negative controls.
3. Group related snapshots and labels into a case-pack directory when you want a
   batch proof run by city, route family, or event.
4. Run calibration.
5. Compare Sentinel against the naive baseline.

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

Negative controls are supported in the same structure:

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

Grade one archive/label set:

```bash
make transit-calibration-report ARGS="--archive-root data/feeds/mbta --labels path/to/labels.json"
```

Render a markdown summary:

```bash
make transit-calibration-summary ARGS="--archive-root data/feeds/mbta --labels path/to/labels.json"
```

Grade the committed cross-city suite:

```bash
make check-transit-case-packs
```

Grade a specific committed subtree:

```bash
make transit-calibration-report ARGS="--archive-root data/case-packs/mbta --labels data/case-packs/mbta"
```

## What Counts As A Real Win

- Sentinel matches at least as many labeled incidents as the baseline.
- Sentinel produces no more extra alerts than the baseline.
- Sentinel recommends a more useful action than generic rider-warning output.
- Control packs stay quiet.
