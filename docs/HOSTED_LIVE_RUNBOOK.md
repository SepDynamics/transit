# Hosted Live Runbook

This runbook is for the live MBTA deployment serving `sepdynamics.co`.

The hosted public surface is live-feed first. It should demonstrate current
public MBTA data at production-like scale: hundreds of vehicles, current trip
updates, active alerts, scored corridors, public route status, and an
operator-facing action queue.

Seeded demo mode is still useful as a fallback and proof workflow, but it is
not the primary posture for the published stakeholder site.

## Current Droplet Deployment

As of 2026-04-14, `sepdynamics.co` runs as a live MBTA feed deployment on the
DigitalOcean Droplet.

Runtime posture:

- live MBTA mode
- Docker Compose services: `valkey`, `archive`, `ingest`, `api`, and `frontend`
- compose files: `docker-compose.transit.yml` and `docker-compose.live-host.yml`
- Valkey: `127.0.0.1:6379`
- API: `127.0.0.1:8000`
- frontend nginx: `127.0.0.1:8080`
- public reverse proxy: Caddy on ports `80` and `443`
- public URL: `https://sepdynamics.co/`
- public status page: `https://sepdynamics.co/#status`
- API through the proxy: `https://sepdynamics.co/api/status/network`
- public stakeholder access with no reverse-proxy authentication
- replay serving disabled on the live host with `TRANSIT_REPLAY_ENABLED=0`

The Caddy config lives at `/etc/caddy/Caddyfile` and proxies
`sepdynamics.co` and `www.sepdynamics.co` to `127.0.0.1:8080` with automatic
TLS.

## Live Startup

Use the live host override for the public MBTA site. It binds app ports to
loopback, disables replay serving, uses the MBTA live system name, and refreshes
only `data/feeds/mbta/current/` instead of growing timestamped archive windows.

```bash
docker compose -f docker-compose.transit.yml -f docker-compose.live-host.yml --profile demo stop api-demo frontend-demo
docker compose -f docker-compose.transit.yml -f docker-compose.live-host.yml up -d --build valkey archive ingest api frontend
```

Do not run the seeded demo services at the same time as the live services. The
demo-only services `api-demo` and `frontend-demo` exist for fallback mode and
should not be the public runtime when `sepdynamics.co` is presenting live MBTA
coverage.

## Verify Live State

Check the public frontend and public API through Caddy:

```bash
curl https://sepdynamics.co/
curl https://www.sepdynamics.co/
curl https://sepdynamics.co/api/status/network
curl https://sepdynamics.co/api/transit/sources
curl https://sepdynamics.co/api/transit/health?scope=live
```

Check the local services directly:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8080/api/status/network
curl http://127.0.0.1:8080/api/status/routes
curl http://127.0.0.1:8080/api/transit/sources
curl http://127.0.0.1:8080/api/transit/health?scope=live
```

Healthy live state should show:

- `available.live=true` and `available.replay=false` on `/api/transit/sources`
- non-zero `vehicle_count`
- non-zero `trip_update_count`
- current `feed_status.updated_at`
- non-zero `visible_line_count`
- route rows on `/api/status/routes`
- public status page loading at `https://sepdynamics.co/#status`
- app ports `6379`, `8000`, and `8080` bound to `127.0.0.1`

## Reverse Proxy

The current Caddyfile shape is:

```caddyfile
sepdynamics.co, www.sepdynamics.co {
	encode zstd gzip

	reverse_proxy 127.0.0.1:8080
}
```

Reload after changes:

```bash
caddy fmt --overwrite /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
```

Check certificate and proxy status:

```bash
systemctl status caddy --no-pager --lines=30
journalctl -u caddy --since "10 minutes ago" --no-pager
```

## Live Checklist

- Valkey is running and reachable on `127.0.0.1:6379`
- `archive`, `ingest`, `api`, and `frontend` containers are running
- `/health` returns healthy API state
- `/api/transit/health?scope=live` returns fresh MBTA feed status
- `/api/status/network` returns a non-empty live status payload
- `/api/status/routes` returns route rows
- `/api/transit/sources` shows live available and replay unavailable
- unauthenticated `https://sepdynamics.co/` returns the frontend
- unauthenticated `https://www.sepdynamics.co/` returns the frontend
- unauthenticated `https://sepdynamics.co/api/status/network` returns JSON
- `#status` loads without frontend errors
- `ss -ltnp` shows app ports `6379`, `8000`, and `8080` bound to `127.0.0.1`
- the benchmark artifact bundle was refreshed recently enough for the audience

## Recovery

If the live dashboard drops to zero or the data looks wrong:

1. Check `/api/transit/health?scope=live`.
2. Confirm the archive and ingest containers are running.
3. Restart only the failing service first.
4. Fall back to seeded mode only when live feed repair would distract from an
   active stakeholder session.

Container recovery examples:

```bash
docker compose -f docker-compose.transit.yml -f docker-compose.live-host.yml restart api
docker compose -f docker-compose.transit.yml -f docker-compose.live-host.yml restart ingest
docker compose -f docker-compose.transit.yml -f docker-compose.live-host.yml restart archive
docker compose -f docker-compose.transit.yml -f docker-compose.live-host.yml up -d --build frontend
```

For a host-based live MBTA backend, prefer the committed `systemd --user`
target instead of ad hoc shell processes:

```bash
systemctl --user enable --now transit-sentinel-mbta-live.target
```

Before switching a host to that target, stop any manually started `archive.py`,
`ingest.py`, or `api.py` loops so the supervised stack is the only live backend
runtime.

If the live backend is running under `systemd --user`, restart only the failed
service:

```bash
systemctl --user restart transit-sentinel-api.service
systemctl --user restart transit-sentinel-mbta-ingest.service
systemctl --user restart transit-sentinel-mbta-archive.service
```

## Benchmark Artifact Refresh

Refresh the shareable benchmark bundle before stakeholder updates or hosted
environment changes:

```bash
make transit-benchmark-artifacts ARGS="--archive-root data/case-packs --labels data/case-packs --artifact-name cross-city-suite"
```

Expected output root:

- `artifacts/benchmarks/cross-city-suite/manifest.json`
- `artifacts/benchmarks/cross-city-suite/calibration_report.json`
- `artifacts/benchmarks/cross-city-suite/calibration_summary.md`
- `artifacts/benchmarks/cross-city-suite/case-packs/*/archive_report.json`

## Seeded Fallback Appendix

Use seeded mode only when you need a deterministic fallback, a regression proof
state, or a meeting-safe backup while live feed repair is deferred.

Start Valkey only:

```bash
docker compose -f docker-compose.transit.yml -f docker-compose.demo-host.yml up -d valkey
```

Seed a clean demo state into Valkey from the preferred archive window or the
committed fallback pack:

```bash
make transit-demo-seed ARGS="--redis redis://localhost:6379/0 --clear-store"
```

Start the demo-only API and frontend services:

```bash
docker compose -f docker-compose.transit.yml -f docker-compose.demo-host.yml --profile demo up -d --build api-demo frontend-demo
```

Verify the seeded fallback:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8080/api/status/network
curl http://127.0.0.1:8080/api/status/routes
curl http://127.0.0.1:8080/api/transit/sources
```

To return to live mode, stop the demo services and restart the live stack:

```bash
docker compose -f docker-compose.transit.yml -f docker-compose.demo-host.yml --profile demo stop api-demo frontend-demo
docker compose -f docker-compose.transit.yml -f docker-compose.live-host.yml up -d --build valkey archive ingest api frontend
```
