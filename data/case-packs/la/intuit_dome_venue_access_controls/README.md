# LA Intuit Dome Venue Access Controls

This case pack reconstructs a Friday evening Intuit Dome access window with both
Metro express bus and K Line rail service on April 3, 2026.

The retained snapshots center on `2026-04-04T02:05:00Z` for the bus shuttle and
`2026-04-04T02:02:00Z` for the rail corridor, which were approximately
`19:05 PDT` and `19:02 PDT` in Los Angeles.

It is intentionally a multi-agency control pack.

The goal is to attach venue-access context to both bus and rail corridors while
keeping scheduled event-service overlays from surfacing as incidents on their
own.

## Source Provenance

- Official LA Metro bus GTFS: `https://gitlab.com/LACMTA/gtfs_bus/raw/master/gtfs_bus.zip`
- Official LA Metro rail GTFS: `https://gitlab.com/LACMTA/gtfs_rail/raw/master/gtfs_rail.zip`
- Reconstruction metadata: [source_manifest.json](/sep/transit-sentinel/data/case-packs/la/intuit_dome_venue_access_controls/source_manifest.json)

## Retained Corridors

- `696-13201` direction `0`
- `807` direction `0`

## Labels

- [intuit_dome_venue_access_controls.json](/sep/transit-sentinel/data/case-packs/la/intuit_dome_venue_access_controls/labels/intuit_dome_venue_access_controls.json)

## Run

```bash
PYTHONPATH=. python3 scripts/transit/grade_calibration.py \
  --archive-root data/case-packs/la/intuit_dome_venue_access_controls \
  --labels data/case-packs/la/intuit_dome_venue_access_controls/labels
```
