# Artifacts

Generated demo and benchmark outputs belong here.

Current Phase 1 contract:

- benchmark artifacts should be written under `artifacts/benchmarks/<artifact-name>/`
- generated files in this tree are intentionally ignored by git

Primary generator:

```bash
make transit-benchmark-artifacts ARGS="--archive-root data/case-packs --labels data/case-packs --artifact-name cross-city-suite"
```
