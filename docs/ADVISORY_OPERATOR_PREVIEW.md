# Alternative-Service Operator Preview

Transit Sentinel exposes deterministic, stop-scoped alternative-service
evaluation at:

```text
GET /api/transit/alternative-advisories
```

This is a protected operations endpoint. It always requires a bearer token
explicitly registered as `operator` or `admin` in `TRANSIT_API_TOKENS`, even
when `TRANSIT_API_REQUIRE_AUTH` is disabled for compatibility with other API
surfaces. With no token registry, anonymous access, an unknown token, or a
viewer token, it fails closed with HTTP `401`. It is intentionally not
available under the public `/api/status/*` surface.

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
decision. Every response identifies `release_stage: operator_preview` and
states the product boundary: advisory only, no inferred mechanical or traffic
cause, no arrival guarantee, and no dispatch or reroute instruction.

Do not promote this endpoint into the rider-facing status API until historical
disruption and quiet-control validation establishes recommendation coverage,
false-recommendation rate, realized arrival improvement, and alternative-route
reliability.
