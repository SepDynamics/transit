# Roadmap

This file replaces the older status, project-outline, backlog, frontend-plan,
and 90-day planning snapshots. It should stay short, current, and Boston-only.
For investor positioning, read it with [Investor Brief](/sep/transit-sentinel/docs/INVESTOR_BRIEF.md)
and [Stack Audit - 2026-04-19](/sep/transit-sentinel/docs/STACK_AUDIT_2026-04-19.md).

## Where The Product Stands

Transit Sentinel is a working MBTA public-data transit operations platform.

The current wedge is precise: convert MBTA public GTFS and GTFS Realtime feeds
into ranked, explainable route status and operational proof. Do not dilute the
story with generic smart-city language or unsupported non-Boston claims.

Current strengths:

- live MBTA deployment at `sepdynamics.co`
- bounded Docker runtime with Valkey, archive, ingest, API, and frontend
- live health script and native Valkey history TTLs
- Docker healthchecks for Valkey, API, and frontend
- materialized live read models for scorecard, trends, dashboard, and public
  network status
- native Valkey TTLs on rolling history keys
- conditional JSON `GET` support through `ETag` / `If-None-Match`
- API parity harness for future sidecar migrations
- public MBTA status endpoints and status page
- protected operations API boundary for `/api/transit/*`
- opt-in notification dispatcher Compose profile
- React operations console with priority queue, evidence drawer, lazy-loaded
  map, history, trends, and scorecard panels
- rolling MBTA corridor and vehicle history
- replay imports and committed MBTA case-pack calibration
- optional notification and benchmark artifact tooling

Current boundaries:

- public feeds do not replace internal dispatch systems
- MBTA is the only supported live lane
- replay is disabled on the hosted live stack
- public frontend is status-only unless a protected console deployment is
  intentionally added
- scorecard reads are intentionally capped on the small live host
- route-level zero delay means no measured delay burden in the current sample,
  not guaranteed healthy service
- docs, case packs, and public claims should stay Boston-focused

## Next Sensible Work

### 1. Make The Investor Demo Repeatable

Goal: make the live MBTA proof easy to show without changing production risk.

Work:

- run `scripts/transit/live_health.py --json` before every demo
- keep the public host status-only
- keep `/api/transit/*` protected and demonstrate the `401` boundary when useful
- prepare screenshots or a short recording of the protected console from a
  controlled environment
- document the exact demo path in `docs/INVESTOR_BRIEF.md`

Exit criteria:

- a non-engineer can open the docs and run the same meeting flow
- demo claims are backed by live health output, API responses, and case packs
- no bearer token is exposed in browser-visible public config

### 2. Keep The Live Host Boring

Goal: avoid host crashes and make operational health obvious.

Work:

- keep `scripts/transit/live_health.py` as the first live-host check
- alert when Valkey memory, swap usage, 503 count, or API latency crosses a
  threshold
- keep history retention and scorecard caps explicit in docs and compose
- keep native Valkey TTLs as the primary history memory-control path
- keep `scripts/transit/prune_history.py` available only for manual recovery

Exit criteria:

- one command reports service health, memory, swap, container restarts, recent
  OOM evidence, Valkey memory, large keys, and endpoint status
- the live host can run for a week without manual memory intervention
- any future OOM has enough logs to identify the owning process quickly

### 3. Improve MBTA Read Models

Goal: keep the API responsive without relying on cold scorecard rollups.

Work:

- continue materializing live scorecard and trend payloads outside the request
  path
- serve public status scorecard from the cached Valkey key first
- keep `/api/transit/dashboard` as the default console payload
- reserve full history queries for explicit drilldowns
- add tests around read-model freshness and fallback behavior

Exit criteria:

- scorecard endpoints are fast on first request after restart
- old clients requesting large scorecard limits cannot create load spikes
- API memory stays comfortably below its container limit under burst traffic

### 4. Reduce Ingest And API Runtime Cost

Goal: lower CPU and network cost before any larger API framework migration or
agency expansion.

Work:

- use `TRANSIT_INGEST_PROFILE=1` or `--profile` to measure snapshot, write,
  read-model, and status-write cost during live ingest investigations
- avoid repeated static GTFS parsing where the current lightweight metadata is
  enough
- keep read-model materialization outside the request path
- keep conditional GET support wired through status and console payloads
- measure frontend-side ETag reuse under live traffic and tune only if needed
- use `scripts/transit/api_parity.py` before any API framework sidecar takes
  traffic
- evaluate Server-Sent Events only after measuring polling pressure
- consider FastAPI/Uvicorn only as a deliberate migration with parity tests

Exit criteria:

- repeated unchanged dashboard/status reads return `304` where applicable
- ingest has a measured baseline and one documented optimization target before
  each runtime tuning change
- polling users do not exhaust the live host request queue
- current and candidate APIs have zero parity diffs before route migration
- any API rewrite preserves auth, cache, read-model, and OpenAPI behavior

### 5. Harden The Public API Schema

Goal: make integrations safer without freezing internal experimentation.

Work:

- keep `apps/frontend/public/static/transit.openapi.yaml` aligned with current
  public status and frontend-consumed operations payloads
- keep `/api/status/*` schemas closed, required fields explicit, status
  severity enums documented, and `ETag` / `304` behavior represented
- document public `/api/status/*` schemas as the stable public contract
- keep `/api/transit/*` protected for operations use
- add versioning only where external consumers need stability

Exit criteria:

- public status endpoints have stable documented schemas
- ops endpoints can be protected without breaking the public status page
- frontend types and OpenAPI schemas do not drift silently

### 6. Grow MBTA Proof Carefully

Goal: expand evidence only where MBTA public data can support it.

Work:

- keep MBTA archive collection healthy
- turn high-signal MBTA windows into more case packs
- add more quiet controls for planned service, accessibility advisories, stop
  changes, and overnight low-service periods
- keep positive incidents and quiet controls balanced
- generate a short proof report for investor and pilot conversations

Exit criteria:

- `make check-transit-case-packs` remains a meaningful gate
- new scoring changes improve or preserve baseline comparison results
- proof artifacts can be generated quickly from committed MBTA packs

### 7. Improve The Console Without Adding Marketing Weight

Goal: keep the frontend technical, useful, and smaller to maintain.

Work:

- keep obsolete external-facing copy out of the product UI
- improve the selected-corridor evidence drawer
- keep the map bundle lazy-loaded so the initial console bundle stays small
- improve route search and grouping on the public status page
- keep polling intervals tied to operational need rather than visual motion

Exit criteria:

- users can see why a corridor was ranked without reading raw JSON
- initial page load is not dominated by map code
- public status remains readable on small screens

### 8. Choose The Commercial Wedge

Goal: turn the MBTA implementation into a focused pilot offer.

Work:

- test four offers with buyers or partners:
  - public status/API monitoring
  - agency operations triage
  - feed quality assurance
  - reliability analytics and proof reports
- pick one primary offer before broadening features
- define the pilot success metrics: feed-to-status latency, alert precision,
  uptime, operator time saved, or public information quality
- add one non-MBTA agency only after the adapter, proof data, and docs are ready

Exit criteria:

- the product can be described in one buyer-specific sentence
- the next engineering sprint maps directly to pilot evidence
- expansion beyond MBTA is driven by a buyer or proof need, not generic ambition

## Not Current Priorities

- campaign-specific copy inside the product UI
- generic audience decks inside the product UI
- claims about non-Boston coverage
- expanding functionality that increases host load before read-model caching
  and health checks stay boring under live traffic
