# Transit Sentinel Project Outline

## Product Thesis

Transit Sentinel should reduce the time from:

`something looks wrong on the line`

to:

`here is the unstable corridor, here is the likely cause pattern, and here is the best intervention right now`

## First Wedge

The first version should not try to solve all transit analytics.

It should focus on one useful job:

- detect bunching, headway collapse, and terminal instability early enough to help an operator intervene

## Initial Target Market

- public transit agencies
- operations control centers
- rider information teams
- third-party performance analytics teams

## Recommended Public Testbed

- MBTA first
- 511 Bay Area second
- MTA later for scale and complexity

## Phase 1: Scaffold Conversion

Goal:

- lock transit in as the default engineering lane without losing the reusable stack pattern

Deliverables:

- transit-first check and CI defaults
- removal of transit compatibility wrappers that only served the copied cluster path
- explicit legacy cluster compatibility boundaries
- documented target MBTA proof workflow

## Phase 2: Feed Ingest

Goal:

- ingest official public transit feeds into a rolling store

Deliverables:

- static GTFS importer
- GTFS-RT polling collector
- normalization layer for route/trip/vehicle/stop identifiers
- replayable feed snapshot format

## Phase 3: Regime Scoring

Goal:

- score rolling windows into operational regimes rather than raw delay metrics

Candidate signals:

- headway variance
- bunching compression
- dwell inflation
- terminal turnaround drift
- trip cancellation concentration
- feed coherence problems

Deliverables:

- transit regime taxonomy
- hazard scoring
- confidence/provenance output
- recurrence/signature tracking

## Phase 4: Incident Policy

Goal:

- convert scored regimes into operator-facing incidents

Deliverables:

- line/corridor/terminal incident types
- recommended intervention mapping
- severity and confidence policy
- stale-feed handling

## Phase 5: Evaluation

Goal:

- prove value against simple baselines on replayable MBTA case packs

Baselines:

- delay threshold only
- headway threshold only
- alert-feed only

Success measures:

- earlier detection
- fewer false alerts
- better corridor-level grouping
- more useful operator action suggestions
- repeatable case-pack verdicts across multiple MBTA scenarios

## Phase 6: Dashboard Refactor

Goal:

- replace the copied GPU console with a transit operations surface

Views:

- network overview
- line and branch health
- terminal stress
- corridor incidents
- replay mode
- evidence and provenance drilldown

## Definition Of Success

Transit Sentinel is worth continuing if it can show, on public data:

- one real bunching case
- one real corridor degradation case
- one clean control case
- reproducible replay
- measurable win over threshold rules on at least one dimension
