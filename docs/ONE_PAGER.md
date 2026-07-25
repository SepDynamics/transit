# Transit Sentinel — One Pager

**Live at sepdynamics.co** · July 2026

---

## What It Is

Transit Sentinel turns public transit feeds (GTFS + GTFS-RT) into a **ranked, explainable decision queue**: what's unstable, why it was ranked, what evidence supports it, and what an operator should do next.

It is **not** a CAD/AVL replacement, a rider app, or a generic dashboard. It is an operations intelligence layer that sits between raw telemetry and human decision-making.

**The analogy that lands:** Agency alerts are the bulletin board. Transit Sentinel is the triage desk.

Because we rely entirely on public telemetry, we do not need a formal agency integration to demonstrate value. To prove this, we continuously process **Boston's MBTA public feeds** as our live proving ground at `sepdynamics.co` — tracking 192+ routes every 20 seconds, months of continuous operation.

---

## The Pipeline

```
GTFS-RT feeds → archive → ingest / scoring engine → Valkey (read models) → API → frontend
```

1. **Archive** polls agency feeds every 30 seconds
2. **Ingest** normalizes vehicles, trip updates, alerts — then scores each route/corridor on 40+ metrics
3. **Scoring** classifies into 7 regimes (healthy, bunching_onset, headway_collapse, service_degraded, etc.) with weighted hazard score, confidence, and provenance
4. **API** serves public `/api/status/*` and protected `/api/transit/*` endpoints with ETag caching, bounded concurrency
5. **Frontend** React/TypeScript app shows ranked triage queue, live map (MapLibre), scorecard, trend analysis, evidence drawer

---

## What Makes It Different

| Category | What's Available Today | Transit Sentinel Difference |
|---|---|---|
| **CAD/AVL** (CleverCAD, INIT, Conduent) | Vehicle tracking, schedule adherence, dispatch comms, yard mgmt | Adds cross-corridor ranking, explainable scoring, evidence preservation — the "triage desk" on top of dispatch |
| **Raw GTFS-RT feeds** | Raw protobuf/JSON data, requires parsing | Normalizes, scores, caches, separates public vs. protected surfaces, adds public API products |
| **Performance dashboards** | Historical reliability metrics (on-time %, speed, etc.) | Live current-state triage with ranked urgency — answers "what needs attention right now" |
| **Rider apps** (Transit, Google Maps, etc.) | Trip planning, ETAs, rider navigation | Built for operators and public-information teams, not riders |

**The visual:** Your CAD/AVL shows vehicle #1432 at lat/lon with +3min delay. Transit Sentinel shows "#1 Red Line — hazard 0.81 — evidence: 14min delay, 3 high-impact alerts, headway collapsed — action: dispatch relief."

---

## Key Differentiators

1. **Public-feed-first deployment** — works from GTFS-RT feeds agencies already publish. No procurement, no internal data-sharing agreement needed to demonstrate value.

2. **Scoring with provenance** — every ranking carries an evidence trail. Not an opaque score — you can see exactly why a route was ranked where it is.

3. **Public/API separation** — `/api/status/*` (rider-facing, plain language) and `/api/transit/*` (operations, internal vocabulary) share the same engine with different auth, caching, and schema.

4. **Replayable proof (case packs)** — scoring changes are validated against labeled historical incidents. You can prove a change is better or detect regression before deploying.

5. **Performance-engineered architecture** — materialized read models, Redis pipelines, ETag conditional GET, bounded resource limits. Processes network-wide telemetry with minimal infrastructure footprint.

---

## Where the Value Is

| User | What Transit Sentinel Provides |
|---|---|
| **Operations controller** | Ranked triage queue — the 8 routes that need attention right now, with evidence and recommended actions |
| **Public information team** | Public status page + JSON API with rider-friendly language, feed quality monitoring |
| **Data / analytics team** | Replayable proof, case pack calibration, trend analysis, automated scorecard |
| **IT / integration team** | Clean `/api/status/*` contract for third-party developers, feed health monitoring |
| **Executive / board** | Live uptime, incident reduction tracking, performance scorecard across all modes |

---

## What We Have Proven Today (Boston Proving Ground)

Transit Sentinel proves that a useful operations intelligence product can be bootstrapped from public transit standards before requiring internal agency integrations.

- **Live Boston proving ground:** We selected Boston to stress-test our engine due to its rich public data availability and complex multi-modal network. We actively process the MBTA's live network at `sepdynamics.co` with months of zero-downtime continuous operation.
- **Scale:** 192+ routes tracked, current feed ingest every 20 seconds
- **Verifiability:** Rigorous calibration suite — 6 case packs with 16 labeled historical incidents (both positive incidents and quiet controls) ensuring scoring changes are auditable instead of subjective
- **Resilience:** High-efficiency architecture (Valkey read models, bounded memory limits) processing network-wide telemetry without massive cloud overhead
- **Surface area:** 20+ API endpoints across public and protected surfaces, full test suite, benchmark artifacts, live health monitoring

---

## Pilot Opportunity

Transit Sentinel's technical foundation is designed to be reusable across any agency that publishes GTFS and GTFS-RT. Because we rely solely on public telemetry for the first wedge, we do not require a heavy procurement cycle or internal dispatch data-sharing agreements to demonstrate value.

**We are currently looking for our first official pilot partner** to run alongside our live Boston tracker. We can focus the pilot on whichever wedge is most immediately valuable to your team:

1. **Live status / API monitoring** — a public status page and JSON API for riders and third-party developers
2. **Operations triage and prioritization** — the ranked, explainable decision queue for your control center
3. **Feed-quality assurance** — continuous monitoring and reporting of your GTFS-RT feed health
4. **Reliability proof reports** — automated scorecards and replayable evidence for service events
