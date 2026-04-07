# Repo Scope

## What Belongs Here

- public-transit feed archive, ingest, replay, and scoring code
- transit API and frontend code
- transit case packs, labels, and proof artifacts
- event overlays and public-data reporting helpers
- native or shared runtime code that directly supports the transit product

## What Does Not Belong Here

- unrelated infrastructure monitoring products
- placeholder docs about work that has not been implemented
- datasets that are not usable for the current transit product
- repo narratives centered on stale history rather than current behavior

## Rule

If a file does not help archive transit feeds, score service instability, expose
transit incidents, render the transit dashboard, replay public cases, or
support calibration/reporting for the transit product, it should not stay in
this repository.
