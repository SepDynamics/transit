# Transit Sentinel Execution Backlog

## P0: Prove Value On Transit Event Corridors

### 1. Build replayable city/event case packs

- committed overnight advisory control pack now lives under `data/case-packs/mbta/overnight_advisory_controls`
- committed daytime Red Line delay spike pack now lives under `data/case-packs/mbta/daytime_red_line_delay_spike`
- committed LA B Line bunching pack now lives under `data/case-packs/la/daytime_b_line_bunching`
- committed LA E Line bus bridge control now lives under `data/case-packs/la/e_line_bus_bridge_control`
- committed LA Intuit Dome venue-access controls now live under `data/case-packs/la/intuit_dome_venue_access_controls`
- one labeled bunching onset pack
- one headway collapse pack
- one terminal congestion pack
- one healthy control pack
- each pack should point to archived snapshots and carry an operator-facing note

### 2. Tune transit policy against those packs

- calibrate `hold` vs `short_turn`
- calibrate `dispatch_relief` thresholds
- improve stale-feed and alert-lag handling
- tighten confidence so corridor incidents are fewer and clearer

### 3. Expand baseline comparison

- headway-threshold baseline
- delay-threshold baseline
- service-alert-only baseline
- case-pack batch reports with pass/fail verdicts

## P1: Strengthen The Replay Lane

### 4. Make replay a first-class workflow

- standardize case-pack folder conventions
- keep city/event metadata and event overlays attached to corridor reports
- preserve feed provenance in manifests
- support batch grading over a directory of case packs

### 5. Build corridor history summaries

- recurring unstable corridor reports
- incident rate by route and direction
- top action distribution over replay windows

## P2: Remove Legacy Cluster Weight

### 6. Shrink the compatibility surface

- keep cluster checks explicit, not default
- keep the combined cross-city case-pack suite green before each cluster deletion pass
- delete legacy cluster modules once no external users remain
- remove cluster-only datasets and tooling from this repo when transit no longer needs them
