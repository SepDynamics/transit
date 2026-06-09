# Transit Sentinel — Codebase Overview & Investor Readiness Plan

## Summary

Transit Sentinel is a deployed MBTA public-transit operations intelligence engine. It polls MBTA's public GTFS and GTFS-Realtime feeds, normalizes them into scored corridor and vehicle state, ranks service instability with explainable evidence, and serves results through a public status page and a protected operations API. The project runs on a small DigitalOcean droplet behind Caddy, proving that a useful operations intelligence product can be bootstrapped entirely from public transit standards before requiring internal agency integrations.

The codebase is Python 3.12 with a C++ manifold engine extension (PyBind11), a React/Vite/TypeScript frontend, Valkey (Redis) for operational memory, and Docker Compose for deployment. MBTA is the only supported live agency.

---

## Investor Readiness: Current State Assessment

### Existing Investor Materials (9 docs in `docs/`)

| File | Purpose | Status |
|------|---------|--------|
| `INVESTOR_BRIEF.md` | Meeting positioning, differentiation, risks, next steps | ✅ Strong, current |
| `MEETING_ONE_SHEET.md` | Plain-English explanation for non-technical stakeholders | ✅ Excellent, demo-path ready |
| `ARCHITECTURE.md` | Technical architecture explanation | ✅ Strong |
| `STACK_AUDIT_2026-04-19.md` | Dated audit of repo + live droplet | ⚠️ Dated April 2026 — needs refresh |
| `LIVE_DEPLOYMENT.md` | Deployment runbook | ⚠️ References the April audit |
| `ROADMAP.md` | Current state, boundaries, next work | ✅ Current |
| `DATA_AND_CALIBRATION.md` | Data pipeline and case-pack workflow | ✅ Current |
| `API_MIGRATION.md` | FastAPI sidecar migration plan | ✅ Niche but complete |
| `REPO_SCOPE.md` | What belongs in this repo | ✅ Clean, clear |

### Strengths

- **Live deployment is real**: `sepdynamics.co` serves live MBTA data. This is not a mock.
- **Differentiation is sharp**: "Public-feed-first wedge" is clearly articulated.
- **Investor story is tight**: The comparison to MBTA alerts ("bulletin board vs. triage desk") is perfect for non-technical meetings.
- **Codebase is well-structured**: Clean separation of concerns, bounded containers, healthchecks, monitoring scripts.
- **Bounded operational envelope**: Memory limits, concurrency caps, TTLs, cache sizes — everything is intentional.

### Gaps & Risks

1. **No screenshots/walkthroughs for offline use**: The meeting relies on live `sepdynamics.co`. If the site is down, there's no backup.
2. **Only one case pack has positive incidents**: `daytime_red_line_delay_spike` is the only positive detection case pack. `overnight_advisory_controls` has 3 quiet-control label sets. The proof corpus is thin.
3. **Stack audit is stale**: The April 19 audit is 7+ weeks old. Investors may ask about current state.
4. **No uptime/latency history**: No dashboard or log that shows how often the live host has been up.
5. **AGENTS.md is not investor-facing**: Contains Kilo/Codex agent tracing instructions that would confuse a reader.
6. **C++ manifold engine is unused in practice**: The `byte_stream_manifold.cpp` exists and is compiled, but the live ingest path uses the Python heuristic scorer in `domain.py`. This could create confusion if an investor sees "C++ manifold engine" in the stack and asks about it.

---

## Game Plan: 15-Step Optimization for Investor Presentation

### Phase 1: Documentation Consolidation (Days 1-2)

- [ ] **1. Consolidate docs into 3 canonical files + 1 auto-generated report**
  - Keep: `INVESTOR_BRIEF.md` (positioning), `MEETING_ONE_SHEET.md` (easy read), `ARCHITECTURE.md` (deep technical)
  - Merge `LIVE_DEPLOYMENT.md` + `STACK_AUDIT_2026-04-19.md` → single `LIVE_DEPLOYMENT.md` with embedded current audit
  - Merge `DATA_AND_CALIBRATION.md` + `ROADMAP.md` essentials into `ARCHITECTURE.md` 
  - Archive or delete: `API_MIGRATION.md`, `REPO_SCOPE.md`, `ROADMAP.md` (inline what matters)
  
- [ ] **2. Move AGENTS.md to .sixth/ or similar hidden location** — Kilo/Codex agent instructions should not be in the project root visible to investors.

- [ ] **3. Update STACK_AUDIT to current date** — Run `live_health.py --json` on the droplet and write a fresh audit to replace the April 19 one. Remove the date from the filename so it stays current.

- [ ] **4. Add demo screenshots** — Save PNGs of the public status page, protected console (from localhost), health check output, and API JSON responses to `docs/screenshots/`.

- [ ] **5. Add an uptime/health log** — The cron health check already writes to `logs/transit/live_health.jsonl`. Add a README or small script that summarizes recent uptime from those logs. Check this into `artifacts/uptime/`.

### Phase 2: Codebase Polish (Days 3-5)

- [ ] **6. Clean up AGENTS.md artifacts** — The `.codex-run/` and `.sixth/` directories and `AGENTS.md` are project-management artifacts not relevant to the product. Move or clean them.

- [ ] **7. Add a favicon** — The April audit noted 404 favicon noise in nginx logs. Tiny fix, visible polish.

- [ ] **8. Clarify the C++ manifold engine role** — Add a comment in `domain.py` and `ARCHITECTURE.md` explaining that the heuristic Python scorer is the production path and the C++ engine is available for future time-series analysis. Remove it from any "core differentiator" claims unless used.

- [ ] **9. Add a Vite build time measurement** — Make `make frontend-build` print build duration so the CI/production build can be monitored.

- [ ] **10. Set explicit Redis maxmemory** — The April audit flagged this as low priority. Set `--maxmemory 768mb --maxmemory-policy allkeys-lru` on the Valkey command to make the memory boundary explicit rather than relying on container limits alone.

### Phase 3: Investor-Story Hardening (Days 6-7)

- [ ] **11. Add at least 3 more incident case packs** — Find real MBTA high-signal windows from the feed archive (a Red Line disruption, a bus bunching event on a high-frequency route like 66 or 1, a commuter rail delay). Add them as case packs under `data/case-packs/mbta/` with labels.

- [ ] **12. Add the missing overnight quiet control** — The `overnight_advisory_controls` pack has 3 labels; ensure at least one additional quiet control for a low-service period exists.

- [ ] **13. Generate benchmark artifacts** — Run `make transit-benchmark-artifacts` and verify the output under `artifacts/benchmarks/` is clean and meaningful.

- [ ] **14. Produce a one-page "Uptime & Performance Summary"** — From the live health logs, produce a markdown table showing: daily API response times, feed freshness, container memory trends, and any incidents. Place in `docs/UPTIME_SUMMARY.md`.

- [ ] **15. Final README pass** — Ensure README.md is clean, current, and links only to the 3 canonical docs. Remove references to Kilo, Codex, or agent tracing.

---

## Architecture

### Primary Pattern: Pipeline + Read-Model Materialization

The system is a **polling pipeline** with **materialized read models** for low-latency reads:

```
MBTA GTFS / GTFS-RT → archive → ingest → Valkey (latest + history + read models) → API → frontend
```

### Major Subsystems

1. **Archive** (`scripts/transit/archive.py`): Polls MBTA HTTP endpoints (static GTFS .zip + GTFS-RT JSON feeds). Writes to `data/feeds/mbta/current/`. On the live host, skips timestamped archive dirs (`TRANSIT_ARCHIVE_CURRENT_ONLY=1`). Runs every 30s.

2. **Ingest** (`scripts/transit/ingest.py`): Reads the current feed working set, builds a snapshot via `TransitSnapshotService` (GTFS catalog loading + GTFS-RT normalization + route scoring), writes the latest state + rolling history + materialized read models to Valkey. Runs every 5-20s depending on config.

3. **Valkey Store** (`scripts/transit/store.py`): The operational memory layer. Stores latest payloads (health, entities, regimes, incidents, feed_status, errors), rolling sorted-set histories per corridor/vehicle, replay traces, and materialized read models for low-latency API reads. Includes a circuit breaker for Redis connection failures.

4. **API** (`scripts/transit/api.py`): Serves public `/api/status/*` endpoints (no auth) and protected `/api/transit/*` endpoints (bearer auth). Built on Python's `http.server.ThreadingHTTPServer` with a bounded semaphore for concurrency control, JSON response caching via `OrderedDict` LRU, and ETag-based conditional GET support. Reads from Valkey materialized read models first for common paths.

5. **Frontend** (`apps/frontend/`): React 18 / Vite / TypeScript SPA. Two views: public MBTA status page (`StatusPage.tsx`, consumes `/api/status/*`) and protected operations console (`LiveConsole.tsx`, consumes `/api/transit/*` with bearer token). MapLibre GL for the map (lazy-loaded). Uses conditional GET via `If-None-Match` for all polling.

6. **Scoring Engine** (`scripts/transit/domain.py`): The heuristic classifier that takes normalized GTFS-RT data and produces per-corridor regimes, hazard scores, actions, and incidents. Uses ~40 route metrics (delay distributions, headway compression, dwell overruns, terminal backlog, alert analysis, feed freshness) to classify into 7 regimes and recommend 6 actions.

7. **C++ Manifold Engine** (`src/core/`): A compiled PyBind11 module providing `byte_stream_manifold` and `structural_entropy` analysis. Available but not currently used in the production scoring path.

### Technology Stack

| Layer | Technology |
|-------|-----------|
| Backend runtime | Python 3.12 |
| Native extension | C++17 via PyBind11 |
| Data store | Valkey 7 / Redis 7-alpine |
| API server | Python stdlib `http.server.ThreadingHTTPServer` |
| Frontend | React 18, TypeScript, Vite 8 |
| Map rendering | MapLibre GL JS 5.x |
| Deployment | Docker Compose, Caddy reverse proxy |
| Container base | python:3.12-slim |
| Host | DigitalOcean droplet, 3.8 GB RAM, 2 vCPUs |

### Execution Flow

**Startup**: Docker Compose launches Valkey → archive → ingest → API → frontend (with healthcheck dependencies).

**Runtime loop**:
1. Archive polls MBTA feeds → writes current files → sleeps
2. Ingest reads current files → builds snapshot via TransitSnapshotService → writes latest state + history + read models to Valkey → sleeps
3. API serves from Valkey (read models for speed, fallback to cold rollups)
4. Frontend polls API every 10-30s with conditional GET

---

## Directory Structure (Annotated)

```
transit-sentinel/
├── README.md                        — Entry point, dev setup, common ops
├── AGENTS.md                        — [SHOULD MOVE] Kilo/Codex agent tracing instructions
├── Makefile                         — All operational commands
├── requirements.txt                 — Python deps (requests, redis, pytest, pybind11)
├── Dockerfile.backend               — Multi-stage Python image with C++ build
├── docker-compose.transit.yml       — Main stack (valkey, archive, ingest, api, frontend, notify)
├── docker-compose.live-host.yml     — Live host overrides (auth, caching, resource limits)
├── docker-compose.demo-host.yml     — Demo mode override
│
├── scripts/
│   ├── transit/                     — Core application code
│   │   ├── api.py                   — HTTP API server (TransitAPIService + TransitAPIHandler)
│   │   ├── ingest.py                — Feed-to-Valkey pipeline (TransitIngestService)
│   │   ├── archive.py               — MBTA feed downloader (TransitAgencyArchiveService)
│   │   ├── store.py                 — Valkey CRUD + read models (TransitStore, 1000+ lines)
│   │   ├── domain.py                — Scoring engine (TransitSnapshotService, ~1200 lines)
│   │   ├── feeds.py                 — GTFS/GTFS-RT parser + normalizer
│   │   ├── transit_types.py         — All data models (dataclasses + JSON serialization)
│   │   ├── severity.py              — Public-facing severity tiers and wording templates
│   │   ├── auth.py                  — Bearer token RBAC (viewer/operator/admin)
│   │   ├── agencies.py              — Agency adapter registry (MBTA adapter)
│   │   ├── calibration.py           — Case-pack calibration utilities
│   │   ├── case_packs.py            — Event overlay loading
│   │   ├── demo_seed.py             — Deterministic demo state seeder
│   │   ├── grade_calibration.py     — Case-pack gating (make check-transit-case-packs)
│   │   ├── replay.py                — Archive-to-Valkey replay importer
│   │   ├── live_health.py           — Host/service health reporter
│   │   ├── prune_history.py         — Manual Valkey history trimmer
│   │   ├── report.py                — Corridor report generator
│   │   ├── notify.py                — Webhook/email notification dispatcher
│   │   ├── api_parity.py            — FastAPI migration gate
│   │   ├── benchmark_artifacts.py   — Benchmark output generator
│   │   ├── proof_windows.py         — Proof window analyzer
│   │   ├── snapshot_paths.py        — Archive path utilities
│   │   └── render_calibration_summary.py — Calibration summary renderer
│   └── shared/
│       └── runtime.py               — clamp(), isoformat_ms(), scope_matches()
│
├── src/
│   └── core/                        — C++ manifold engine (PyBind11)
│       ├── bindings.cpp             — Python bindings
│       ├── byte_stream_manifold.cpp — Byte-stream manifold analysis
│       ├── structural_entropy.cpp   — Entropy computation
│       └── trajectory.h             — Trajectory types
│
├── apps/
│   └── frontend/                    — React/Vite/TypeScript SPA
│       ├── src/
│       │   ├── App.tsx              — Route switching (ops console vs status page)
│       │   ├── main.tsx             — React entry point
│       │   ├── pages/
│       │   │   ├── LiveConsole/     — Protected operations dashboard (15 panels)
│       │   │   └── StatusPage/      — Public MBTA status page
│       │   ├── hooks/
│       │   │   └── useTransitData.ts — Central polling hook (7 concurrent pollers)
│       │   ├── utils/
│       │   │   └── api.ts           — fetchJson, fetchCachedJson, ETag cache
│       │   ├── types/
│       │   │   └── transit.ts       — All TypeScript interfaces (~700 lines)
│       │   └── components/
│       └── nginx/                   — nginx config fragments
│
├── data/
│   ├── case-packs/mbta/             — Committed MBTA proof packs
│   │   ├── daytime_red_line_delay_spike/  — Positive incident (1 label)
│   │   └── overnight_advisory_controls/   — Quiet controls (3 labels)
│   └── feeds/mbta/                  — Archived feed data (gitignored)
│
├── tests/
│   └── transit/                     — Pytest test suite (18 test files)
│
├── ops/
│   └── systemd/                     — Optional systemd service units (not in use)
│
├── docs/                            — 9 markdown files (target: consolidate to 3-4)
│
└── artifacts/                       — Benchmark outputs, README
```

---

## Key Abstractions

### `TransitSnapshotService` (`scripts/transit/domain.py:106`)
- **Responsibility**: The central scoring engine. Takes GTFS static + GTFS-RT inputs and produces a complete snapshot with health, entities, regimes, and incidents.
- **Key methods**: `snapshot()` (main entry), `health()`, `entities()`, `regimes()`, `incidents()`.
- **Lifecycle**: Created per ingest service. Caches the GTFS catalog (by file mtime) and the last snapshot (for 2-120s configurable TTL).
- **Notable**: The `_build_snapshot()` method (~200 lines) orchestrates the entire pipeline: load catalog → load GTFS-RT → enrich trip updates → filter/dedupe → score routes → build health. The route scoring (`_score_routes()`) is the most complex function in the codebase (~500 lines of heuristics).

### `TransitStore` (`scripts/transit/store.py:42`)
- **Responsibility**: All Valkey CRUD operations. Manages latest-state keys, rolling history sorted sets, replay traces, and materialized read models.
- **Key methods**: `write_snapshot()`, `health()`, `entities()`, `incidents()`, `scorecard()`, `trends()`, `write_live_read_models()`.
- **Lifecycle**: Created once per service, holds a Redis client connection. Has an in-process JSON cache (thread-safe LRU with configurable TTL) and a circuit breaker for Redis failures (5 failures → 30s open circuit with half-open probing).
- **Used by**: API service, ingest service, notification service.
- **Notable**: `write_snapshot()` is the most critical write path — it uses Redis pipelines to atomically write 6 latest-state keys plus per-entity history (vehicle meta, corridor meta, observations, regimes, incidents), all in a single pipeline call. The key naming scheme uses `transit:<scope>:<kind>:<entity>` patterns.

### `TransitAPIService` (`scripts/transit/api.py:31`)
- **Responsibility**: Serves all API endpoints. Manages in-process response caching with LRU eviction.
- **Key methods**: One per endpoint: `transit_dashboard()`, `public_status_routes()`, `public_status_network()`, `transit_scorecard()`, etc.
- **Lifecycle**: Created once, lives for the server lifetime. Has a thread-safe LRU cache (`OrderedDict` + RLock) with configurable max entries and TTLs.
- **Notable**: Uses a two-tier read strategy: check materialized Valkey read model first (for "live" scope), fall back to cold rollup from store methods. This is the key performance optimization.

### `TransitAPIHandler` (`scripts/transit/api.py:567`)
- **Responsibility**: HTTP request handler. Routes by path, handles auth, serializes JSON with ETag support.
- **Notable**: Self-implemented `ThreadingHTTPServer` handler, not a framework. The `do_GET()` method is a long if/elif chain over URL paths. The concurrency model is a `BoundedSemaphore` that returns 503 `server_busy` when overloaded. CORS headers are configurable via env vars.

### `TransitIngestService` (`scripts/transit/ingest.py:40`)
- **Responsibility**: Orchestrates the periodic ingest loop. Decides when to write history and materialize read models.
- **Key methods**: `run()` (infinite loop), `run_once()` (single snapshot → store → read models → status).
- **Notable**: The `run_once()` method has optional profiling that records per-stage wall and CPU time. History writes are decoupled from the main ingest cycle via a configurable `history_interval_seconds`.

### `TransitAgencyArchiveService` (`scripts/transit/archive.py:51`)
- **Responsibility**: Polls MBTA HTTP endpoints and writes feed data to disk.
- **Key methods**: `run()` (infinite loop), `run_once()` (fetch all feeds → write current + optional archive).
- **Notable**: Static GTFS is only refreshed every 6 hours (configurable). Live-host mode skips the archive directory tree entirely. Uses atomic write patterns (`.tmp` → rename) and writes per-feed JSON metadata.

### `TransitRuntimeConfig` (`scripts/transit/domain.py:96`)
- **Responsibility**: Configuration dataclass holding feed paths, timezone, and staleness parameters for the snapshot service.
- **Where it's created**: `TransitIngestService` and `TransitSnapshotService.__init__()` both build these from env vars/CLI args/defaults.

### `TransitRealtimeBundle` / Transit data models (`scripts/transit/transit_types.py`)
- **Responsibility**: All data models as frozen dataclasses with JSON serialization. Includes `TransitVehicleObservation`, `TransitTripUpdateObservation`, `TransitAlertObservation`, `TransitRegimeRecord`, `TransitIncidentRecord`, `TransitCorridorSnapshot`, `TransitVehicleSnapshot`, and `GTFSStaticCatalog`.
- **Notable**: Every model has `to_json()` (serialize) and most have `from_mapping()` (deserialize from dict, with type coercion and defaults). This pattern ensures data integrity across the Valkey persistence boundary.

### `RouteStatus` / `severity.py` (`scripts/transit/severity.py:144`)
- **Responsibility**: Converts internal scoring signals into public-facing severity tiers (Good/Advisory/Delay/Disruption/Severe) with plain-language wording templates.
- **Key method**: `classify_severity(regime, action, hazard, active_alert_count)` — takes the regime, recommended action, hazard score, and alert count and returns a stable public severity tier.
- **Notable**: This is the public-facing surface. The wording templates explicitly avoid exposing internal vocabulary (e.g., "bunching_onset" maps to "Service advisory" with delay language).

---

## Data Flow

### Live Data Path (the production flow)

1. **Archive** (`archive.py`): Every 30s, fetches 4 MBTA feeds (static GTFS .zip, vehicle positions JSON, trip updates JSON, alerts JSON) → writes to `data/feeds/mbta/current/` with atomic writes + metadata JSON files.

2. **Ingest** (`ingest.py`): Every 20s (live host) or 5s (default), reads current feed files:
   - `TransitSnapshotService.snapshot()` builds the full payload:
     - `load_gtfs_catalog()`: Parses GTFS static .zip → in-memory catalog (routes, trips, stops, stop_times, calendar, shapes)
     - `load_gtfs_realtime_resource()` × 3: Parses vehicle positions, trip updates, alerts → `TransitRealtimeBundle`
     - `_enrich_trip_updates()`: Attaches route/direction IDs and derives delays from schedule
     - `_filter_trip_updates()`: Keeps only trip updates relevant to current vehicles or near-future schedules
     - `_dedupe_trip_updates()`: Deduplicates by vehicle ID or block ID
     - `_score_routes()`: Groups vehicles/trip_updates/alerts by route+direction → computes 40 metrics → classifies regime → assigns hazard + action → creates incidents
     - `_build_vehicle_rows()`: Attaches corridor regimes to each vehicle
     - `_build_health_payload()`: Aggregates network-level stats
   
3. **Store** (`store.py`): `TransitStore.write_snapshot()` writes to Valkey:
   - Latest-state keys (6 keys: health, entities, regimes, incidents, feed_status, errors)
   - Per-entity history (sorted sets for each vehicle observation, vehicle regime, corridor summary, corridor regime, corridor incident — trimmed to retention limit)
   - Materialized read models (scorecard, trends, dashboard, status:network) — only refreshed when history is written or on first run after restart

4. **API** (`api.py`): Serves requests:
   - Public endpoints (`/api/status/network`, `/api/status/routes`, etc.): No auth. Reads Valkey read models first for speed, falls back to cold rollups. Returns JSON with ETag.
   - Protected endpoints (`/api/transit/dashboard`, etc.): Requires bearer token with viewer role. Same read-model-first pattern.
   - Scorecard requests: Cached separately with longer TTL (60s). Large limits bypass cache.

5. **Frontend**: React app polls at intervals:
   - Dashboard: 10s (main polling loop)
   - Map, scorecard, sources: 30s
   - History (per selected entity): 30s
   - All requests use `If-None-Match` — unchanged responses return 304 and reuse cached parse result

### Scoring Path (the intelligence)

For each (route_id, direction_id) pair, `_score_routes()` computes:
- **Delay signals**: median_delay, p90_delay, delay_spread, avg_delay
- **Headway signals**: compressed_headway_share (convoy detection), scheduled_headway
- **Terminal signals**: terminal_backlog_count (vehicles delayed ≥180s near terminal)
- **Dwell signals**: dwell_overrun_share (departure delay > arrival delay by ≥90s)
- **Alert signals**: active_alert_count, high_impact_alert_count, facility_alert_count
- **Feed health**: feed_age_seconds, position_coverage, trip_update_coverage
- **Schedule context**: scheduled_service_active, low_observation, route_mode

These feed into `_classify_route()` (a ~120-line if/elif tree) producing one of:
`healthy`, `bunching_onset`, `headway_collapse`, `terminal_congestion`, `stop_dwell_instability`, `corridor_unstable`, `service_degraded`, `feed_incoherent`

Then `_recommended_action()` maps regime + metrics to: `monitor`, `hold`, `short_turn`, `dispatch_relief`, `inspect_terminal`, `warn_riders`, `mark_feed_degraded`

Then `_hazard_score()` computes a weighted composite (7 components with fixed weights) → `_operational_priority_score()` produces 0-100 → `_priority_label()` maps to Immediate/High/Watch/Monitor.

---

## Non-Obvious Behaviors & Design Decisions

### Hidden Invariants

1. **"Live" scope never uses trace_id**: If `scope=live` and a `trace_id` is provided, the API falls back to cold rollups instead of using the materialized read model. This is intentional — read models are live-only.

2. **Route-level "zero delay" ≠ healthy**: The scoring engine treats zero measured delay as "no delay burden in current sample," not as proof of health. A route can still score high due to alerts, headway compression, or bunching.

3. **History write interval decouples from ingest interval**: Ingest runs every 5-20s, but history (sorted set entries) is only written every 30-60s. This dramatically reduces Valkey write pressure while keeping the main ingest fast.

4. **Scorecard and trends are read-model materialized outside the request path**: The ingest service computes these. The API serves the pre-computed payload. This is the single most important performance design decision — without it, every API call would need to scan thousands of sorted set entries.

5. **The C++ manifold engine is not used in production**: It compiles and is importable, but `domain.py` uses pure-Python heuristics. The C++ engine was built for future time-series analysis that hasn't been implemented yet.

### State Management

- **Mutable state in Valkey only**: The API and ingest services hold no persistent mutable state. The API has an LRU cache (per-key, TTL-bounded) and the snapshot service has a GTFS catalog cache (mtime-keyed) and a snapshot cache (TTL-bounded). All can be lost on restart without data loss.
- **Valkey is the source of truth**: `write_snapshot()` uses Redis pipelines for atomicity. `write_live_read_models()` also uses pipelines. The `clear_runtime_state()` method can delete all `transit:*` keys for a clean reset.
- **No database migrations**: All data models are JSON blobs in Valkey keys. Schema changes require a clear-runtime-state or a code change that handles old-format blobs.
- **Token registry is in-memory**: `auth.py` loads `TRANSIT_API_TOKENS` at import time. Reload requires calling `reload_registry()` or restarting the API container. No token revocation without restart.

### Error Propagation

1. **Ingest errors are captured, not fatal**: `TransitSnapshotService.snapshot()` catches individual feed-load failures and appends them to an `errors` list. A feed failure doesn't crash the ingest loop — it writes partial data with error metadata.

2. **API 503 concurrency protection**: `ThreadingHTTPServer.process_request()` uses `BoundedSemaphore` (configurable via `TRANSIT_API_MAX_CONCURRENT_REQUESTS`). When saturated, it sends an immediate 503 without queueing. The request queue size is also configurable.

3. **Circuit breaker for Valkey**: `TransitStore` has a circuit breaker: 5 consecutive connection/timeout failures → 30s open circuit (all operations throw immediately). After 30s, a single request is allowed to probe recovery.

4. **Frontend degrades gracefully**: The operations console shows an error banner but doesn't crash. Map and scorecard polls are fire-and-forget — failures don't cascade to the main dashboard.

### Performance-Sensitive Paths

- **`_score_routes()` in domain.py**: ~500 lines, called once per ingest cycle. For MBTA (~200 routes), this takes ~50-200ms. The biggest cost is `_compute_route_metrics()` which iterates over vehicles and trip updates.
- **`write_snapshot()` in store.py**: Uses Redis pipelines to batch ~200+ operations (6 latest keys + per-entity history) into a single round trip.
- **`scorecard()` in store.py**: Makes 3×N pipeline calls (N = number of corridors) to fetch summary, regime, and incident history from sorted sets. Materialized read models avoid this entirely for the common case.
- **Frontend conditional GET**: The `fetchCachedJson` utility stores ETags and sends `If-None-Match` on every poll. Unchanged responses return `304` with 0-byte body, saving bandwidth.
- **MapLibre lazy loading**: The MapLibre GL JS bundle (~500KB) is code-split into a separate chunk and only loaded when the map panel mounts.

### Quirks & Surprises

- **The API is built on stdlib `http.server`**: No FastAPI, no Flask, no Django. This is intentional — minimal dependencies. The `TransitAPIHandler` implements routing, auth, CORS, caching, and ETag support entirely by hand.
- **Valkey is configured without `maxmemory` on the live host**: The April audit noted this. Container memory limits + application TTLs are the only bounds. Relying on OOM-killer as the last line of defense.
- **The public status page and operations console share the same React build**: The live host uses `OPS_CONSOLE_ENABLED=0` to hide the console navigation, but the entire console code is still shipped in the JS bundle. 
- **Auth is a simple token→role map in env vars**: No JWTs, no OAuth, no session management. `TRANSIT_API_TOKENS=readonly-token:viewer,admin-token:admin`. Rotation requires restart.
- **`Kilo.json` and `kilo.jsonc` exist but are empty config shells**: Both reference `AGENTS.md` for instructions. Not relevant to the transit product.
- **The `.mypy_cache/` directory is checked in**: The gitignore should probably exclude it, but it's present.

---

## Module Reference

| File | Purpose |
|------|---------|
| `scripts/transit/api.py` | HTTP API server — routes, caching, ETag, auth, CORS |
| `scripts/transit/ingest.py` | Periodic feed-to-Valkey pipeline with profiling |
| `scripts/transit/archive.py` | MBTA feed downloader with atomic writes |
| `scripts/transit/store.py` | Valkey CRUD — latest state, history, read models, circuit breaker |
| `scripts/transit/domain.py` | GTFS parsing, route scoring, regime classification, incident creation |
| `scripts/transit/feeds.py` | GTFS .zip and GTFS-RT JSON/protobuf parsing |
| `scripts/transit/transit_types.py` | All data models with JSON serialization |
| `scripts/transit/severity.py` | Public severity tiers and rider-facing wording |
| `scripts/transit/auth.py` | Bearer token RBAC (viewer/operator/admin) |
| `scripts/transit/agencies.py` | MBTA adapter with feed URLs and defaults |
| `scripts/transit/live_health.py` | Host/service health checker |
| `scripts/transit/replay.py` | Archive window → Valkey replay importer |
| `scripts/transit/case_packs.py` | Event overlay loading and matching |
| `scripts/transit/grade_calibration.py` | Case-pack calibration gate |
| `scripts/transit/demo_seed.py` | Deterministic demo state seeder |
| `scripts/transit/notify.py` | Webhook/email notification dispatcher |
| `scripts/transit/prune_history.py` | Manual Valkey history trimmer |
| `scripts/transit/api_parity.py` | FastAPI migration parity harness |
| `scripts/transit/benchmark_artifacts.py` | Benchmark output generator |
| `scripts/transit/calibration.py` | Case-pack scoring utilities |
| `scripts/transit/proof_windows.py` | Proof window analysis |
| `scripts/transit/report.py` | Corridor report generator |
| `scripts/transit/severity.py` | Public route status builder |
| `src/core/bindings.cpp` | C++ PyBind11 bindings for manifold engine |
| `src/core/byte_stream_manifold.cpp` | Byte-stream manifold analysis (C++) |
| `src/core/structural_entropy.cpp` | Entropy computation (C++) |
| `apps/frontend/src/pages/LiveConsole/LiveConsole.tsx` | Operations console (15 panels) |
| `apps/frontend/src/pages/StatusPage/StatusPage.tsx` | Public MBTA status page |
| `apps/frontend/src/hooks/useTransitData.ts` | Central polling hook (7 pollers) |
| `apps/frontend/src/utils/api.ts` | API client with ETag caching |
| `Dockerfile.backend` | Multi-stage Python + C++ build |
| `docker-compose.transit.yml` | Main stack definition |
| `docker-compose.live-host.yml` | Live host resource/cache overrides |

---

## Suggested Reading Order

1. **`README.md`** — Project overview, dev setup, common operations. Start here.
2. **`docs/ARCHITECTURE.md`** — System architecture and data flow. Understand the big picture.
3. **`scripts/transit/domain.py`** — The scoring engine (lines 1-300 for types and config, lines 300-800 for the snapshot build, rest for route scoring). This is the core intelligence.
4. **`scripts/transit/api.py`** — The API layer. Understanding `TransitAPIService` and the handler routing reveals the full surface area.
5. **`scripts/transit/store.py`** — Valkey operations. Understanding `write_snapshot()` and the read model pattern is key to the performance story.
6. **`apps/frontend/src/hooks/useTransitData.ts`** — The frontend polling architecture. Shows how the React app connects to the API.
7. **`scripts/transit/severity.py`** — The public-facing surface. Short and explains how internal scoring becomes rider-friendly output.
