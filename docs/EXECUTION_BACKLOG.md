# Transit Sentinel Execution Backlog

## Recently Landed

- LA Metro rail and bus websocket archive lanes
- dashboard map view
- network and corridor scorecard API plus UI
- webhook, SMTP, and JSONL notification sidecar
- transit-only repo cleanup and removal of unrelated runtime code

## P0: Deepen Real Public Proof

### 1. Run Long-Lived Archive Collection

- keep MBTA archiving continuously
- validate LA Metro websocket collection under live conditions
- capture enough windows to build more real case packs instead of synthetic examples

### 2. Grow The Case-Pack Corpus

- add more MBTA bunching, collapse, and degradation packs
- build LA Metro packs from live websocket captures once validated
- keep a healthy balance of positive incidents and negative controls

### 3. Keep The Public-Demo Lane Tight

- keep the cross-city case-pack suite green
- keep docs aligned with actual runtime behavior
- avoid reintroducing non-transit or off-scope assets

## P1: Productize The Current Surface

### 4. Expand Reporting Outputs

- recurring scorecard exports
- replay-based incident retrospectives
- simpler proof artifacts for demos and procurement reviews

### 5. Decompose The Frontend

- split `LiveConsole.tsx` into smaller feature components
- preserve replay, map, incident, and scorecard behavior while reducing console complexity

### 6. Harden Operational Delivery

- decide on auth and RBAC requirements before external deployment
- tighten notification routing and delivery configuration
- improve mobile and small-screen usability for field users

## P2: Expand Agency Scope Deliberately

### 7. Validate The Next California Target

- choose a concrete public California feed target before building a new adapter
- do not document a Caltrans lane until the code and feeds are real

### 8. Broaden Event Operations Coverage

- add more venue overlays only where they can be exercised against real public feed behavior
- strengthen corridor-to-corridor event and disruption scenarios with labeled evidence
