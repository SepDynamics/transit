# MBTA Overnight Advisory Controls

This case pack captures a real MBTA snapshot taken at `2026-04-04T07:11:40.756Z`
which was approximately `03:11 EDT` in Boston on Saturday, April 4, 2026.

It is intentionally a control pack, not a disruption pack.

The goal is to prevent Transit Sentinel from escalating:

- future planned service notices
- accessibility advisories
- stop relocation notices

into live corridor incidents during an overnight lull when the feed shows no
delay burden and little or no scheduled service pressure.

## Source Provenance

- Official MBTA GTFS: `https://cdn.mbta.com/MBTA_GTFS.zip`
- Official MBTA GTFS-RT vehicle positions
- Official MBTA GTFS-RT trip updates
- Official MBTA GTFS-RT alerts
- Full captured manifest: [source_manifest.json](/sep/transit-sentinel/data/case-packs/mbta/overnight_advisory_controls/source_manifest.json)

## Retained Routes

- `15`
- `Blue`
- `Green-B`
- `Green-E`
- `Orange`
- `Red`

## Labels

- [planned_service_alert_controls.json](/sep/transit-sentinel/data/case-packs/mbta/overnight_advisory_controls/labels/planned_service_alert_controls.json)
- [accessibility_advisory_controls.json](/sep/transit-sentinel/data/case-packs/mbta/overnight_advisory_controls/labels/accessibility_advisory_controls.json)
- [stop_change_monitor_controls.json](/sep/transit-sentinel/data/case-packs/mbta/overnight_advisory_controls/labels/stop_change_monitor_controls.json)

## Run

```bash
PYTHONPATH=. python3 scripts/transit/grade_calibration.py \
  --archive-root data/case-packs/mbta/overnight_advisory_controls \
  --labels data/case-packs/mbta/overnight_advisory_controls/labels
```
