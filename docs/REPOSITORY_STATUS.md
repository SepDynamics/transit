# Repository Status

## Current State

Transit Sentinel is now a transit-only repository with one primary product lane:

- archive public transit feeds
- ingest them into a rolling store
- score corridor service state and operator priority
- expose incidents and evidence through API and frontend surfaces
- replay archived cases and grade them against labeled expectations

## Supported Runtime Surfaces

- MBTA archive lane via HTTP polling
- LA Metro rail and bus archive lanes via websocket realtime collection
- Valkey-backed live and replay state
- HTTP API, React console, calibration tools, and notifications
- `systemd --user` supervision assets for the live MBTA backend

## Committed Proof Assets

- MBTA case packs
- Los Angeles case packs
- public-data event overlays
- naive-baseline calibration path

## Current Known Boundaries

- LA Metro public alert coverage is still weaker than MBTA's
- there is no Caltrans-specific adapter in the repo
- auth and RBAC are not yet part of the current product surface
- the durable host runtime path currently targets the MBTA live lane first

## Documentation Rule

Docs in this repo should describe the current transit product, current public
data lanes, and current backlog. Repo history should not be a primary
documentation theme.
