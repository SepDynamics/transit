# Meeting Preparation: LA Metro & Charlotte Metro

**Date:** July 2026 | **Product:** Transit Sentinel | **Prepared by:** SepDynamics

---

## Table of Contents

1. [Meeting Objective & Strategy](#1-meeting-objective--strategy)
2. [What Transit Sentinel Actually Is (30-Second Version)](#2-what-transit-sentinel-actually-is)
3. [LA Metro — Current State & Analysis](#3-la-metro--current-state)
4. [Charlotte Metro (CATS) — Current State & Analysis](#4-charlotte-metro-cats)
5. [CRITICAL: Feed Research — LA Metro & CATS Public GTFS-RT](#5-critical-feed-research)
6. [What Transit Sentinel Can Do for Each Agency](#6-what-transit-sentinel-can-do)
7. [How We're Different & Better Than What They Have](#7-how-were-different)
8. [Complement vs. Replace — The Real Answer](#8-complement-vs-replace)
9. [Evidence Wall — What We Can Prove Today](#9-evidence-wall)
10. [Objection Handling](#10-objection-handling)
11. [Demo Path for This Meeting](#11-demo-path)
12. [Proposed Next Steps / Ask](#12-proposed-next-steps)

---

## 1. Meeting Objective & Strategy

**Primary goal:** Get a follow-up — a technical demo, a data-sharing conversation, or a pilot scoping call.

**Strategy:** Lead with understanding of their world, then show how Transit Sentinel fills a gap their current systems leave open.

**The framing that works for both agencies:**

> "You're investing heavily in operations infrastructure — CAD/AVL, dispatch consoles, control centers. Those systems tell you **where your vehicles are** and **whether they're on schedule**. Transit Sentinel is the layer that tells you **what deserves attention right now** and **why** — turning raw telemetry into a ranked, explainable decision queue."

**Do NOT lead with:**
- "We replace your CAD/AVL system" (we don't, and they'll know)
- "We're like Swiftly/Transit/Google Maps" (we're not rider-facing in the same way)
- "We built this for MBTA" without immediately explaining why it applies to them

**DO lead with:**
- Their public GTFS-RT feeds are already rich enough to detect meaningful signals
- We can demonstrate value from public data alone, before any internal integration
- The architecture is lightweight — we've been running on a single 2-vCPU droplet for months

---

## 2. What Transit Sentinel Actually Is

### The One-Line Description

> Transit Sentinel turns public transit feeds into a ranked, explainable decision queue: what's unstable, why it was ranked, what evidence supports it, and what an operator should do next.

### The Analogy That Lands

> **MBTA/CATS/LA Metro alerts are the bulletin board. Transit Sentinel is the triage desk.**

Agencies publish alerts, vehicle positions, trip updates, and schedules as public data. What's missing is the layer that:
1. **Fuses** all those signals together (alerts + delays + headways + telemetry health)
2. **Ranks** routes by operational priority, not just alphabetically
3. **Explains** *why* a route deserves attention — with evidence, not just a score
4. **Remembers** — rolling history and replayable proof so you can analyze what happened

### What It Is Not

- **Not a CAD/AVL replacement** — we don't dispatch vehicles, manage crews, or handle signals
- **Not a rider app** — though we have a public status page, the real value is the operations console and API
- **Not a generic dashboard** — the scoring engine is purpose-built for transit operations patterns

### Current Deployment

| Metric | Status |
|--------|--------|
| Live URL | `https://sepdynamics.co/` |
| Agency | MBTA (Boston) |
| Runtime | Single DigitalOcean droplet, 2 vCPU, 3.8 GB RAM |
| Containers | valkey, archive, ingest, api, frontend |
| Uptime | Months of continuous operation |
| Public API | `/api/status/*` — open to all |
| Ops API | `/api/transit/*` — bearer auth protected |
| Feed freshness | Ingest every 20s, archive every 30s |
| Routes tracked | 192+ active routes per cycle |

---

## 3. LA Metro — Current State

### What We Know

**Public Data Posture:**
- Publishes static GTFS via developer.metro.net and the regional GTFS repo on GitHub
- Publishes GTFS-RT through **Swiftly's API** (api.goswift.ly) — requires API key registration at goswift.ly/realtime-api-key
- Runs the Metro API v2 at api.metro.net (FastAPI-based) providing JSON access to static data and a WebSocket for real-time pass-through
- Participates in Cal-ITP and California GTFS data standardization
- Regional GTFS consolidation via LACMTA/los-angeles-regional-gtfs on GitHub

**Operations Infrastructure (Critical Context):**
- **November 2025: Awarded ATMS II contract to Clever Devices** — a landmark program
- This implements **CAD/AVL across bus AND rail for the first time** (previously only bus had CAD/AVL)
- Includes: CleverCAD dispatch platform, yard management system, new data center
- Current Bus Operations Control (BOC) center is being maintained during the transition
- Multi-year rollout — ATMS II is in early-to-mid deployment phase as of mid-2026

**System Scale:**
- 2nd largest transit agency in the US (after NYC MTA)
- ~1M+ weekday boardings
- ~2,000+ buses, ~200+ rail vehicles
- Bus, light rail (A/B/C/E/K lines), subway (B/D lines)

### What They Probably Have (Inferred)

| Function | Current System | Notes |
|----------|---------------|-------|
| Bus CAD/AVL | Legacy (vendor unknown) | Being replaced by ATMS II |
| Rail operations | Separate from bus CAD/AVL | ATMS II will unify for first time |
| Dispatch console | Bus Operations Control (BOC) center | Transitioning to CleverCAD |
| Public realtime data | Swiftly → Metro API v2 | Requires API key registration |
| Performance dashboards | Internal, likely Clever Devices or custom | May not show cross-system triage |
| Alert publishing | Metro API | Likely not correlated with telemetry |

### The Gaps ATMS II Won't Fill

Clever Devices' CleverCAD is excellent at what it does — vehicle tracking, schedule adherence, dispatch communications, yard management. But enterprise CAD/AVL systems consistently have blind spots:

1. **Cross-corridor ranking** — CAD systems show individual route status. No system currently tells an operator "here are the 8 routes that need your attention right now, ranked by severity with evidence."
2. **Explainable scoring** — CAD/AVL generates raw data (vehicle lat/lon, schedule deviation). It doesn't produce an *explainable decision queue* with provenance.
3. **Public-feed-first value** — The ATMS II rollout will take years. Transit Sentinel can demonstrate value *today* using the public feeds LA Metro already publishes.
4. **Replayable proof** — When something goes wrong, CAD systems don't easily answer "show me exactly what the system knew at 3:15 PM on Tuesday."

---

## 4. Charlotte Metro (CATS) — Current State

### What We Know

**Public Data Posture:**
- Publishes GTFS schedule feed (via Transitland, 64+ archived versions)
- Publishes GTFS-RT (vehicle positions confirmed, trip updates likely, alerts likely)
- Open data portal through City of Charlotte
- GTFS Scorecard grade available — room for improvement on feed quality

**System:**
- Bus + LYNX Blue Line light rail + CityLYNX Gold Line streetcar
- ~15-20 million annual boardings
- ~300+ buses, ~40 light rail vehicles

**Operations Infrastructure (Less Certain):**
- Likely has a CAD/AVL system (many small-to-mid-size agencies use INIT, Conduent, or Clever Devices)
- May have less sophisticated analytics tooling than larger agencies

### The CATS Opportunity

CATS is the ideal "wedge" agency for Transit Sentinel because:
1. **Lower complexity** — fewer routes means faster onboarding
2. **Public GTFS-RT already exists** — we can be live in days
3. **Feed quality improvement** — our feed-quality endpoint alone provides value
4. **Budget sensitivity** — lightweight deployment is economical vs. enterprise CAD/AVL
5. **Growth trajectory** — Charlotte is growing fast; analytics needs will grow with it

---

## 5. CRITICAL: Feed Research — LA Metro & CATS Public GTFS-RT

Six months ago, you found LA Metro's feeds seemed sparse. Here's the actual picture after deep investigation:

### LA Metro GTFS-RT Feeds

LA Metro's GTFS-RT feeds are **behind Swiftly's API**, which requires registration for an API key. They are NOT open, free, anonymous endpoints like MBTA's.

| Feed | URL | Auth Required | Format |
|------|-----|---------------|--------|
| **Bus Vehicle Positions** | `https://api.goswift.ly/real-time/lametro/gtfs-rt-vehicle-positions` | Yes — Swiftly API key (HTTP header) | Protobuf |
| **Bus Trip Updates** | `https://api.goswift.ly/real-time/lametro/gtfs-rt-trip-updates` | Yes — Swiftly API key (HTTP header) | Protobuf |
| **Rail Vehicle Positions** | `https://api.goswift.ly/real-time/lametro-rail/gtfs-rt-vehicle-positions` | Yes — Swiftly API key (HTTP header) | Protobuf |
| **Rail Trip Updates** | `https://api.goswift.ly/real-time/lametro-rail/gtfs-rt-trip-updates` | Yes — Swiftly API key (HTTP header) | Protobuf |
| **Alerts** | Not clearly documented as public | Unknown | Unknown |

**Swiftly API key registration:** Free tier available at `https://goswift.ly/realtime-api-key` — requires name, email, and agreement to terms.

**Metro API v2** (`api.metro.net`) provides:
- A WebSocket passthrough to Swiftly real-time data (`ws://api.metro.net/ws/LACMTA/vehicle_positions/` or `/ws/LACMTA/trip_updates/`)
- JSON REST endpoints for static GTFS data (routes, stops, stop_times, trips, shapes)
- Static data uses agency_id values: `"LACMTA"` (bus) and `"LACMTA_Rail"` (rail)
- Canceled service data endpoint

**Static GTFS:**
- Available from the los-angeles-regional-gtfs GitHub repo (LACMTA hosts for multiple regional agencies)
- Also available through developer.metro.net
- Over 100 archived versions on Transitland

**Cal-ITP quality reports** show LA Metro (City of Los Angeles) publishes all three GTFS-RT feeds (vehicle positions, trip updates, service alerts) with vehicle positions completeness typically at 50-80% of scheduled trips.

### Charlotte CATS GTFS-RT Feeds

CATS publishes GTFS-RT feeds. The exact endpoints are less well-documented but:
- GTFS schedule: available through Transitland (64+ versions)
- GTFS-RT: vehicle positions confirmed in Mobility Database
- Alert and trip update feeds likely available but need endpoint confirmation

### Transit Sentinel Compatibility

Our feed parsing code (`scripts/transit/feeds.py`) handles:
- ✅ **JSON GTFS-RT** (MBTA's format) — works natively
- ✅ **Protobuf GTFS-RT** — via `google.transit.gtfs_realtime_pb2` (already imported)
- ✅ **HTTP feed fetching** — via `requests` library
- ✅ **Authorization headers** — already supported in the codebase
- ✅ **CamelCase + snake_case field mapping** — the parser handles both naming conventions

**Bottom line:** We can consume LA Metro's feeds with **one requirement**: we need a Swiftly API key. The code already supports protobuf parsing, auth headers, and the field naming. We don't need a Metro-specific API adapter — the existing agency adapter pattern handles this with configuration.

For CATS: likely same compatibility, just need the actual feed URLs.

### What This Means for the Demo

| Scenario | Feasibility | What It Takes |
|----------|-------------|---------------|
| **Point Transit Sentinel at LA Metro public feeds TODAY** | ✅ Feasible with one sign-up | Register for Swiftly API key (free, online), add LA Metro agency adapter, deploy |
| **Demonstrate on MBTA, explain LA Metro compatibility** | ✅ Works now | The live site is already running |
| **Use LA Metro's Metro API v2 as alternative** | ⚠️ Partial | Static data is available via REST JSON, real-time is WebSocket-only which would need an adapter |
| **CATS live demo** | ✅ Feasible with their feed URLs | Need to confirm CATS feed endpoints, add agency adapter |
| **LADOT bus as backup LA demo option** | ✅ Feasible | LADOT publishes open GTFS-RT at ladotbus.com with NO API key required |

### Recommended Approach for the Meeting

When they say "show us on our data," the answer is:

> "Your GTFS-RT feeds are managed through Swiftly. We need an API key from Swiftly (which is a free registration), and we can be ingesting LA Metro data within a week. In the meantime, let's show you what Transit Sentinel looks like on Boston, and then we can discuss the timeline for LA."

For the pitch — **this is actually a feature, not a bug**: If we can demonstrate value from a Swiftly-managed feed, we're proving we can coexist with and add value on top of whatever realtime data platform they use. Swiftly is a data provider; Transit Sentinel is the intelligence layer on top.

---

## 6. What Transit Sentinel Can Do for Each Agency

### For LA Metro

| Capability | Value Proposition |
|-----------|------------------|
| **Live route triage** | From LA Metro's existing GTFS-RT feeds (via Swiftly), produce a ranked queue of routes needing attention — with evidence |
| **Cross-modal ranking** | Bus, light rail, subway — all scored on the same priority scale |
| **Feed quality monitoring** | Continuously monitor LA Metro's own public feed health — freshness, coverage, completeness |
| **Incident evidence preservation** | Rolling history + replay captures exactly what the system knew during any service event |
| **Public status API** | `/api/status/*` endpoints LA Metro could offer to third-party developers |
| **ATMS II augmentation** | Once ATMS II/CleverCAD is live, Transit Sentinel can ingest richer internal data |
| **Calibration against real events** | Case packs let LA Metro validate scoring changes without regressions |

### For Charlotte CATS

| Capability | Value Proposition |
|-----------|------------------|
| **Triage console** | Immediate access to an operations dashboard with ranked route priority, live map, trend analysis |
| **Feed quality improvement** | Continuous monitoring of GTFS-RT feed health |
| **Status page** | A public MBTA-quality status page for CATS riders |
| **Performance scorecard** | Automated reliability reporting without manual spreadsheet work |
| **Low-cost deployment** | Can be live for a fraction of what enterprise CAD/AVL upgrades cost |

---

## 7. How We're Different & Better Than What They Have

### The Core Differentiation Matrix

| What They Have | What It Does | What's Missing | What Transit Sentinel Adds |
|----------------|-------------|----------------|---------------------------|
| **CleverCAD / CAD-AVL** | Vehicle tracking, schedule adherence, dispatch | Cross-corridor ranking, explainable scoring, evidence preservation | The "triage desk" on top |
| **Swiftly API / Metro API v2** | Raw GTFS-RT data delivery | Normalization, scoring, caching, prioritization, auth separation | Turns raw feeds into decision-ready surfaces |
| **Operations control center** | Human dispatchers monitoring screens | Automated triage when the operator is elsewhere | Always-on scoring queue |
| **Performance dashboards** | Historical reliability metrics | Live current-state triage with ranked urgency | "What needs attention *right now*" |
| **Rider apps (Transit, Google Maps)** | Trip planning, ETAs | Operations context, evidence, API products for agencies | Built for operators, not riders |

### The Visual Metaphor

```
YOUR CAD/AVL SYSTEM                    TRANSIT SENTINEL
┌─────────────────────┐                ┌─────────────────────┐
│  Vehicle #1432      │                │  #1 Red Line        │
│    Lat: 34.0522     │                │    Hazard: 0.81     │
│    Lon: -118.2437   │                │    Evidence:        │
│    Status: On-time  │                │      - 14 min delay │
│                     │                │      - 3 high-impact│
│  Vehicle #1851      │                │        alerts       │
│    Lat: 34.0519     │                │      - Headway      │
│    Status: +3 min   │                │        collapsed    │
└─────────────────────┘                │    Action: Dispatch │
   Raw telemetry                       │      relief         │
                                       └─────────────────────┘
                                        Ranked, explainable decision queue
```

### The Technical Differentiators

1. **Public-feed-first deployment** — we work from the data they already publish through Swiftly. No procurement, no data-sharing agreement needed to show value.

2. **Scoring with provenance** — every route ranking carries an evidence trail with hazard components, not an opaque score.

3. **Public/API separation** — `/api/status/*` and `/api/transit/*` share the same engine but have different auth, vocabulary, caching.

4. **Replay and case packs** — prove a scoring change is better by replaying against historical labeled incidents.

5. **Performance-engineered for small hosts** — materialized read models, Redis pipelines, ETag caching, bounded resource limits.

---

## 8. Complement vs. Replace — The Real Answer

### LA Metro: Complement ATMS II, Not Replace It

**The honest answer:** LA Metro just awarded a landmark ATMS II contract to Clever Devices. They are not replacing it. But ATMS II leaves a specific blind spot open.

> ATMS II gives LA Metro world-class CAD/AVL — vehicle tracking, dispatch, yard management. What it **doesn't** provide is a cross-corridor triage and explainability layer that answers "what deserves attention right now, and why?"

**Three-phase approach:**

| Phase | What | How |
|-------|------|-----|
| **Phase 1: Public-feed demo** | Via Swiftly API key | "Here's what your own data says about your system right now." |
| **Phase 2: Coexist with ATMS II** | During ATMS II rollout | Transit Sentinel adds the triage/explainability layer on top |
| **Phase 3: Richer ops data** | Post-ATMS II integration | If LA Metro exposes internal CAD data, scoring becomes more accurate |

### Charlotte CATS: Complement or Near-Replace for Lightweight Analytics

CATS is smaller. Transit Sentinel could functionally replace the *analytics and triage* portion of what an enterprise system would provide, without replacing dispatch or vehicle tracking.

---

## 9. Evidence Wall — What We Can Prove Today

### From the Live MBTA Deployment

| Claim | Evidence |
|-------|----------|
| We run on a single small droplet | DigitalOcean: 2 vCPU, 3.8 GB RAM, 77 GB disk |
| We handle 192+ routes per cycle | Live `/api/status/network` shows active route count |
| Feed freshness is maintained | Ingest every 20s, archive every 30s |
| Public API is open and documented | `sepdynamics.co/api/status/network`, OpenAPI spec available |
| Ops API is properly protected | `/api/transit/*` returns 401 without bearer token |
| Scoring produces explainable output | Each regime record includes provenance factors, hazard components, reasons list |
| Conditional GET reduces bandwidth | All endpoints support ETag / If-None-Match |
| Case packs prove regression safety | 6 case packs with 16 labels under `data/case-packs/mbta/` |
| Benchmark suite exists | `make transit-benchmark-artifacts` generates reproducible reports |
| Live health monitoring works | `scripts/transit/live_health.py` runs on cron every 15 minutes |

### From the Codebase

| Asset | Location | What It Shows |
|-------|----------|---------------|
| Scoring engine | `scripts/transit/domain.py` | 40+ route metrics, 7 regime classifiers, 6 action recommendations |
| Public severity translation | `scripts/transit/severity.py` | Internal scoring → public-friendly severity tiers |
| Agency adapter pattern | `scripts/transit/agencies.py` | Adding a new agency is ~20 lines |
| Feed parsing | `scripts/transit/feeds.py` | Supports JSON + Protobuf GTFS-RT, auth headers, camelCase + snake_case |
| Materialized read models | `scripts/transit/store.py` | Scorecard, trends, dashboard, network status pre-computed |
| API surface | `scripts/transit/api.py` | 20+ endpoints with caching, ETag, bounded concurrency |
| Frontend | `apps/frontend/` | React/TypeScript with lazy-loaded MapLibre map |
| Tests | 18 test files | API, archive, auth, calibration, domain, feeds, ingest, replay, store, severity |

---

## 10. Objection Handling

### "We just awarded ATMS II to Clever Devices. Why would we need this?"

**Response:** "ATMS II is exactly why you should talk to us. You're making a major investment in CAD/AVL infrastructure. But CAD/AVL systems don't produce a ranked, explainable triage queue across all modes. Transit Sentinel is the layer that turns that rich ATMS II data into an intelligence surface. And we can prove the value from your Swiftly feeds before ATMS II is even fully deployed."

### "Your MBTA demo doesn't prove it works for LA."

**Response:** "Actually, the feed format difference is the main thing. MBTA publishes JSON GTFS-RT with no auth. LA Metro publishes protobuf GTFS-RT through Swiftly with API key auth. Our code handles both — we already have protobuf parsing and auth header support built in. The agency adapter pattern means adding LA Metro is a configuration change, not a code rewrite. We can be ingesting your feeds within a week of getting a Swiftly API key."

### "Our feeds require a Swiftly API key."

**Response:** "That's fine. Swiftly offers free API key registration at goswift.ly. We already support HTTP auth headers in our feed fetching. Once we have a key, we're live. If anything, this is a positive: it proves we can integrate with data platforms like Swiftly, which many agencies use."

### "We already have internal dashboards."

**Response:** "Do your dashboards rank routes by cross-system priority with explainable evidence? Do they separate public-facing status from internal operations vocabulary? If the answer to either is no, that's the gap we fill."

### "Charlotte is too small for this."

**Response:** "Smaller agencies benefit *more* from automated triage, because you have fewer operators monitoring more routes. Our deployment model scales down — the same architecture that runs MBTA on a 2-vCPU droplet runs even more efficiently on a smaller footprint."

### "How is this different from Swiftly?"

**Response:** "Swiftly is a data delivery platform — they provide the GTFS-RT feed pipeline. Transit Sentinel is the intelligence layer that consumes that data and produces ranked, explainable triage. We're complementary to Swiftly, not competitive. Your Swiftly feed is actually the input we'd use."

---

## 11. Demo Path

### For This Meeting

1. **Open `https://sepdynamics.co/`** — "This is the MBTA public status page. Everything you see comes from public GTFS-RT feeds."
2. **Point to the network banner** — "Live network severity, route count, disruption count, feed freshness."
3. **Click "Show map"** — "300+ vehicles, color-coded by service health."
4. **Show "What Needs Attention"** — "Ranked triage queue with evidence: delay burden, alert count, risk score."
5. **Show "Service Reliability"** — "Live on-time percentage, stable vs. unstable routes."
6. **Show "Source & Feed Quality"** — "Feed freshness countdown. We monitor the health of the feed itself."
7. **Open `/api/status/network`** — "Public JSON API."
8. **Explain the pattern:** "This same stack points at any GTFS-RT agency. The scoring engine, map, triage queue — all identical. Only the feed URLs change."
9. **Acknowledge the Swiftly dependency:** "For LA, we'd use your existing Swiftly feed. For Charlotte, your existing GTFS-RT. One API key sign-up and we're live."

### Key Talking Points

- "This is real MBTA data. Live. Right now."
- "The same deployment can point at LA Metro or CATS with a configuration change."
- "Our feed parser handles both JSON and protobuf GTFS-RT, with auth headers."
- "We can be live on your data within a week of getting feed access."

---

## 12. Proposed Next Steps

### For Both Agencies

| Step | What | Timeline |
|------|------|----------|
| 1 | Share this brief with their technical lead | After meeting |
| 2 | Obtain GTFS-RT feed access (Swiftly API key for LA, feed URLs for CATS) | Within 1 week |
| 3 | Configure Transit Sentinel for their agency | 1-2 days |
| 4 | Produce live demo on their data | Day 3-5 |
| 5 | Technical demo call with their operations / IT team | Week 2 |
| 6 | Pilot discussion | Week 3-4 |

### For LA Metro Specifically

**Immediate ask:** "Give us a Swiftly API key — or confirm we can register for one — and within a week we'll show you what Transit Sentinel sees in LA Metro's data. No contract. No integration."

**On Swiftly key:** If they're already a Swiftly customer, they may have a key or can get one from their Swiftly account. Otherwise, the free registration at goswift.ly/realtime-api-key works.

**ATMS II integration points for later:**
- Consume CleverCAD's data API for richer vehicle state
- Provide Transit Sentinel's triage queue as a BOC center screen
- Export case packs as evidence for post-event analysis

### For Charlotte CATS Specifically

**Immediate ask:** "Share your GTFS-RT feed URLs. We'll have a live Transit Sentinel deployment for Charlotte within a week."

---

## Appendix A: LA Metro Feed Quick Reference

| Feed | URL | Type | Auth |
|------|-----|------|------|
| Bus Vehicle Positions | `https://api.goswift.ly/real-time/lametro/gtfs-rt-vehicle-positions` | Protobuf | Swiftly API key |
| Bus Trip Updates | `https://api.goswift.ly/real-time/lametro/gtfs-rt-trip-updates` | Protobuf | Swiftly API key |
| Rail Vehicle Positions | `https://api.goswift.ly/real-time/lametro-rail/gtfs-rt-vehicle-positions` | Protobuf | Swiftly API key |
| Rail Trip Updates | `https://api.goswift.ly/real-time/lametro-rail/gtfs-rt-trip-updates` | Protobuf | Swiftly API key |
| Metro API v2 (static) | `https://api.metro.net/routes/LACMTA` etc. | REST JSON | None |
| Swiftly key sign-up | `https://goswift.ly/realtime-api-key` | — | Free registration |
| Static GTFS (regional) | `https://github.com/LACMTA/los-angeles-regional-gtfs` | GTFS .zip | None |
| Cal-ITP quality reports | `https://reports.dds.dot.ca.gov/gtfs_schedule/2026/01/183/` | HTML | None |

## Appendix B: Agency Profiles

| Dimension | LA Metro | Charlotte CATS | MBTA (current) |
|-----------|----------|----------------|----------------|
| Size | 2nd largest US agency | Mid-size | 4th largest US agency |
| Modes | Bus, light rail, subway, BRT | Bus, light rail, streetcar | Bus, subway, light rail, commuter rail, ferry |
| Daily boardings | ~1M+ | ~50K | ~1.1M |
| Routes | ~200 bus + rail | ~80 bus + 1 rail | ~200 routes |
| GTFS-RT format | Protobuf (via Swiftly) | Likely protobuf | JSON (no auth) |
| GTFS-RT auth required | Yes — API key | Unknown | No |
| CAD/AVL vendor | Clever Devices (ATMS II) | Unknown (INIT/Conduent likely) | Internal systems |
| Public API | Metro API v2 | Limited | None (Transit Sentinel provides) |

## Appendix C: Key Messages Cheat Sheet

| Question | One-Sentence Answer |
|----------|---------------------|
| What is Transit Sentinel? | An operations intelligence layer that turns public transit feeds into a ranked, explainable decision queue. |
| How is it different from CAD/AVL? | CAD/AVL tells you where vehicles are; we tell you what deserves attention and why. |
| Do you replace CleverCAD/INIT? | No — we complement them. We're the triage desk on top of the dispatch system. |
| How fast can you be live on our data? | Within a week — we need a Swiftly API key for LA or feed URLs for Charlotte. |
| Our feeds require a Swiftly key. | We already support auth headers and protobuf. One free registration and we're live. |
| How is this different from Swiftly? | Swiftly delivers GTFS-RT data. Transit Sentinel consumes it and produces intelligence. Complementary. |
| What does it cost? | Our current deployment runs on a $12/month droplet. Enterprise pricing depends on scale. |
| Why Boston first? | MBTA has rich public data. The architecture generalizes to any GTFS-RT agency. |
| What's the proof? | Live at sepdynamics.co for months. 6 case packs. Full test suite. Deployed on real infrastructure. |
