# Transit Case Packs

Committed documentation covers the MBTA proof packs used for Boston-focused
calibration.

- [mbta](/sep/transit-sentinel/data/case-packs/mbta/README.md)

Run the committed MBTA suite:

```bash
PYTHONPATH=. python3 scripts/transit/grade_calibration.py \
  --archive-root data/case-packs/mbta \
  --labels data/case-packs/mbta \
  --strict
```
