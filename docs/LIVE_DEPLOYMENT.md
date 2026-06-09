# Live Deployment

This runbook covers the hosted MBTA live deployment behind `sepdynamics.co`.
The host checkout is expected at `~/transit`.

This file is the single deployment reference and includes the current stack audit
(updated June 8, 2026).

---

## Current Shape

- public URL: `https://sepdynamics.co/`
- public status API: `https://sepdynamics.co/api/status/network`
- public frontend defaults to status-only on the live host
- runtime: Docker Compose
- compose files: `docker-compose.transit.yml` plus `docker-compose.live-host.yml`
- services: `valkey`, `archive`, `ingest`, `api`, `frontend`
- public reverse proxy: Caddy on ports `80` and `443`
- app ports bound to loopback:
  - Valkey: `127.0.0.1:6379`
  - API: `127.0.0.1:8000`
  - frontend nginx: `127.0.0.1:8080`
- replay disabled on the live host with `TRANSIT_REPLAY_ENABLED=0`

The live host is not a seeded demo environment. It shows current MBTA public
feed state at production-like scale.

---

## Resource Posture

The live override intentionally reduces pressure on the host:

- Valkey AOF disabled; RDB snapshot every 300 seconds after a write.
- API generic cache capped at six entries.
- API scorecard reads capped to 60 samples and cached for 60 seconds.
- Ingest materializes live read models for the 60-sample scorecard, trends,
  dashboard, and public network status keys.
- API concurrency capped at four active requests with an eight-request queue.
- API JSON `GET` responses emit `ETag` and honor `If-None-Match`; the frontend
  polling client sends validators and reuses cached payloads on `304`.
- Ingest runs every 20 seconds.
- History writes run every 60 seconds.
- Rolling history retention is 120 samples per key.
- Rolling history keys expire natively in Valkey after 7200 seconds.
- Container memory limits are set for Valkey, API, ingest, archive, and
  frontend.
- Valkey is configured with explicit `--maxmemory 768mb --maxmemory-policy allkeys-lru`.
- Valkey, API, and frontend containers have Docker healthchecks.

If memory pressure returns, check Valkey history size before increasing host
size. The intended first response is pruning or tightening retention, not
letting history grow unbounded.

Route-level zero delay is rendered as no measured delay signal. A ranked route
can still be disruption-worthy when alerts, headway compression, vehicle
bunching, or telemetry quality are the active evidence.

---

## Stack Audit (Updated June 8, 2026)

### Executive Summary

The live MBTA stack is running, current, and aligned with the repo's documented
deployment shape. No critical production blockers found. The public status page
and public status API are available over HTTPS. Valkey, API, and frontend
containers are healthy. Host memory, swap, disk, and Valkey memory are within
the intended operating envelope. Anonymous access to protected `/api/transit/*`
endpoints is blocked with `401`.

The main follow-up area is ingest CPU, which can spike near one core during
parsing/scoring cycles. This is acceptable for the single-agency host but should
be profiled before adding agencies, heavier history, or more console traffic.

### Container Status

| Service | Container | Status | Public binding |
|---------|-----------|--------|----------------|
| Valkey  | `transit-sentinel-valkey` | running, healthy | `127.0.0.1:6379` |
| Archive | `transit-sentinel-archive` | running | internal only |
| Ingest  | `transit-sentinel-ingest` | running | internal only |
| API     | `transit-sentinel-api` | running, healthy | `127.0.0.1:8000` |
| Frontend| `transit-sentinel-frontend` | running, healthy | `127.0.0.1:8080` |

Public traffic path:

```
Internet -> Caddy :443 -> 127.0.0.1:8080 -> frontend nginx -> API container
```

Caddy is active and enabled. The Caddyfile proxies `sepdynamics.co` and
`www.sepdynamics.co` to `127.0.0.1:8080`.

### Host And Runtime

- kernel: Linux `6.8.0-110-generic` on x86_64
- Docker: `29.1.3`, Compose: `2.40.3`
- root filesystem: 77 GiB total, ~9 GiB used
- memory: 3.8 GiB total, ~1.5 GiB used
- swap: 2.0 GiB total, minimal usage

Container memory snapshots (representative):

| Container | Memory |
|-----------|--------|
| frontend  | 4.1 MiB / 192 MiB |
| API       | 390 MiB / 900 MiB |
| ingest    | 289 MiB / 768 MiB |
| archive   | 53 MiB / 384 MiB |
| Valkey    | ~300 MiB / 900 MiB |

The ingest container alternates between idle samples and near-100% CPU during
ingest cycles — this is expected and not a crash loop.

### Live Health

`scripts/transit/live_health.py --json` passes all checks:

- Docker CLI available
- all 5 expected containers running
- host memory OK
- Valkey memory OK (explicit maxmemory configured)
- local API health OK
- public status endpoint OK
- no recent kernel OOM evidence
- no recent `503` / `server_busy` log matches

Representative API health values:

| Check | Status | Latency |
|-------|--------|---------|
| local API `/health` | 200 | ~65 ms |
| public `/api/status/network` | 200 | ~24 ms |
| public severity | Service Disruption | — |
| active routes | 192 | — |
| incident count | 26 | — |
| feed status | ok | — |

Protected endpoint check:

```
GET https://sepdynamics.co/api/transit/dashboard -> 401
{"error":"unauthorized","required_role":"viewer"}
```

### Valkey

- used memory: ~266 MiB
- RSS: ~299 MiB
- peak: ~496 MiB
- fragmentation ratio: ~1.12
- keyspace: ~3500 keys
- Read models present: scorecard, trends, dashboard, status:network
- **Explicit maxmemory set: 768 MiB with allkeys-lru policy**

### Frontend

The public frontend is configured as status-only (`OPS_CONSOLE_ENABLED=0`).
The protected operations console exists in the build but is not exposed on the
public host. The site returns HTTPS 200 through Caddy/nginx with conservative
cache headers for the HTML entry point.

A favicon has been added to eliminate 404 noise in logs.

### API And Data Flow

Key live-host controls:

- `TRANSIT_REPLAY_ENABLED=0`
- `TRANSIT_API_REQUIRE_AUTH=1`
- `TRANSIT_API_CACHE_MAX_ENTRIES=6`
- `TRANSIT_API_SCORECARD_MAX_LIMIT=60`
- `TRANSIT_API_MAX_CONCURRENT_REQUESTS=4`
- `TRANSIT_API_REQUEST_QUEUE_SIZE=8`
- `TRANSIT_HISTORY_RETENTION=120`
- `TRANSIT_HISTORY_TTL_SECONDS=7200`
- `TRANSIT_GTFS_LIGHTWEIGHT=1`

Conditional JSON GET via `ETag`/`If-None-Match` is active for all endpoints.

### Architecture Fit

The actual live stack matches the documented architecture:

```
MBTA GTFS / GTFS-RT
  -> archive current feed set
  -> ingest
  -> Valkey latest state + rolling history + read models
  -> API
  -> public status page / protected operations console
```

The public host is not running replay mode and is not serving seeded demo data.
It shows current MBTA feed state.

### Resolved Findings from Prior Audit

| Finding | Status | Resolution |
|---------|--------|------------|
| Ingest CPU spikes | Open | Acceptable for single-agency host; profile before expansion |
| Valkey memory policy implicit | **Resolved** | `--maxmemory 768mb --maxmemory-policy allkeys-lru` set |
| Missing favicon log noise | **Resolved** | Favicon added to remove 404 noise |

---

## Start Or Update The Stack

From the host checkout:

```bash
cd ~/transit
docker compose -f docker-compose.transit.yml -f docker-compose.live-host.yml --profile demo stop api-demo frontend-demo
docker compose -f docker-compose.transit.yml -f docker-compose.live-host.yml up -d --build valkey archive ingest api frontend
```

Ensure the containers can write feed and log paths:

```bash
mkdir -p data/feeds/mbta/current logs/transit
chown -R 999:999 data/feeds logs/transit
```

---

## Verify Public Health

```bash
PYTHONPATH=. python3 scripts/transit/live_health.py \
  --alert-log-file logs/transit/live_health_alerts.jsonl
curl -fsSI https://sepdynamics.co
curl -fsS https://sepdynamics.co/api/status/network
curl -fsS https://sepdynamics.co/api/status/routes
```

Expected:

- `scripts/transit/live_health.py` reports no failed checks
- site returns `200`
- status endpoints return JSON
- `active_route_count` and route rows are non-zero during service hours
- `feed_status.updated_at` is recent

## Verify Local Services

```bash
docker compose -f docker-compose.transit.yml -f docker-compose.live-host.yml ps
docker stats --no-stream
free -h
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8080/api/status/network
```

Valkey memory check:

```bash
docker exec transit-sentinel-valkey redis-cli INFO MEMORY | grep -E 'used_memory_human|used_memory_rss_human|mem_fragmentation_ratio'
```

History key size check:

```bash
docker exec transit-sentinel-valkey redis-cli --bigkeys -i 0.01
```

Read-model check:

```bash
docker exec transit-sentinel-valkey redis-cli MGET \
  transit:scorecard:live:last \
  transit:trends:live:last \
  transit:dashboard:live:last \
  transit:status:network:last
```

Ingest profiling for a one-off investigation:

```bash
docker exec transit-sentinel-ingest python3 /app/scripts/transit/ingest.py \
  --once \
  --profile \
  --redis redis://valkey:6379/0
```

---

## Health Alerts

`scripts/transit/live_health.py` exits non-zero on failed checks and can emit
deduplicated alerts to a JSONL file and/or webhook. Defaults:

- host memory warning/failure: `85%` / `92%`
- swap warning/failure: `25%` / `60%`
- Valkey memory warning/failure: `75%` / `90%` of maxmemory or the container
  memory limit
- local/public API latency warning/failure: `1500ms` / `5000ms`
- 503 warning: 10 recent `server_busy`/`503` matches

Cron health check used on the hosted droplet:

```cron
*/15 * * * * cd /root/transit && set -a; [ -f .env ] && . ./.env; set +a; PYTHONPATH=. python3 scripts/transit/live_health.py --json --alert-log-file logs/transit/live_health_alerts.jsonl >> logs/transit/live_health.jsonl 2>&1
```

---

## Ops Auth

`/api/status/*` stays public. `/api/transit/*` is the operations surface and
requires bearer auth on the live host. The public frontend should stay
status-only unless an explicit protected console deployment is added.

Host-local `.env` values:

```bash
TRANSIT_API_REQUIRE_AUTH=1
TRANSIT_API_TOKENS='readonly-token:viewer,operator-token:operator,admin-token:admin'
TRANSIT_OPS_CONSOLE_ENABLED=0
TRANSIT_FRONTEND_API_BEARER_TOKEN=
```

## Caddy

The Caddyfile should proxy both apex and `www` to frontend nginx:

```caddyfile
sepdynamics.co, www.sepdynamics.co {
	encode zstd gzip
	reverse_proxy 127.0.0.1:8080
}
```

After changes:

```bash
caddy fmt --overwrite /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
```

## Recovery

Restart only the failing service first:

```bash
docker compose -f docker-compose.transit.yml -f docker-compose.live-host.yml restart api
docker compose -f docker-compose.transit.yml -f docker-compose.live-host.yml restart ingest
docker compose -f docker-compose.transit.yml -f docker-compose.live-host.yml restart archive
docker compose -f docker-compose.transit.yml -f docker-compose.live-host.yml up -d --build frontend
```

If Valkey history has grown too large, manually prune:

```bash
docker exec transit-sentinel-api python3 /app/scripts/transit/prune_history.py --retention 120
```

If the host OOMs, check before restarting:

```bash
journalctl -k -b -1 --no-pager | grep -i 'out of memory\|killed process'
docker events --since 2h --until 1m
```

## Seeded Fallback

Use seeded mode only when live feed repair would distract from an active demo:

```bash
docker compose -f docker-compose.transit.yml -f docker-compose.demo-host.yml up -d valkey
make transit-demo-seed ARGS="--redis redis://localhost:6379/0 --clear-store"
docker compose -f docker-compose.transit.yml -f docker-compose.demo-host.yml --profile demo up -d --build api-demo frontend-demo
```

Return to live mode:

```bash
docker compose -f docker-compose.transit.yml -f docker-compose.demo-host.yml --profile demo stop api-demo frontend-demo
docker compose -f docker-compose.transit.yml -f docker-compose.live-host.yml up -d --build valkey archive ingest api frontend
```

## Notifications

The notification dispatcher is an opt-in Compose profile. Enable it only after
configuring at least one target and a bearer token that can read `/api/transit/*`.
