# Transit Case Packs

Committed transit proof packs are organized by city and event context.

- [mbta](/sep/transit-sentinel/data/case-packs/mbta/README.md)
- [la](/sep/transit-sentinel/data/case-packs/la/README.md)

Run the full committed cross-city suite:

```bash
PYTHONPATH=. python3 scripts/transit/grade_calibration.py \
  --archive-root data/case-packs \
  --labels data/case-packs \
  --strict
```
