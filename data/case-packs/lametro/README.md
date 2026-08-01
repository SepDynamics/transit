# LA Metro case packs

This suite contains deterministic LA Metro bus case packs promoted from the July 27–August 1, 2026 local evidence archive:

- `weekday_bus_instability_sequence`: seven positive operational labels across three 20-minute sequences.
- `saturday_mixed_alert_controls`: two active degradations and four negative controls for future alerts and monitor-only conditions.

Every source hourly bundle was checksum-verified before selective extraction. Each pack includes source provenance, per-file checksums, static GTFS anchors, and the GTFS-realtime snapshots required for offline replay.

These packs are intentionally LA Metro-only. They contain no MBTA evidence. Bus feeds were available during capture; LA Metro Rail real-time requests returned HTTP 403, so the current suite makes no rail claims.
