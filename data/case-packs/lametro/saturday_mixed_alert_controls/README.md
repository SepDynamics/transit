# LA Metro Saturday mixed alert controls

This case pack preserves a 20-minute sequence around 09:14 PDT on Saturday, August 1, 2026. It pairs two active service-degradation labels with four negative controls: two directions of a future Route 102 detour, a future Route 217 closure, and a Route 720 condition that remains below the escalation threshold.

The controls specifically test alert active periods. Planned notices present in a GTFS-realtime feed must not be treated as current incidents before their start time. The before-and-after snapshots retain context for later sequence analysis; strict calibration uses the center snapshot.

All inputs were promoted from a locally stored hourly bundle after SHA-256 verification. `source_manifest.json` records source and feed provenance, and `checksums.sha256` protects the promoted files. LA Metro Rail real-time endpoints returned HTTP 403 during capture, so this pack is bus-only.
