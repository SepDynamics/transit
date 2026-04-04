# Repo Scope

This repository is the transit adaptation branch of the original stack.

## Keep

- the ingest -> score -> policy -> API -> dashboard pattern
- replay and evaluation tooling
- reusable structural scoring ideas
- native/core experimentation that still helps the transit product

## Remove Over Time

- GPU-specific terminology
- cluster/node/GPU product language
- dataset importers that only exist for the source application
- evaluation fixtures that are not relevant to transit

## Add

- GTFS and GTFS-RT ingestion
- transit entity models
- transit incident evaluation fixtures
- transit-specific frontend views
- public transit proof bundles

## Rule

If a file does not help ingest transit feeds, score service instability, expose
transit incidents, render the transit dashboard, replay public cases, or support
evaluation of the transit product, it should not stay in this repo.
