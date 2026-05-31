# Investor Brief

Meeting target: current investor / partner meeting

For the shortest room-ready version, start with
[Transit Sentinel Meeting One-Sheet](/sep/transit-sentinel/docs/MEETING_ONE_SHEET.md).

Transit Sentinel is a live MBTA intelligence layer. It takes public MBTA GTFS
and GTFS Realtime feeds, turns them into scored route and vehicle state, and
serves the result through a public status page, protected operations API, and
replayable proof workflow.

The current product is not a slideware prototype. It is deployed at
`https://sepdynamics.co/`, running the live MBTA stack on a small droplet, with
bounded memory, current feed ingest, Valkey read models, API health checks, and
a status-only public frontend.

## One-Sentence Pitch

Transit Sentinel turns public transit feeds into an explainable, live decision
queue: what is unstable, why it was ranked, what evidence supports it, and what
an operator or public-status surface should do next.

## Problem

Transit agencies already publish schedule, prediction, vehicle, and alert data,
but the data is fragmented and low-level. A rider app can show the next bus. A
performance dashboard can show historical reliability. Neither is designed to
answer the operational question quickly:

What deserves attention right now, and what evidence proves it?

For MBTA specifically, the public data surface is rich enough to detect useful
signals:

- static GTFS schedule and route metadata
- GTFS Realtime vehicle positions
- GTFS Realtime trip updates
- GTFS Realtime service alerts
- public feed freshness, counts, and coherence

The missing layer is the conversion from raw feed events into ranked operating
context.

## Current MBTA Implementation

Live deployment:

- public URL: `https://sepdynamics.co/`
- public API: `https://sepdynamics.co/api/status/network`
- live services: `valkey`, `archive`, `ingest`, `api`, `frontend`
- reverse proxy: Caddy to nginx frontend
- live scope: MBTA only
- replay: disabled on the public live host
- protected operations surface: `/api/transit/*` requires bearer auth

Runtime path:

```text
MBTA static GTFS + GTFS-RT
  -> archive current working set
  -> ingest and normalize
  -> score corridors, vehicles, incidents, and telemetry health
  -> persist latest state, rolling history, and read models in Valkey
  -> serve public status and protected operations APIs
  -> render public status page and private console
```

What is already implemented:

- current MBTA feed capture every 30 seconds
- live ingest every 20 seconds on the hosted stack
- route, vehicle, alert, trip-update, feed-status, regime, incident, trend, and
  scorecard payloads
- public `/api/status/*` endpoints for status page and integrations
- protected `/api/transit/*` operations endpoints
- materialized live read models for dashboard, network status, scorecard, and
  trends
- `ETag` / `If-None-Match` conditional GET support for polling efficiency
- bounded API cache, request queue, scorecard cap, and container memory limits
- replay and calibration against committed MBTA case packs
- one-command live health reporting through `scripts/transit/live_health.py`

## What It Proves Today

Transit Sentinel proves that a useful operations intelligence product can be
bootstrapped from public transit standards before requiring internal agency
integrations.

The live MBTA deployment proves:

- Public GTFS and GTFS-RT feeds are enough to create a live route-priority
  surface.
- The system can maintain current state, rolling history, and read models on a
  small host when retention and cache limits are explicit.
- Public status and private operations surfaces can share the same underlying
  scoring layer while keeping the operations API protected.
- Replay and case packs make scoring changes auditable instead of subjective.

The product boundary is also clear: public feeds cannot replace dispatch,
supervisor tooling, crew systems, signal systems, or internal incident response.
Transit Sentinel is the layer that turns public telemetry into explainable
status, prioritization, and proof.

## Value

For agencies and operators:

- Faster triage: route instability is ranked instead of buried in raw feeds.
- Explainability: each ranking carries evidence such as alerts, vehicles, trip
  updates, delay burden, bunching signals, and feed health.
- Lower adoption friction: the first deployment can run from public feeds.
- Safer public messaging: public status endpoints use rider language while the
  protected console keeps internal scoring vocabulary available.
- Operational memory: rolling history and replay create a record of what the
  system knew and when it knew it.

For riders and public information teams:

- A status surface that is more specific than a network-wide banner.
- Clear per-route status, active alerts, and freshness metadata.
- A future path to embedded status widgets, station displays, and alert
  workflows without exposing private operations endpoints.

For investors:

- The product is already deployed against a real, complex network.
- The first wedge does not depend on long agency procurement cycles or internal
  data access.
- The technical foundation is reusable across agencies that publish GTFS and
  GTFS-RT, but the current proof stays disciplined around Boston.
- The business can expand from monitoring and proof into agency operations,
  rider communications, data quality, and reliability analytics.

## Differentiation

Transit Sentinel is not trying to be a general trip planner, a replacement CAD
system, or a generic historical dashboard.

| Category | What it usually does | Transit Sentinel difference |
| --- | --- | --- |
| Rider apps such as Transit, Google Maps, and Apple Maps | Help a rider choose a trip, see ETAs, and navigate. | Produces a route-priority and evidence layer for operations, status pages, APIs, and proof. |
| MBTA public performance dashboards | Publish transparent reliability, ridership, prediction accuracy, and related metrics. | Focuses on live current-state triage and explainable route ranking. |
| Advocacy analytics such as TransitMatters dashboards | Explore performance, headways, travel times, and trends for accountability and planning. | Converts live feed state into ranked operational actions and protected API payloads. |
| Enterprise platforms such as Swiftly | Offer broad agency platforms for predictions, operations, analytics, and integrations. | Starts as a lightweight public-feed-first layer that can prove value before deep integrations and can also augment existing platforms. |
| Raw MBTA developer feeds | Publish schedule, real-time, and alert data for developers. | Normalizes, scores, caches, protects, and packages those feeds into decision-ready surfaces. |

Important distinction: public-feed-first is a wedge, not a ceiling. Internal
AVL, CAD, passenger load, dispatch, work-order, or incident systems could be
added later, but the MBTA deployment demonstrates useful value without them.

## Evidence To Show In The Meeting

Use these live checks before the investor meeting:

```bash
ssh root@161.35.226.210
cd ~/transit
PYTHONPATH=. python3 scripts/transit/live_health.py --json
curl -fsS https://sepdynamics.co/api/status/network
curl -fsS https://sepdynamics.co/api/status/routes
```

Demo sequence:

1. Open `https://sepdynamics.co/` and show the public MBTA status page.
2. Point out the network banner, active route count, priority alerts, and
   source data.
3. Show `/api/status/network` as the public integration surface.
4. Explain that `/api/transit/*` is the protected operations surface and is not
   exposed to anonymous browsers.
5. Use [the one-sheet](/sep/transit-sentinel/docs/MEETING_ONE_SHEET.md) for the
   simple comparison to `mbta.com/alerts/bus`: MBTA alerts are the bulletin
   board; Transit Sentinel is the triage desk.
6. Show this repo's MBTA case packs and calibration workflow as the proof path.
7. Show the live health report to prove this is deployed software, not a mock.

Do not position the public host as a dispatch replacement. The sharper claim is
that Transit Sentinel is an explainable intelligence and proof layer built on
top of public transit telemetry.

## Current Risks

- MBTA is the only live lane. Multi-agency expansion should wait until the
  Boston proof set and operating story are stronger.
- Public feeds cannot see internal causes such as crew availability, dispatch
  decisions, signal problems, or supervisor actions.
- Ingest CPU can spike during live parsing and scoring cycles. Memory and host
  health are stable, but profiling ingest cost should be part of the next
  engineering sprint.
- The public site is intentionally status-only. A polished private-console demo
  needs a protected deployment path or controlled local session.
- The proof corpus is still small. More positive incidents and quiet controls
  will make scoring claims more defensible.

## What To Do From Here

### Next 24 Hours

- Run `scripts/transit/live_health.py --json` on the droplet the morning of the
  meeting.
- Keep the public host status-only.
- Use the investor brief, architecture, stack audit, roadmap, and case-pack
  docs as the meeting source of truth.
- Avoid claims about non-Boston coverage until another agency is implemented
  end to end.

### Next 30 Days

- Add more MBTA case packs from real high-signal incidents and quiet control
  windows.
- Use the ingest profiler to track snapshot, write, read-model, and status
  write cost; keep reducing repeated parsing and read-model work where
  measurable.
- Produce screenshots or short videos of the public status page, protected
  console, health report, and replay workflow.
- Add a protected operator-console demo path that does not put bearer tokens in
  browser-visible public config.
- Add a small uptime and latency log so reliability claims can be backed by
  evidence instead of anecdotes.

### Next 60 Days

- Turn notifications into a demonstrable workflow: webhook, email, or proof
  window output when a corridor crosses a severity threshold.
- Keep the tightened OpenAPI contract for `/api/status/*` aligned with the
  public status payloads and conditional `ETag` behavior.
- Package a repeatable MBTA proof report from committed case packs and recent
  live windows.
- Add a second agency only if the data adapter, case packs, docs, and demo path
  are completed together.

### Next 90 Days

- Pursue one pilot with a public agency, campus transit operator, business
  improvement district, or civic-data partner.
- Decide whether the commercial wedge is status/API monitoring, agency
  operations triage, reliability analytics, or data-quality assurance.
- Add security and product basics needed for pilots: role-based login, audit
  retention policy, deployment checklist, backup policy, and incident runbook.
- Build a small benchmark showing feed-to-status latency, API latency, memory,
  and scoring stability across representative MBTA windows.

## Source References

- [MBTA V3 API Portal](https://api-v3.mbta.com/) describes the MBTA developer
  API as a schedule, real-time, and alert interface.
- [MBTA GTFS documentation](https://github.com/mbta/gtfs-documentation)
  documents MBTA static GTFS, GTFS Realtime, archives, and developer license
  resources.
- [MBTA GTFS Realtime documentation](https://github.com/mbta/gtfs-documentation/blob/master/reference/gtfs-realtime.md)
  lists MBTA service alerts, trip updates, and vehicle position feed formats.
- [MBTA Performance Metrics](https://mbta.getanchor.io/performance-metrics.html)
  documents MBTA public dashboards for reliability, ridership, customer
  satisfaction, prediction accuracy, and speed restrictions.
- [TransitMatters Data Dashboard](https://transitmatters.org/blog/2020/8/3/rolling-out-our-data-dashboard)
  is an example of public MBTA performance analysis around travel times,
  headways, dwell times, and alerts.
- [Swiftly platform](https://www.goswift.ly/platform) is an example of a broad
  enterprise transit data and operations platform.
- [Transit app](https://transitapp.com/) is an example of a rider-facing app
  centered on nearby transit, real-time tracking, trip planning, and rider
  navigation.
