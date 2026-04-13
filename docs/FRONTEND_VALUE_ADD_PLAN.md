# Frontend Value-Add Plan

## Goal

Make the product value obvious in the first minute of use:

`public feed signals -> corridor risk -> action priority -> replayable proof`

The frontend should make clear that Transit Sentinel is not a raw feed viewer.
It turns GTFS schedules, GTFS-RT vehicles, trip updates, and alerts into
operator-facing priorities, evidence, and replay assets.

## Implemented In This Pass

- Added a top-of-console value strip after the hero in the operations console.
- Tied the value strip to live runtime counters:
  - configured feed lanes
  - visible vehicles
  - trip updates
  - alerts
  - scored corridors
  - average risk
  - most urgent action
  - replay trace count
  - scorecard snapshot count
- Reframed hero copy around the value path:
  - public-data proof
  - replayable incidents
  - operator action queue
- Expanded the bundled OpenAPI endpoint index so the API surface behind the
  frontend is easier to inspect.
- Updated repository status and audit notes so docs match the current code
  state and current product boundaries.

## Next Frontend Improvements

### 1. Make The Proof Loop Clickable

Add a replay-proof panel that links each active incident to:

- the selected corridor
- the latest evidence factors
- the replay trace selector
- scorecard history for the same corridor

The key shift is from "incident list" to "why this was escalated and how to
replay it."

### 2. Add A Corridor Evidence Drawer

When a corridor is selected, show a compact evidence summary above the history
tables:

- top provenance factors
- delay/headway/dwell components
- active alerts used for corroboration
- confidence and signal agreement
- current action tier

This should use existing API fields before adding new endpoints.

### 3. Add Public Status Search And Grouping

The public status page needs a clearer product front door:

- route search
- grouping by mode or route family
- severity filters
- "updated" and auto-refresh state
- route drilldown deep links

This is the highest-value public-facing UX improvement because the current flat
route list will not scale to large agencies.

### 4. Add Value Metrics To The Scorecard

Turn scorecard output into clearer proof metrics:

- percent of corridor snapshots that remained stable
- percent at risk
- top recurring actions
- comparison to a naive threshold baseline when available
- replay-window links for scored incidents

This likely needs backend support to expose baseline comparisons in the API,
not only in calibration artifacts.

### 5. Consolidate Polling Into A Dashboard Payload

The operations console still polls several endpoints on overlapping intervals.
Add a backend dashboard endpoint or server-push path that returns:

- health
- entities
- regimes
- incidents
- trends
- map summary
- scorecard summary
- sources

This would make the UI feel faster and reduce the operational cost of the
frontend itself.

### 6. Add Guided Demo States

For demos, add named narrative states to the seeded data flow:

- live MBTA snapshot
- replay incident trace
- quiet control trace
- LA Metro event trace when archive quality is validated

The frontend can then label the selected trace with the proof intent instead
of only showing a trace id.

## Backend Or Data Work Needed

- Typed OpenAPI response schemas for all frontend-consumed payloads.
- A compact incident evidence endpoint if existing incident payloads become too
  heavy for the drawer.
- Baseline comparison metrics surfaced through the API, not only through
  calibration reports.
- More replay traces with labeled positive incidents and quiet controls.
- More Valkey batching for history writes and scorecard/trend reads so large
  replay imports remain interactive.

## Copy Standard

Use product language that names the operational outcome:

- Prefer: `Corridor risk`, `Action queue`, `Replay proof`, `Public evidence`.
- Avoid: generic feature labels such as `Dashboard`, `Analytics`, or
  `Visualization`.
- Prefer counts tied to the current selected scope over static claims.
- Keep internal regime tokens available in details, but lead with
  operator-facing labels.
