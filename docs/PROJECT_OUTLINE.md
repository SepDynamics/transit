# Transit Sentinel Project Outline

## Product Thesis

Transit Sentinel should shorten the time from:

`something looks wrong on the line`

to:

`service on this corridor is irregular, here is the evidence, and here is the highest-priority intervention right now`

## What The Product Does Now

- archives public GTFS and GTFS-RT feeds
- normalizes transit entities into rolling corridor and vehicle state
- scores transit-native regimes and recommended actions
- persists live and replay state in Valkey
- exposes incidents, trends, history, map, and scorecards through an API
- renders that state in a React operations console
- regression-tests scoring against public case packs

## Primary Users

- transit operations control teams
- rider information teams
- performance and planning analysts
- agencies evaluating public-data monitoring improvements

## Current Proof Assets

- committed MBTA and Los Angeles case packs
- a naive baseline comparison path
- event overlays for venue access and service-context scenarios
- replay imports that let the dashboard and API re-run known incidents

## Core Product Tracks

### 1. Live Monitoring

- archive current agency feeds
- ingest them into the rolling store
- surface current instability in the dashboard and notification layer

### 2. Replay And Calibration

- import archived windows as replay traces
- compare Sentinel against simple baselines
- keep case-pack verdicts reproducible

### 3. Reporting

- corridor trend memory
- network and corridor scorecards
- archive-based summaries and retrospectives

### 4. Event Operations

- venue overlays
- bus bridge and service-context scenarios
- event-day corridor monitoring on public data

## Near-Term Success Criteria

Transit Sentinel is worth continuing if it can keep showing, on real public
data:

- repeatable incident detection on multiple agencies
- better behavior than naive threshold baselines
- low false-positive behavior on control scenarios
- useful operator-facing explanations and actions
- a clean live-to-replay workflow for demos and reviews
