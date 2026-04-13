Transit Sentinel — Audit And Execution Plan

Audit performed: 2026-04-07

1. Current Verified State

Transit Sentinel is a working transit operations intelligence platform with
real scoring logic, multi-agency data pipelines, and production-adjacent
frontend surfaces. It is past the prototype stage and into early-product
territory.

Verified on the current branch:

- 106 transit tests collected, all passing
- frontend TypeScript typecheck passing
- frontend production build passing
- cross-city case-pack calibration gate passing
- MBTA and LA Metro data lanes implemented and exercised

1a. What Is Implemented And Working

Backend (Python 3.12, ~10,900 LOC across 22 modules):

- MBTA live archive via HTTP polling with GTFS static + GTFS-RT
- LA Metro rail and bus live archive via websocket realtime collection
- full GTFS static parsing (routes, trips, stop_times, shapes, calendar)
- GTFS-RT ingest for vehicle positions, trip updates, and alerts
- corridor regime scoring engine with 8 regimes, 7 operator actions, hazard/confidence/provenance/priority scoring
- mode-aware thresholds (bus vs subway vs commuter rail vs ferry)
- corroboration requirements preventing single-signal false positives
- bus noise suppression for sparse low-vehicle corridors
- Valkey-backed rolling store with snapshot persistence, vehicle history, corridor history, incident memory, scorecard aggregation
- replay imports from archived snapshots into named traces
- HTTP API with 18+ endpoints covering health, entities, regimes, incidents, trends, history, sources, map, scorecard, and audit
- public status API under /api/status/* (no auth required)
- bearer token RBAC with viewer/operator/admin roles and audit trail
- incident acknowledgement with Valkey-backed persistence
- webhook, SMTP, and JSONL notification dispatch with deduplication and severity gating
- proof window capture around detected incidents
- calibration and case-pack grading with naive-baseline comparison
- benchmark artifact generation
- deterministic demo seeder from committed case packs or live archive
- severity classification for public-facing output (good/advisory/delay/disruption/severe)

Frontend (React 18, TypeScript, ~3,400 LOC):

- operations console with 9 fully functional panels:
  - hero status, toolbar with replay scope switching, overview metrics,
    corridor overview with priority sorting, map with regime-colored vehicles
    and hazard-colored corridors, trend watch, incident feed with acknowledgement,
    vehicle inventory, network and corridor scorecards
- public status page with network severity banner, per-route tiles with
  plain-language wording, active alerts feed, reliability scorecard table
- MapLibre GL map with lazy loading, auto-fit bounds, vehicle popups, legend overlay
- comprehensive TypeScript types (593 lines covering all API surfaces)
- responsive design with 3 breakpoints (1100px, 720px, 480px)
- runtime config injection for Docker deployments

Infrastructure:

- Docker compose with Valkey, archive, ingest, API, and frontend services
- multi-stage frontend Dockerfile (Node build to nginx alpine)
- Python backend Dockerfile with C++ native extension build
- systemd --user service units for durable MBTA backend supervision
- nginx production config with security headers, HSTS, CSP, gzip, caching
- GitHub Actions CI running tests, case-pack gate, frontend typecheck, and build
- Makefile with 20+ targets

1b. What Is Not Implemented

- no Caltrans or other California agency adapter
- no consumer-facing trip planning or routing product
- no WebSocket/SSE push for realtime frontend updates (polling only)
- no API versioning or pagination; the bundled OpenAPI file is an endpoint
  index, not a full typed schema contract yet
- no frontend tests
- no Python static analysis in CI
- no JWT/OAuth — only static bearer tokens
- no hosted production deployment

2. Value Proposition Assessment

2a. Core Value: What Transit Sentinel Does That Others Do Not

The scoring engine in domain.py is the primary technical differentiator. It
goes beyond raw GTFS-RT feed forwarding by:

- classifying corridor-level service regimes from vehicle telemetry, not just
  echoing agency alerts
- requiring multi-signal corroboration before escalating (delay + alerts,
  headway compression + delay, etc.)
- suppressing false positives from sparse bus data and facility-only alerts
- producing confidence and provenance metadata so consumers know why a
  classification was made and how reliable it is
- generating operator-facing priority scores with actionable tiers (Immediate,
  High, Watch, Monitor)

This is genuinely harder than threshold-based alerting on raw feeds. The
corroboration logic, mode-aware thresholds, and bus noise suppression address
real failure modes that naive systems hit immediately.

2b. Current Value Readiness

The public status layer is the most product-ready surface:

- rider-facing status page with severity-colored network banner, per-route
  tiles, alerts, and reliability scorecard
- public API that does not require authentication
- severity wording templates that produce human-readable advisory text
- works entirely from public data with no agency integration required

The ops console is the strongest proof surface:

- shows the reasoning behind public status classifications
- replay support enables incident retrospectives
- priority-ranked incident feed with acknowledgement workflow
- map visualization with regime and hazard overlays
- scorecard aggregation over rolling windows

The calibration system is the strongest evidence surface:

- case-pack grading against labeled expectations
- naive-baseline comparison for measurable improvement claims
- cross-city regression gate in CI
- benchmark artifact generation for external review

2c. Value Gaps

The product is not yet commercially deployable because:

1. No hosted demo environment exists
2. Archive depth is shallow — no 30-day continuous corpus for either agency
3. Case-pack corpus is small — not enough labeled scenarios for statistically
   significant benchmark claims
4. The scoring thresholds are hardcoded in source with no configuration
   mechanism — deploying against a new agency requires code changes
5. The public status page has no search, filtering, route grouping, or
   drill-down — it does not scale past ~20 routes
6. No external pilot or design partner engagement is captured

3. Technical Debt And Risk Assessment

3a. Critical Issues

1. API server still uses stdlib http.server. It now has a 1 MB request-size
   guard, but production deployments still need a reverse proxy or production
   server posture for connection management, timeouts, and observability.

2. Large archive replay imports still fan out into many serial Valkey history
   writes. write_snapshot now has an initial pipeline and retry wrapper, but
   vehicle/corridor history writes and scorecard/trend reads need more batching.

3. The scoring engine still has hardcoded thresholds and duplicated hazard
   weight logic. Agency-specific tuning still requires source edits.

4. The frontend still fires multiple polling requests per interval with no
   backoff, tab-visibility pause, or AbortController cancellation path.

3b. High-Priority Technical Issues

5. No TTL/expiration on ephemeral Valkey keys. Vehicle metadata, corridor
   metadata, and acknowledgement keys persist forever. Stale entities
   accumulate without bound.

6. Scorecard and trend queries issue N+1 Redis commands — one ZRANGE per
   corridor per facet. For 50 corridors, that is 150 individual commands per
   request. (store.py:466-838)

7. The OpenAPI file now covers the current endpoint list, but response schemas
   are still generic. It is not yet a stable typed public contract.

8. LA Metro websocket archive coverage exists, but it should be broadened to
   reconnect, timeout, and malformed-message behavior before relying on it as
   a continuous hosted lane.

9. Frontend tests are still absent despite useful business logic in hooks,
   formatters, and response transformations.

10. Python static analysis is still absent from CI.

11. Public status still needs search, filtering, grouping, and route drilldown
    before it scales cleanly to larger agencies.

3c. Documentation Gaps

- OpenAPI endpoint coverage exists, but typed response schemas are still missing
- No local development setup guide or CONTRIBUTING.md
- No LA Metro websocket protocol documentation

4. Product Positioning

The three product directions from the previous task.md remain valid. The
relative readiness has not changed materially.

A. Public Service-Status Layer — primary wedge

This remains the strongest near-term product. It requires only public data,
needs no agency integration, and is the easiest to demonstrate. The status
page, public API, and severity classification are all implemented.

What it needs to become commercially viable:

- hosted demo environment that stays up reliably
- route search and filtering for agencies with 50+ routes
- route grouping by mode (bus, rail, ferry)
- route detail drill-down pages
- embeddable widget or iframe-ready status tiles
- mobile-optimized layout
- branding beyond text-only footer
- auto-refresh indicator so users know data is live
- loading skeletons instead of blank states

B. Internal Control-Room Intelligence — proof and upsell surface

The ops console is fully functional and demonstrates why the scoring engine
adds value over raw feeds. Replay support, incident acknowledgement, priority
ranking, and scorecard aggregation are all working.

What it needs:

- corridor click-through on the map (currently only vehicles are clickable)
- incident timeline visualization (not just raw data tables)
- keyboard navigation for corridor/vehicle selection
- error boundaries so a single panel crash does not take down the entire page
- WebSocket push to replace 11 concurrent polling loops

Structural constraint: public data alone is not sufficient for full dispatch
workflow software. This surface will always be a decision-support tool, not a
dispatch replacement.

C. Consumer Reliability Assistant — deferred

Data signals exist (route health, incident summaries, historical scorecards)
but there is no dedicated consumer product surface. This requires routing,
distribution, personalization, and notification infrastructure that is not
the best immediate use of time.

5. What Can Be Improved From Here

5a. Highest-Impact Engineering Improvements

1. Replace http.server with a production WSGI server or put it behind a
   reverse proxy with request size limits and connection management. This is
   the single largest production safety issue.

2. Extend Redis pipeline batching beyond the latest snapshot payload into
   vehicle/corridor history writes and scorecard/trend reads. This is the
   single largest remaining performance improvement for replay imports and
   high-frequency dashboard reads.

3. Harden Redis failure handling with more coverage and fallbacks. A retry and
   circuit-breaker wrapper now exists, but error-path tests and read-path
   behavior still need attention.

4. Extract scoring thresholds into a configuration object. The existing
   TransitRuntimeConfig dataclass should be extended to include hazard
   weights, regime thresholds, incident gates, and priority tier boundaries.
   This unblocks agency-specific tuning without code changes.

5. Add TTL expiration to ephemeral Valkey keys and implement selective
   pruning of stale entities. The current approach — either accumulate
   forever or wipe everything — is a production memory leak.

5b. Highest-Impact Product Improvements

1. Hosted demo environment. This is the single most important deliverable for
   external credibility. Nothing else matters if people cannot see the product
   running.

2. Route search, filtering, and grouping on the public status page. The
   current flat list does not work for agencies with many routes.

3. Deep linking. Users cannot share a URL that points to a specific corridor,
   vehicle, or incident. This is a basic product expectation.

4. Loading states and auto-refresh indicators. The current blank-to-populated
   transition makes the product feel unfinished.

5. Archive depth. A 30-day continuous MBTA corpus and a 2-week LA Metro
   corpus are prerequisites for statistically meaningful benchmark claims.

5c. Highest-Impact Quality Improvements

1. Broaden LA Metro websocket archive tests. Basic coverage now exists, but
   reconnect, timeout, malformed-message, and longer live-window behavior are
   still significant risks.

2. Add error-path tests. The current suite tests only happy paths. No tests
   verify behavior on malformed feeds, Redis failures, network timeouts, or
   corrupt snapshots.

3. Add Python static analysis to CI (mypy or pyright). The codebase uses
   type annotations extensively but they are never checked.

4. Keep CI focused on live targets. The current workflow runs `make check`;
   avoid reintroducing dead jobs or stale non-transit targets.

5. Add frontend tests. The React frontend has zero tests despite having
   meaningful business logic in hooks, formatters, and data transformations.

6. Public Data Ceiling

This assessment has not changed. Public data is deep enough for real-time
corridor status products, rider-facing service quality layers, live route
severity surfaces, historical scorecards, and replay products. It is not
enough for dispatch-grade operating software, authoritative ETA replacement,
or internal workflow tooling tied to crew and signal systems.

Agency-specific assessment:

- MBTA: strong for both realtime and historical public-data work. Alert
  coverage is good. Feed quality is reliable.
- LA Metro: strong enough for realtime corridor monitoring. Websocket
  collection provides vehicle positions and trip updates. Public alert
  coverage is weaker than MBTA. Collection-path tests exist, but live
  reliability coverage still needs depth.

7. Execution Plan

Phase 1: Foundation Hardening (0-30 Days)

Goal: make the product safe to run continuously and demonstrable externally.

Deliver:

- keep CI green with no stale non-transit jobs
- keep Dockerfile security intact (non-root user, multi-stage build, .dockerignore)
- extend Redis pipeline batching to history writes and scorecard/trend reads
- add Redis error-path tests around retry and circuit-breaker behavior
- keep API request-size limits covered by tests
- keep nginx runtime config caching disabled
- keep frontend API error handling covered
- keep REPOSITORY_STATUS.md aligned with implemented auth/RBAC behavior
- set up a hosted demo environment
- start continuous MBTA archiving
- broaden tests for archive_ws.py

Proof gates:

- CI is fully green with no dead jobs
- demo environment is accessible and stays up over a 7-day window
- archive jobs run continuously without manual intervention
- replay import and scorecard/trend reads avoid obvious N+1 Valkey paths

Phase 2: Product Polish (30-60 Days)

Goal: make the public status page credible as a product front door.

Deliver:

- route search and filtering on the status page
- route grouping by mode
- deep linking to corridors, vehicles, and incidents
- loading skeletons and auto-refresh indicators
- error boundaries around frontend panels
- extract scoring thresholds into configuration
- expand case-pack corpus with new archive-derived scenarios
- add error-path tests for feeds, store, and API
- add Python type checking to CI
- add TTL expiration to ephemeral Valkey keys
- typed response schemas for the existing OpenAPI endpoint index

Proof gates:

- status page is usable for an agency with 50+ routes
- deep links work and are shareable
- benchmark corpus has materially more labeled scenarios
- Python type checking passes in CI

Phase 3: Evidence And Outreach (60-90 Days)

Goal: turn technical capability into investment-grade and pilot-grade evidence.

Deliver:

- expanded benchmark reports with false-positive rate and lead-time metrics
- replay-based incident retrospectives as shareable artifacts
- embeddable status widget or iframe-ready tile view
- stabilized public status API contract with versioning
- mobile-optimized status page
- first external pilot or design-partner engagement
- frontend test suite covering hooks, formatters, and critical panels

Proof gates:

- measurable false-positive suppression vs naive baseline on expanded corpus
- measurable lead-time advantage on selected incident types
- one external party has seen a live or replay demo
- public status API has a documented v1 contract

8. Objective Success Metrics

Detection lead time:
  Sentinel classification timing compared to official public disruption
  messaging where that comparison is possible.

False-positive rate:
  Sentinel vs naive threshold rules on labeled control scenarios. The
  calibration infrastructure exists — the corpus needs to be large enough
  for the measurement to be meaningful.

Public-status usefulness:
  Whether routes flagged as at-risk are followed by materially worse
  observed service within a defined time window.

Coverage and freshness:
  Feed freshness, route coverage, and archive completeness across all
  supported agency lanes.

Product reliability:
  Uptime of archive, ingest, API, and public status surfaces over rolling
  windows.

Portability:
  Time and effort required to onboard the next agency lane, measured from
  feed discovery to passing calibration gate.

9. Investment Readiness Gate

This is worth presenting as a serious investment case when the following are
all true:

- stable hosted live demo accessible externally
- stable replay demo with curated incident narratives
- 30 days of continuous MBTA archive depth
- validated LA Metro realtime archive quality with test coverage
- case-pack corpus large enough for statistically significant benchmark claims
- objective benchmark evidence showing improvement over naive baseline
- polished public service-status page with search, filtering, and deep links
- one credible pilot or design-partner conversation in progress
- critical technical debt resolved (Redis pipelines, connection handling,
  API request limits, Docker security)

10. Bottom Line

Transit Sentinel has real technical depth in its scoring engine and a
surprisingly complete product surface for its codebase size. The corroboration
logic, mode-aware thresholds, bus noise suppression, and confidence scoring
are genuine differentiators — not trivial to replicate.

The product gap is not capability. It is proof and polish.

The scoring engine works. The data pipelines work. The frontend surfaces work.
What is missing is:

- enough archived data to make benchmark claims statistically credible
- enough product polish to use the status page as a front door
- enough infrastructure hardening to run reliably in production
- enough external exposure to validate that the value proposition resonates

The near-term execution priority is:

1. harden the foundation (fix critical technical debt, set up hosted demo)
2. polish the public status surface (search, filtering, deep links, mobile)
3. deepen the evidence (archive corpus, case packs, benchmark reports)
4. get the product in front of external eyes

The question is no longer whether the scoring engine adds value over raw feeds.
The calibration system can measure that, and the corroboration logic is
demonstrably more sophisticated than threshold rules. The question is whether
the team can close the gap between working codebase and deployable product
fast enough to matter.
