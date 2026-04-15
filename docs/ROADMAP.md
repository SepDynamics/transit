# Roadmap

This file replaces the older status, project-outline, backlog, frontend-plan,
and 90-day planning snapshots. It should stay short and current.

## Where The Product Stands

Transit Sentinel is a working public-data transit operations platform.

Current strengths:

- live MBTA deployment at `sepdynamics.co`
- bounded Docker runtime with Valkey, archive, ingest, API, and frontend
- React operations console focused on the technical stack and live priority
  queue
- public status endpoints and status page
- rolling corridor and vehicle history
- network and corridor scorecards
- replay imports and committed case-pack calibration
- MBTA and LA Metro public-data collection paths
- optional notification and benchmark artifact tooling

Current boundaries:

- public feeds do not replace internal dispatch systems
- live public deployment is MBTA-first
- LA Metro alert coverage is weaker than MBTA
- replay is disabled on the hosted live stack
- ops auth exists but is not required by default
- scorecard reads are intentionally capped on the small live host
- no new agency should be documented as supported without code, feed config,
  and tests

## Next Sensible Work

### 1. Keep The Live Host Boring

Goal: avoid another host crash.

Work:

- add a simple memory/swap/HTTP health check script for the live host
- alert when Valkey memory, swap usage, 503 count, or API latency crosses a
  threshold
- keep history retention and scorecard caps explicit in docs and compose
- document a lightweight weekly prune/verify routine

Exit criteria:

- one command reports service health, memory, swap, and endpoint status
- the live host can run for a week without manual memory intervention
- any future OOM has enough logs to identify the owning process quickly

### 2. Precompute Heavy Read Models

Goal: keep the API responsive without relying on cold scorecard rollups.

Work:

- materialize the live scorecard during ingest or a sidecar interval
- serve public status scorecard from a cached Valkey key
- keep `/api/transit/dashboard` as the default console payload
- reserve full history queries for explicit drilldowns

Exit criteria:

- scorecard endpoints are fast on first request after restart
- old clients requesting large scorecard limits cannot create load spikes
- API memory stays comfortably below its container limit under burst traffic

### 3. Harden The Public API Schema

Goal: make integrations safer without freezing internal experimentation.

Work:

- keep `apps/frontend/public/static/transit.openapi.yaml` aligned with current
  public status and frontend-consumed operations payloads
- decide which endpoints are public, ops-only, and internal
- require auth for ops endpoints before exposing them beyond trusted users
- add versioning only where external consumers need stability

Exit criteria:

- public status endpoints have stable documented schemas
- ops endpoints can be protected without breaking the public status page
- frontend types and OpenAPI schemas do not drift silently

### 4. Improve The Console Without Adding Marketing Weight

Goal: keep the frontend technical, useful, and smaller to maintain.

Work:

- finish removing obsolete external-facing copy
- add an evidence drawer for selected corridors
- make the map bundle lazy-loaded so the initial console bundle is smaller
- improve route search and grouping on the public status page
- keep polling intervals tied to operational need rather than visual motion

Exit criteria:

- users can see why a corridor was ranked without reading raw JSON
- initial page load is not dominated by map code
- public status remains readable on small screens and large agencies

### 5. Grow Proof Carefully

Goal: expand evidence only where the data can support it.

Work:

- keep MBTA archive collection healthy
- turn high-signal MBTA windows into more case packs
- validate LA Metro websocket captures under live load
- add LA Metro case packs only after capture quality is proven
- keep positive incidents and quiet controls balanced

Exit criteria:

- `make check-transit-case-packs` remains a meaningful gate
- new scoring changes improve or preserve baseline comparison results
- proof artifacts can be generated quickly from committed packs

### 6. Expand Agency Scope Only With A Feed Interface

Goal: avoid placeholder documentation and unsupported claims.

Work:

- pick the next agency only after identifying specific public feed URLs or
  websocket interfaces
- implement adapter, archive path, ingest path, and tests together
- add at least one case pack before calling the lane supported

Exit criteria:

- supported-agency docs match implemented code
- no roadmap item depends on a generic statewide placeholder

## Not Current Priorities

- campaign-specific copy inside the product UI
- generic audience decks inside the product UI
- claims about unsupported agencies
- expanding functionality that increases host load before read-model caching
  and health checks are in place
