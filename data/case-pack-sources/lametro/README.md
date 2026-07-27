# LA Metro Case-Pack Sources

This uncommitted working archive receives checksum-verified hourly bundles from
the short-term droplet capture buffer. Raw bundles, extracted windows, generated
catalogs, and promoted working snapshots are ignored by Git. Only reviewed,
labeled evidence is copied into `data/case-packs/lametro/`.

- `incoming/`: verified hourly `tar.gz` bundles and SHA-256 sidecars.
- `extracted/`: temporary review and replay windows.
- `catalog/`: generated candidate indexes.
- `promoted/`: snapshots selected for case-pack curation.

Run `make transit-lametro-pull`, then `make
transit-lametro-candidate-index`. The pull refuses to run below 8 GiB free and
never prunes a remote bundle until its local copy passes checksum validation.
