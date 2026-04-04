# LA Metro E Line Bus Bridge Control

This case pack reconstructs an E Line service window with a historical bus
bridge overlay and near-on-time trip updates for Friday evening, April 3, 2026.

The retained snapshot centers on `2026-04-04T02:08:00Z`, which was
approximately `19:08 PDT` in Los Angeles.

It is intentionally a control pack.

The goal is to keep planned bridge operations and published replacement-service
context from being misclassified as a live corridor failure.

## Source Provenance

- Official LA Metro rail GTFS: `https://gitlab.com/LACMTA/gtfs_rail/raw/master/gtfs_rail.zip`
- Historical E Line bus bridge timetable: `https://cdn.beta.metro.net/wp-content/uploads/2022/02/09202628/804_TT_04-09-23.pdf`
- Reconstruction metadata: [source_manifest.json](/sep/transit-sentinel/data/case-packs/la/e_line_bus_bridge_control/source_manifest.json)

## Retained Corridor

- `804` direction `0`

## Labels

- [e_line_bus_bridge_control.json](/sep/transit-sentinel/data/case-packs/la/e_line_bus_bridge_control/labels/e_line_bus_bridge_control.json)

## Run

```bash
PYTHONPATH=. python3 scripts/transit/grade_calibration.py \
  --archive-root data/case-packs/la/e_line_bus_bridge_control \
  --labels data/case-packs/la/e_line_bus_bridge_control/labels
```
