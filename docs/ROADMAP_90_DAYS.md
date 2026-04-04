# Transit Sentinel 90-Day Plan

## Goal

Turn the scaffold into a useful public-data transit operations product by
anchoring on MBTA first, then proving the system adds value beyond raw feeds.

## Days 1-30: Ingest And Archive

- land an official MBTA archive lane for:
  - static GTFS
  - GTFS-RT vehicle positions
  - GTFS-RT trip updates
  - GTFS-RT alerts
- store timestamped snapshots locally and maintain a `current/` working set
- normalize feeds into transit-native entities:
  - route
  - trip
  - vehicle
  - stop cluster
  - terminal
  - corridor
- define replay bundle format and import/export helpers
- add feed health and feed incoherence detection
- establish labeled MBTA case-pack conventions for replay and grading

Exit criteria:

- one command can archive an MBTA snapshot on demand
- one service can refresh the local working set continuously
- the API/dashboard can read the archived current feed set
- one MBTA case pack can be replayed and graded end to end

## Days 31-60: Score Service Instability

- implement headway and bunching features
- add terminal congestion and dwell inflation features
- group vehicle-level failures into corridor incidents
- compare heuristic baseline vs Sentinel scoring on historical slices
- build replayable proof bundles:
  - bunching case
  - degraded corridor case
  - healthy control case
- tune operator actions against labeled cases rather than generic scaffold rules

Exit criteria:

- route/corridor incidents are visible in the API
- backtests can compute lead time and alert quality
- recurring bad corridors can be identified over archived history
- at least one MBTA bunching-focused case pack shows Sentinel beating a naive baseline

## Days 61-90: Make It Operationally Useful

- add reliability dashboards over archived history
- expose rider-information and feed-quality views:
  - stale predictions
  - alert lag
  - ghost vehicles
  - missing trip updates
- build case-study summaries and proof pages
- add second-agency adapter to validate portability:
  - WMATA preferred
  - TriMet acceptable
- start removing cluster-only repo weight that no longer serves the transit product

Exit criteria:

- the product can explain why a corridor is unstable
- the product can replay and score known service failures
- the product can benchmark agencies or routes on the same normalized model
