# Live Deployment

This runbook covers the hosted MBTA live deployment behind `sepdynamics.co`.
The host checkout is expected at `~/transit`.

## Current Shape

- public URL: `https://sepdynamics.co/`
- public status API: `https://sepdynamics.co/api/status/network`
- runtime: Docker Compose
- compose files: `docker-compose.transit.yml` plus `docker-compose.live-host.yml`
- services: `valkey`, `archive`, `ingest`, `api`, `frontend`
- public reverse proxy: Caddy on ports `80` and `443`
- app ports bound to loopback:
  - Valkey: `127.0.0.1:6379`
  - API: `127.0.0.1:8000`
  - frontend nginx: `127.0.0.1:8080`
- replay disabled on the live host with `TRANSIT_REPLAY_ENABLED=0`

The live host is not a seeded demo environment. It should show current MBTA
public feed state at production-like scale.

## Resource Posture

The live override intentionally reduces pressure on the host:

- Valkey AOF disabled; RDB snapshot every 300 seconds after a write.
- API generic cache capped at six entries.
- API scorecard reads capped to 60 samples and cached for 60 seconds.
- Ingest materializes live read models for the 60-sample scorecard, trends,
  dashboard, and public network status keys.
- API concurrency capped at four active requests with an eight-request queue.
- Ingest runs every 20 seconds.
- History writes run every 60 seconds.
- Rolling history retention is 120 samples per key.
- Container memory limits are set for Valkey, API, ingest, archive, and
  frontend.

If memory pressure returns, check Valkey history size before increasing host
size. The intended first response is pruning or tightening retention, not
letting history grow unbounded.

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

## Verify Public Health

```bash
PYTHONPATH=. python3 scripts/transit/live_health.py
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

## Ops Auth

`/api/status/*` stays public. `/api/transit/*` is the operations surface and
should require bearer auth before the console is shared outside trusted users.
Set both values in the host shell or deployment environment before rebuilding:

```bash
export TRANSIT_API_REQUIRE_AUTH=1
export TRANSIT_API_TOKENS='readonly-token:viewer,operator-token:operator,admin-token:admin'
export TRANSIT_FRONTEND_API_BEARER_TOKEN='readonly-token'
```

The frontend container injects `TRANSIT_FRONTEND_API_BEARER_TOKEN` into the
runtime config as `API_BEARER_TOKEN`. Do not commit real tokens.

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

If Valkey history has grown too large, prune rolling histories to the live
retention target:

```bash
docker exec transit-sentinel-api python3 /app/scripts/transit/prune_history.py --retention 120
```

For a weekly guardrail, schedule the same command through cron or a systemd
timer. Run it once with `--dry-run` first after any retention change.

If the host OOMs again, check the previous boot before restarting blindly:

```bash
journalctl -k -b -1 --no-pager | grep -i 'out of memory\|killed process'
docker events --since 2h --until 1m
```

## Seeded Fallback

Use seeded mode only when live feed repair would distract from an active demo or
test session:

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

## Optional Systemd Backend Path

The `ops/systemd/` units are still useful for a host-supervised backend outside
Docker. They are not the current public `sepdynamics.co` runtime. Use them only
when you intentionally want archive, ingest, and API as user services while the
frontend is hosted separately.
