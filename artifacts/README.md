# Artifacts

Generated benchmark and proof outputs belong here.

Current shape:

- benchmark artifacts should be written under `artifacts/benchmarks/<artifact-name>/`
- generated files in this tree are intentionally ignored by git

Primary generator:

```bash
make transit-benchmark-artifacts ARGS="--archive-root data/case-packs --labels data/case-packs --artifact-name cross-city-suite"
```
