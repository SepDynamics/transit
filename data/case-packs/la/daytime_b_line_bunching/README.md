# LA Metro B Line Daytime Bunching

This case pack reconstructs a weekday midday B Line compression window from the
official LA Metro rail GTFS schedule for Friday, April 3, 2026.

The retained snapshot centers on `2026-04-03T19:22:00Z`, which was
approximately `12:22 PDT` in Los Angeles.

It is intentionally a positive rail pack.

The goal is to prove that Transit Sentinel detects early bunching on a frequent
urban rail corridor before a naive alert-or-delay threshold would issue a
broader service alert.

## Source Provenance

- Official LA Metro rail GTFS: `https://gitlab.com/LACMTA/gtfs_rail/raw/master/gtfs_rail.zip`
- Reconstruction metadata: [source_manifest.json](/sep/transit-sentinel/data/case-packs/la/daytime_b_line_bunching/source_manifest.json)

## Retained Corridor

- `802` direction `1`

## Labels

- [b_line_midday_bunching.json](/sep/transit-sentinel/data/case-packs/la/daytime_b_line_bunching/labels/b_line_midday_bunching.json)

## Run

```bash
PYTHONPATH=. python3 scripts/transit/grade_calibration.py \
  --archive-root data/case-packs/la/daytime_b_line_bunching \
  --labels data/case-packs/la/daytime_b_line_bunching/labels
```
