# Stack Audit - 2026-04-19

Audit date: Sunday, April 19, 2026

Scope:

- local repository at `/sep/transit-sentinel`
- live droplet at `root@161.35.226.210`
- host checkout at `/root/transit`
- public site at `https://sepdynamics.co/`

## Executive Summary

The live MBTA stack is running, current, and aligned with the repo's documented
deployment shape.

No critical production blockers were found. The public status page and public
status API are available over HTTPS. Valkey, API, and frontend containers are
healthy. Host memory, swap, disk, and Valkey memory are within the intended
operating envelope. Anonymous access to protected `/api/transit/*` endpoints is
blocked with `401`.

The main follow-up is performance polish: ingest CPU can spike near one core
during parsing/scoring cycles. This is acceptable for the current single-agency
host but should be profiled before adding agencies, heavier history, or more
console traffic.

## Repository State

Local checkout:

- path: `/sep/transit-sentinel`
- branch: `main`
- status at audit start: clean relative to `origin/main`
- latest commit: `139d207 Add API parity migration harness`

Host checkout:

- path: `/root/transit`
- branch: `main`
- status: clean relative to `origin/main`
- commit: `139d207`

The local repository and droplet checkout are on the same commit.

## Live Deployment

Expected compose files are in use:

```text
docker-compose.transit.yml
docker-compose.live-host.yml
```

Running services:

```text
valkey
archive
ingest
api
frontend
```

Container status during audit:

| Service | Container | Status | Public binding |
| --- | --- | --- | --- |
| Valkey | `transit-sentinel-valkey` | running, healthy | `127.0.0.1:6379` |
| Archive | `transit-sentinel-archive` | running | internal only |
| Ingest | `transit-sentinel-ingest` | running | internal only |
| API | `transit-sentinel-api` | running, healthy | `127.0.0.1:8000` |
| Frontend | `transit-sentinel-frontend` | running, healthy | `127.0.0.1:8080` |

Public traffic path:

```text
Internet -> Caddy :443 -> 127.0.0.1:8080 -> frontend nginx -> API container
```

Caddy is active and enabled. The Caddyfile proxies `sepdynamics.co` and
`www.sepdynamics.co` to `127.0.0.1:8080`.

## Host And Runtime

Host and runtime observed during audit:

- kernel: Linux `6.8.0-110-generic` on x86_64
- Docker client/server: `29.1.3`
- Docker Compose: `2.40.3`
- root filesystem: 77 GiB total, 9.1 GiB used, 68 GiB available
- memory: 3.8 GiB total, 1.5 GiB used, 2.3 GiB available
- swap: 2.0 GiB total, 496 KiB used

Container memory snapshots:

| Container | Memory |
| --- | --- |
| frontend | 4.1 MiB / 192 MiB |
| API | 389.6 MiB / 900 MiB |
| ingest | 289 MiB / 768 MiB |
| archive | 53 MiB / 384 MiB |
| Valkey | about 300 MiB / 900 MiB |

The ingest container alternated between idle samples and near-100 percent CPU
samples. Logs show this corresponds to ingest cycles rather than a crash loop.

## Live Health

`scripts/transit/live_health.py --json` passed all checks:

- Docker CLI available
- all 5 expected containers running
- host memory OK
- Valkey memory OK
- local API health OK
- public status endpoint OK
- no recent kernel OOM evidence
- no recent `503` / `server_busy` log matches

Representative API health values during audit:

- local API `/health`: `200`, about `65 ms`
- public `/api/status/network`: `200`, about `24 ms`
- public status severity at sample time: `Service Disruption`
- active routes at sample time: `192`
- disrupted routes at sample time: `63`
- incident count at sample time: `26`
- feed status: `ok`
- vehicle count at sample time: `395`
- trip update count at sample time: `864`
- alert count at sample time: `95`

Protected endpoint check:

```text
GET https://sepdynamics.co/api/transit/dashboard -> 401
{"error":"unauthorized","required_role":"viewer"}
```

This matches the live-host security boundary: `/api/status/*` is public,
`/api/transit/*` is protected.

## Valkey

Valkey memory during audit:

- used memory: about `265.7M`
- RSS: about `299M`
- peak: about `496M`
- fragmentation ratio: about `1.12`
- keyspace: `3484` keys, `1612` keys with expirations
- average TTL: about `6421` seconds

Largest keys were expected latest-state and history payloads:

- `transit:live:last:entities`
- `transit:entities:last`
- `transit:dashboard:live:last`
- `transit:live:last:regimes`
- `transit:regimes:last`
- vehicle regime history keys

Read models were present:

- `transit:scorecard:live:last`
- `transit:trends:live:last`
- `transit:dashboard:live:last`
- `transit:status:network:last`

Observation: Valkey is bounded by Docker container memory, not by Redis
`maxmemory`; `maxmemory_human` reported `0B`. That is acceptable with current
host checks, but setting an explicit Redis `maxmemory` and policy would make the
memory boundary more obvious.

## Frontend

The public frontend is configured as status-only:

```text
OPS_CONSOLE_ENABLED=0
API_URL=""
```

This matches the intended investor-safe public posture. The protected operations
console exists in the build, but the live public host should not expose it until
there is a protected route or login flow.

The site returns HTTPS `200` through Caddy/nginx. Cache headers are conservative
for the HTML entry point, which is appropriate for a runtime-configured single
page app.

Low-priority log noise:

- nginx logs a missing `/favicon.ico` request.

This does not affect the demo, but adding a favicon would remove avoidable
error noise from logs.

## API And Data Flow

The API serves public status endpoints and protected operations endpoints from
Valkey. For normal live paths, materialized read models avoid cold scorecard and
dashboard rollups on every request.

Important live-host controls:

- `TRANSIT_REPLAY_ENABLED=0`
- `TRANSIT_API_REQUIRE_AUTH=1`
- `TRANSIT_API_CACHE_MAX_ENTRIES=6`
- `TRANSIT_API_SCORECARD_MAX_LIMIT=60`
- `TRANSIT_API_MAX_CONCURRENT_REQUESTS=4`
- `TRANSIT_API_REQUEST_QUEUE_SIZE=8`
- `TRANSIT_HISTORY_RETENTION=120`
- `TRANSIT_HISTORY_TTL_SECONDS=7200`
- `TRANSIT_GTFS_LIGHTWEIGHT=1`

The API also supports conditional JSON GET responses through `ETag` and
`If-None-Match`, and the frontend polling client uses those validators.

## Architecture Fit

The actual live stack matches the documented architecture:

```text
MBTA GTFS / GTFS-RT
  -> archive current feed set
  -> ingest
  -> Valkey latest state + rolling history + read models
  -> API
  -> public status page / protected operations console
```

The public host is not running replay mode and is not serving seeded demo data.
It is showing current MBTA feed state.

## Findings

### No Critical Findings

The live stack is healthy enough for the April 20 investor meeting.

### Medium: Ingest CPU Spikes

The ingest service can use roughly one full CPU core during some cycles. Memory
remains stable and the service is not crash-looping.

Recommended action:

- profile `TransitSnapshotService.snapshot()` and read-model materialization
- measure whether static GTFS metadata, alert parsing, or route scoring is the
  largest contributor
- avoid increasing agency count or history depth until this is understood

### Low: Valkey Memory Policy Is Implicit

Valkey relies on container memory limits plus application retention and TTLs.
Redis `maxmemory` is not set.

Recommended action:

- consider adding explicit `--maxmemory` and an intentional eviction policy once
  the desired failure mode is chosen
- keep TTLs and history retention as the primary correctness control

### Low: Missing Favicon Log Noise

The frontend logs missing `/favicon.ico` requests.

Recommended action:

- add a small favicon or nginx route to reduce noisy error logs

## Investor Meeting Readiness

Ready to show:

- public MBTA status page
- public status API
- live health JSON
- protected API returning `401` without a bearer token
- MBTA-only architecture and deployment docs
- MBTA case-pack and calibration workflow

Avoid claiming:

- dispatch replacement
- internal MBTA operational visibility
- non-Boston live coverage
- predictive accuracy guarantees beyond the committed proof and live evidence

Best claim:

Transit Sentinel is a deployed MBTA intelligence layer that converts public
transit telemetry into ranked, explainable service status and operational proof.
