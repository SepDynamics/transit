# Los Angeles Case Packs

Committed Los Angeles proof packs currently include:

- [daytime_b_line_bunching](/sep/transit-sentinel/data/case-packs/la/daytime_b_line_bunching/README.md)
- [e_line_bus_bridge_control](/sep/transit-sentinel/data/case-packs/la/e_line_bus_bridge_control/README.md)
- [intuit_dome_venue_access_controls](/sep/transit-sentinel/data/case-packs/la/intuit_dome_venue_access_controls/README.md)

Run the full committed Los Angeles suite:

```bash
PYTHONPATH=. python3 scripts/transit/grade_calibration.py \
  --archive-root data/case-packs/la \
  --labels data/case-packs/la \
  --strict
```
