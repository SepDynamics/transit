# LA Metro weekday bus instability sequence

This case pack preserves three 20-minute LA Metro bus operating sequences captured on July 30–31, 2026. The center snapshots label seven operational examples spanning terminal congestion, corridor instability, bunching onset, headway collapse, and stop-dwell instability. The snapshots ten minutes before and after each labeled instant retain nearby context for later lifecycle analysis.

All selected snapshots came from locally archived hourly bundles. Each bundle was verified against its adjacent SHA-256 file before extraction. `source_manifest.json` records the bundle digest, capture metadata, original feed status, feed URL, and per-feed digest. `checksums.sha256` protects the promoted pack files.

The pack contains complete LA Metro bus static and real-time inputs needed for deterministic replay. The source capture also attempted LA Metro Rail, but its real-time endpoints returned HTTP 403; rail observations are therefore not represented or labeled here.
