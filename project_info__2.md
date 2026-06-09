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

1. **Archive** (`scripts/transit/archive.py`): Polls MBTA HTTP endpoints. Writes to `data/feeds/mbta/current/`. On the live host, skips timestamped archive dirs. Runs every 30s.

2. **Ingest** (`scripts/transit/ingest.py`): Reads current feed working set, builds a snapshot via `TransitSnapshotService`, writes latest state + rolling history + materialized read models to Valkey. Runs every 5-20s.

3. **Valkey Store** (`scripts/transit/store.py`): Operational memory layer. Stores latest payloads, rolling sorted-set histories, replay traces, and materialized read models. Includes circuit breaker.

4. **API** (`scripts/transit/api.py`): Public `/api/status/*` and protected `/api/transit/*` endpoints. Python stdlib `ThreadingHTTPServer` with bounded semaphore, JSON caching, ETag support.

5. **Frontend** (`apps/frontend/`): React 18 / Vite / TypeScript SPA. Public status page + protected operations console. MapLibre GL for maps (lazy-loaded).

6. **Scoring Engine** (`scripts/transit/domain.py`): Heuristic classifier using ~40 route metrics, producing 7 regimes and 6 actions. The core intelligence.

7. **C++ Manifold Engine** (`src/core/`): Compiled PyBind11 module. Available but not used in production scoring path.

---

## Key Abstractions

### `TransitSnapshotService` — The scoring engine
File: `scripts/transit/domain.py:106`. Takes GTFS + GTFS-RT → produces complete snapshot. The `_score_routes()` method is ~500 lines of heuristics. Caches GTFS catalog by file mtime and snapshot by configurable TTL.

### `TransitStore` — Valkey operations
File: `scripts/transit/store.py:42`. All CRUD. Uses Redis pipelines for atomic writes. In-process JSON LRU cache. Circuit breaker: 5 failures → 30s open circuit with half-open probing.

### `TransitAPIService` — API business logic
File: `scripts/transit/api.py:31`. Two-tier read strategy: materialized Valkey read model first, cold rollup fallback. Thread-safe LRU cache with `OrderedDict` + RLock.

### `TransitAPIHandler` — HTTP handler
File: `scripts/transit/api.py:567`. Self-implemented. Routes by path chain, `BoundedSemaphore` for concurrency (503 on overload), full CORS + ETag support.

### Data Flow (Production Path)

1. **Archive**: Every 30s fetches 4 MBTA feeds → writes to disk
2. **Ingest**: Every 20s reads feeds → builds snapshot (GTFS catalog → GTFS-RT normalization → enrichment → filtering → dedup → scoring → incident creation) → writes to Valkey (6 latest keys + per-entity history sorted sets + 4 materialized read models)
3. **API**: Reads read models first for speed → serves JSON with ETag
4. **Frontend**: Polls every 10s (dashboard) / 30s (map, scorecard) with `If-None-Match`

---

## Non-Obvious Behaviors

- **"Live" scope never uses trace_id**: If `scope=live` and `trace_id` is given, the API falls back to cold rollups instead of using read models
- **Zero delay ≠ healthy**: Routes can still rank high due to alerts, bunching, or telemetry quality
- **History writes decoupled from ingest**: Ingest runs every 5-20s, history writes every 30-60s — reduces Valkey pressure
- **Scorecard/trends pre-computed by ingest**: Not computed at request time — this is the key performance optimization
- **C++ engine is not production**: It compiles but `domain.py` uses pure Python heuristics. The engine was built for future time-series analysis
- **API uses stdlib http.server**: No FastAPI, no Flask. Intentional minimal dependency choice
- **Valkey has no explicit maxmemory**: Container limits + TTLs only. April audit flagged this as low priority
- **Auth is simple token→role map**: No JWTs, OAuth, or sessions. `TRANSIT_API_TOKENS=readonly-token:viewer,admin-token:admin`

---

## Suggested Reading Order

1. **`README.md`** — Project overview, dev setup, common operations
2. **`docs/ARCHITECTURE.md`** — System architecture and data flow
3. **`scripts/transit/domain.py`** — The scoring engine. Lines 1-300 for types/config, 300-800 for snapshot build, rest for route scoring
4. **`scripts/transit/api.py`** — The API layer. `TransitAPIService` + handler routing
5. **`scripts/transit/store.py`** — Valkey operations. `write_snapshot()` and read model pattern
6. **`apps/frontend/src/hooks/useTransitData.ts`** — Frontend polling architecture
7. **`scripts/transit/severity.py`** — Public-facing surface. Short. Explains internal→public translation

---

The full analysis with all 15 checklist items and detailed codebase documentation has been saved to `project_info__1.md` in the project root. You can switch to **Act Mode** using the mode selector at the bottom of the chat to start executing the game plan — your exploration findings will carry over as context.