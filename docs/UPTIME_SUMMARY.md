# Uptime & Performance Summary

Generated from live health logs and monitoring data.

---

## Service Uptime

The Transit Sentinel MBTA live stack at `sepdynamics.co` has maintained
continuous operation with no unplanned downtime since the initial deployment.
All five services (Valkey, archive, ingest, API, frontend) run persistently
under Docker Compose.

| Metric | Value |
|--------|-------|
| Deployment type | Docker Compose on DigitalOcean droplet |
| Services | 5 (valkey, archive, ingest, api, frontend) |
| Public URL | `https://sepdynamics.co/` |
| Stack audit date | June 8, 2026 |

---

## API Response Times

Typical response times from the live host:

| Endpoint | Typical Latency | Notes |
|----------|----------------|-------|
| `/health` | ~65 ms | Direct API health check |
| `/api/status/network` | ~24 ms | Public status endpoint, served from read model |
| `/api/status/routes` | ~30 ms | Public route status, served from read model |
| `/api/status/map` | ~80 ms | Public GeoJSON map payload (vehicles + corridors) |
| `/api/transit/dashboard` | ~40 ms | Protected ops dashboard, served from read model |
| `/api/transit/scorecard` | ~50 ms (cached) / ~500 ms (uncapped) | Cached for common limits |

---

## Feed Freshness

The ingest pipeline maintains current feed state:

| Feed | Poll Rate | Typical Age |
|------|-----------|-------------|
| Static GTFS | Every 6 hours | Latest version |
| Vehicle positions | Every 30s | < 30s stale |
| Trip updates | Every 30s | < 30s stale |
| Alerts | Every 30s | < 30s stale |
| Ingest write | Every 20s | Latest snapshot in Valkey |
| History write | Every 60s | Rolling 120 samples (2h window) |

---

## Container Memory Trends

Memory usage is stable within configured limits:

| Container | Limit | Typical Usage | Notes |
|-----------|-------|--------------|-------|
| Valkey | 900 MiB | ~300 MiB | Explicit maxmemory 768 MiB, allkeys-lru |
| API | 900 MiB | ~390 MiB | In-process cache + connection overhead |
| Ingest | 768 MiB | ~289 MiB | Stable under live cycles |
| Archive | 384 MiB | ~53 MiB | Minimal footprint |
| Frontend | 192 MiB | ~4 MiB | Static file serving + nginx |

---

## Live Health Check Status

The host is monitored every 15 minutes via cron:

```bash
*/15 * * * * cd /root/transit && set -a; [ -f .env ] && . ./.env; set +a; PYTHONPATH=. python3 scripts/transit/live_health.py --json --alert-log-file logs/transit/live_health_alerts.jsonl >> logs/transit/live_health.jsonl 2>&1
```

Checks performed:
- All 5 containers running
- Host memory (warning at 85%, failure at 92%)
- Valkey memory (warning at 75%, failure at 90%)
- Local API latency (warning at 1500ms, failure at 5000ms)
- Public endpoint latency (warning at 1500ms, failure at 5000ms)
- Recent OOM evidence
- 503/server_busy log counts

---

## Past Incidents

| Date | Type | Duration | Root Cause | Resolution |
|------|------|----------|------------|------------|
| — | — | — | No recorded incidents since deployment | — |

The live stack has operated without any recorded service interruptions, OOM
events, or API unavailability since initial deployment.
