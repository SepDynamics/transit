# Alternative-Service Operator Preview

Transit Sentinel exposes deterministic, stop-scoped alternative-service
evaluation at:

```text
GET /api/transit/alternative-advisories
```

The Operations console populates its route-scoped stop selectors from:

```text
GET /api/transit/alternative-advisories/options
```

This is a protected operations endpoint. It always requires a bearer token
explicitly registered as `operator` or `admin` in `TRANSIT_API_TOKENS`, even
when `TRANSIT_API_REQUIRE_AUTH` is disabled for compatibility with other API
surfaces. With no token registry, anonymous access, an unknown token, or a
viewer token, it fails closed with HTTP `401`. It is intentionally not
available under the public `/api/status/*` surface.

The frontend deliberately sends no browser-configured bearer token for either
operator-preview request. Deploy the console at a separate operations-only
hostname or protected path where an authenticated reverse proxy or BFF:

1. authenticates the operator (for example with agency OIDC, mTLS, or an
   equivalent access gateway);
2. strips any client-supplied `Authorization` header;
3. injects the registered operator credential only on the trusted upstream
   request; and
4. keeps the API on a private network or loopback interface.

A VPN limits reachability but does not satisfy the backend's operator-token
check by itself. `OPS_CONSOLE_ENABLED` is only a UI feature flag, not an access
control. Never configure `API_BEARER_TOKEN` on a public frontend: runtime
configuration is JavaScript and is visible to every browser user. Keep
`OPS_CONSOLE_ENABLED=0` and `API_BEARER_TOKEN` empty on the public host.
On the separately protected Operations host, set `OPS_CONSOLE_ENABLED=1` while
still leaving `API_BEARER_TOKEN` empty; server-side proxy injection supplies the
upstream credential.

## Runtime Setup

Compile a compact topology from the same static GTFS source represented by the
live trip-update feed:

```bash
python3 scripts/transit/compile_topology.py \
  data/feeds/mbta/current/MBTA_GTFS.zip \
  data/topology/mbta.json.gz \
  --feed-label MBTA
```

Configure the API container to read it:

```bash
TRANSIT_ADVISORY_TOPOLOGY_PATH=/app/data/topology/mbta.json.gz
TRANSIT_API_TOKENS='operator-token:operator,admin-token:admin'
```

The Compose API services mount `./data` at `/app/data` read-only. The API loads
the artifact on the first advisory request and reuses it for the process
lifetime. If the setting is absent, the file is missing, or validation fails,
the endpoint returns HTTP `503` with `status: unavailable`; it does not fall
back to schedule guessing.

## Request

First request stop choices for the selected disrupted route and, when known,
its GTFS direction:

```bash
curl -H 'Authorization: Bearer operator-token' \
  'http://127.0.0.1:8000/api/transit/alternative-advisories/options?disrupted_route_id=Red&direction_id=0'
```

The lookup returns `available` with ordered stops and each stop's valid
`downstream_stop_ids`, `selection_required` when a route has multiple numbered
directions and none was supplied, or `unavailable` with an explicit topology
reason. Directionless patterns are used only when the route has no numbered
GTFS direction. It returns HTTP `503` for `unavailable`, matching the evaluation
endpoint's fail-closed behavior.

The required query parameters are `origin_stop_id`, `destination_stop_id`, and
`disrupted_route_id`. `direction_id` is optional only when the topology can
identify exactly one applicable direction for that route and ordered stop
pair. An ambiguous request returns HTTP `400` rather than allowing health from
the opposite direction to trigger advice.

Example:

```bash
curl -H 'Authorization: Bearer operator-token' \
  'http://127.0.0.1:8000/api/transit/alternative-advisories?origin_stop_id=place-a&destination_stop_id=place-b&disrupted_route_id=Red&direction_id=0'
```

The evaluation combines the topology with the latest retained stop-prediction
evidence and current direction-specific route regimes. A response is either a
published decision, a suppression with explicit reasons, or an unavailable
decision. Every decision echoes the requested route, origin, and destination so
the console can reject stale or mismatched proxy responses. Every response also
identifies `release_stage: operator_preview` and states the product boundary:
advisory only, no inferred mechanical or traffic cause, no arrival guarantee,
and no dispatch or reroute instruction.

Do not promote this endpoint into the rider-facing status API until historical
disruption and quiet-control validation establishes recommendation coverage,
false-recommendation rate, realized arrival improvement, and alternative-route
reliability.
