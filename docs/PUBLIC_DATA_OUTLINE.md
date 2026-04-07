# Public Data Outline

## Purpose

This document defines what Transit Sentinel can build, test, and keep improving
with public data only.

## Public-Data Lanes In This Repo

### MBTA

Configured in the repo today:

- static GTFS
- GTFS-RT vehicle positions
- GTFS-RT trip updates
- GTFS-RT alerts

MBTA is the cleanest end-to-end public lane for continuous archive growth,
replay imports, dashboard testing, case-pack generation, and KPI reporting.

### LA Metro Rail And Bus

Configured in the repo today:

- static GTFS for rail and bus
- websocket realtime collection for vehicle positions
- websocket realtime collection for trip updates
- canceled-service fetch used as a partial alert proxy when available

This is enough to build and test live archive, replay, map, and scorecard
behavior. The main public-data constraint is alert quality: the repo does not
yet have a clean documented public LA Metro alert feed equivalent to MBTA's
GTFS-RT alerts lane.

### California Beyond LA Metro

The repo does not currently include a Caltrans-specific adapter. If California
coverage expands beyond LA Metro, it should be driven by a concrete public feed
target rather than a generic statewide placeholder.

## What The Repo Already Supports With Public Data

- live archive and replay
- rolling incident and trend memory in Valkey
- map rendering from archived or live vehicle positions
- network and corridor KPI scorecards
- calibration against labeled public case packs
- event overlays and venue-focused scenarios
- webhook and email notifications derived from public-feed incidents

## What We Can Test Right Now

### Live And Replay Workflows

- `make transit-mbta-archive ARGS="--once"`
- `make transit-lametro-rail-archive`
- `make transit-lametro-bus-archive`
- `make transit-ingest ARGS="--once --redis redis://localhost:6379/0"`
- `make transit-replay ARGS="--redis redis://localhost:6379/0 --archive-root data/feeds/mbta --trace-id mbta-proof --max-snapshots 20"`
- `make transit-api ARGS="--redis redis://localhost:6379/0"`

### Dashboard And API Workflows

- live versus replay scope switching
- map rendering through `/api/transit/map`
- KPI scorecards through `/api/transit/scorecard`
- corridor and vehicle history drilldown
- incident notification polling through `scripts/transit/notify.py`

### Calibration Workflows

- `make check-transit-case-packs`
- `make transit-calibration-report ARGS="--archive-root data/case-packs --labels data/case-packs"`

## Current Public-Data Limits

- MBTA is the strongest fully public lane; LA Metro still has weaker public alert coverage.
- Public feeds can prove service instability, delay patterns, alert corroboration, and trend quality, but not internal-only agency KPIs such as crew constraints or dispatch staffing.
- A Caltrans lane should not be documented as supported until a concrete public interface is implemented in code.

## Highest-Value Next Steps

### 1. Deepen The Archive Corpus

- run MBTA continuously for weeks, not just spot captures
- validate LA Metro websocket captures under real live load
- turn high-signal archive windows into more case packs

### 2. Expand The Proof Corpus

- add more MBTA bunching, delay-spike, and degraded-service packs
- add more LA Metro live-derived packs once websocket capture quality is confirmed
- keep positive incidents and negative controls balanced

### 3. Strengthen Reporting

- produce recurring scorecard exports
- publish archive-based retrospectives
- turn replay runs into easier procurement/demo artifacts

### 4. Expand Event Overlays Carefully

- grow venue and event overlays where public service context is knowable
- keep overlays repo-owned and explicitly sourced from public context

## Rule

If a proposed feature cannot be exercised, demonstrated, or regression-tested
with the public feeds and public case packs available to this repo, it should be
treated as future work rather than current capability.
