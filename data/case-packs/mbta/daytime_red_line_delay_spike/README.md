# MBTA Daytime Red Line Delay Spike

This case pack reconstructs a real MBTA daytime disruption window from official
MBTA LAMP public exports for Friday, April 3, 2026.

The retained snapshot centers on `2026-04-03T16:34:05Z`, which was
approximately `12:34 EDT` in Boston.

It is intentionally a positive pack.

The goal is to prove that Transit Sentinel recognizes a real daytime corridor
problem when trip updates show sustained service burden during active service,
not just overnight advisory noise.

## Source Provenance

- Official MBTA LAMP subway performance export for `2026-04-03`
- Official MBTA LAMP GTFS archive tables for `2026`
- Full reconstruction metadata: [source_manifest.json](/sep/transit-sentinel/data/case-packs/mbta/daytime_red_line_delay_spike/source_manifest.json)

## Retained Corridor

- `Red` direction `0`

## Snapshot Shape

- `9` trip updates
- `0` vehicle positions
- `0` alerts
- median delay `923` seconds
- max delay `1511` seconds

This pack is reconstructed from historical MBTA public exports rather than a
captured raw GTFS-RT archive. Vehicle positions and alerts are intentionally
empty because the positive proof comes from the real trip-update delay burden.

## Labels

- [red_line_midday_delay_spike.json](/sep/transit-sentinel/data/case-packs/mbta/daytime_red_line_delay_spike/labels/red_line_midday_delay_spike.json)

## Run

```bash
PYTHONPATH=. python3 scripts/transit/grade_calibration.py \
  --archive-root data/case-packs/mbta/daytime_red_line_delay_spike \
  --labels data/case-packs/mbta/daytime_red_line_delay_spike/labels \
  --strict
```
