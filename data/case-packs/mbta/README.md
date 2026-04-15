# MBTA Case Packs

Committed MBTA proof packs currently include:

- [overnight_advisory_controls](/sep/transit-sentinel/data/case-packs/mbta/overnight_advisory_controls/README.md)
- [daytime_red_line_delay_spike](/sep/transit-sentinel/data/case-packs/mbta/daytime_red_line_delay_spike/README.md)

Run the full committed MBTA suite across both packs:

```bash
PYTHONPATH=. python3 scripts/transit/grade_calibration.py \
  --archive-root data/case-packs/mbta \
  --labels data/case-packs/mbta \
  --strict
```
