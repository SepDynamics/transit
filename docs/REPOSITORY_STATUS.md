# Repository Status

## Current State

Transit Sentinel is a transit-only public-data operations product with one
primary lane:

- archive public transit feeds
- ingest them into a rolling Valkey store
- score corridor service state and operator priority
- expose incidents, maps, scorecards, public status, and replay traces through
  API and frontend surfaces
- grade archived case packs against labeled expectations and a naive baseline

## Current Value Add

The core value is not raw GTFS forwarding. Transit Sentinel turns public feed
signals into prioritized, evidence-backed operating context:

- corridor-level risk instead of feed-level status dumps
- operator-facing service-state labels instead of internal regime tokens
- recommended action tiers such as `Immediate`, `High`, `Watch`, and `Monitor`
- replayable proof windows and case-pack grading for demos and regression
- public status endpoints that can be shown without exposing the ops console

This makes the product strongest today as a public-data service-status layer
and an ops proof console. It is not yet a dispatch replacement because public
feeds do not include internal constraints such as crew, signals, or supervisor
assignments.

## Supported Runtime Surfaces

- MBTA archive lane via HTTP polling
- LA Metro rail and bus archive lanes via websocket realtime collection
- Valkey-backed live and replay state
- HTTP API for `/api/transit/*`, `/api/status/*`, `/health`, audit, and
  incident acknowledgement
- React operations console and public service-status page
- calibration tools, benchmark artifact generation, notifications, and proof
  windows
- Docker Compose services and `systemd --user` supervision assets for the live
  MBTA backend

## Committed Proof Assets

- MBTA case packs
- Los Angeles case packs
- public-data event overlays
- naive-baseline calibration path
- bundled OpenAPI endpoint index at
  `apps/frontend/public/static/transit.openapi.yaml`

## Current Known Boundaries

- LA Metro public alert coverage is still weaker than MBTA's.
- There is no Caltrans-specific adapter in the repo.
- Auth and RBAC are implemented but not required by default. Set
  `TRANSIT_API_REQUIRE_AUTH=1` and configure bearer tokens before exposing ops
  endpoints outside a trusted environment.
- The durable host runtime path targets the MBTA live lane first.
- The OpenAPI file is an endpoint index, not a fully schematized contract yet.
- Frontend polling still uses multiple interval-driven requests instead of a
  push channel or consolidated dashboard payload.
- Large archive replay imports can be slow because history writes still fan out
  across many vehicle and corridor keys.

## Highest-Value Streamlining

- Add typed response schemas to the OpenAPI contract.
- Consolidate the frontend polling path or add server push for high-frequency
  live updates.
- Batch more Valkey history writes and scorecard/trend reads.
- Add TTL or pruning rules for ephemeral vehicle, corridor, and acknowledgement
  keys.
- Expand LA Metro websocket tests and keep case packs balanced between positive
  incidents and quiet controls.
- Keep the hosted demo lane seeded and small by default, then layer live
  archive freshness on only after the seeded API/frontend path is healthy.

## Documentation Rule

Docs in this repo should describe the current transit product, current public
data lanes, and current backlog. Repo history should not be a primary
documentation theme.
