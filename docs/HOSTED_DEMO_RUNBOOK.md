# Hosted Demo Runbook

This runbook is for a stable hosted demo of the public service-status surface.

The hosted demo should prefer recent archive-backed MBTA data for startup, then
fall back to the richer committed MBTA overnight pack when no continuous archive
corpus is present. Layer live archive jobs on only when you explicitly want
live feed freshness.

## Recommended Mode

Use seeded-demo mode as the default external demo posture:

- predictable startup state
- no dependency on live feed health during setup
- replay traces already available in the console
- public status page can be validated before any archive collectors start

Use live archive mode only after the seeded demo is already healthy.

## Current Droplet Deployment

As of 2026-04-14, the hosted investor demo runs on the DigitalOcean Droplet
serving `sepdynamics.co`.

Runtime posture:

- seeded-demo mode only
- Docker Compose services: `valkey`, `api-demo`, and `frontend-demo`
- compose files: `docker-compose.transit.yml` and `docker-compose.demo-host.yml`
- Valkey: `127.0.0.1:6379`
- API: `127.0.0.1:8000`
- frontend nginx: `127.0.0.1:8080`
- public reverse proxy: Caddy on ports `80` and `443`
- public URL: `https://sepdynamics.co/`
- public status page: `https://sepdynamics.co/#status`
- API through the proxy: `https://sepdynamics.co/api/status/network`
- public stakeholder access with no reverse-proxy authentication

The Caddy config lives at `/etc/caddy/Caddyfile` and proxies
`sepdynamics.co` and `www.sepdynamics.co` to `127.0.0.1:8080` with automatic
TLS.

## Prerequisites

- Docker and Docker Compose available on the host
- Python dependencies installed locally if you plan to run the seed command from the repo checkout
- port `6379` available for Valkey
- ports `8000` and `8080` available for the API and frontend
- Caddy available and active when exposing the demo publicly

On small droplets, keep swap enabled before rebuilding the backend image. The
native extension compile can exhaust a 2 GB host without swap. This deployment
uses `/swapfile`, persisted through `/etc/fstab`.

## Seeded Demo Startup

1. Start Valkey only:

```bash
docker compose -f docker-compose.transit.yml -f docker-compose.demo-host.yml up -d valkey
```

2. Seed a clean demo state into Valkey from the preferred archive window or the
   richer committed fallback pack:

```bash
make transit-demo-seed ARGS="--redis redis://localhost:6379/0 --clear-store"
```

3. Start the demo-only API and frontend services:

```bash
docker compose -f docker-compose.transit.yml -f docker-compose.demo-host.yml --profile demo up -d --build api-demo frontend-demo
```

4. Verify the core endpoints:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8080/api/status/network
curl http://127.0.0.1:8080/api/status/routes
curl http://127.0.0.1:8080/api/transit/sources
```

5. Open the two primary demo surfaces:

- public status page: `http://127.0.0.1:8080/#status`
- ops console: `http://127.0.0.1:8080/`

6. Verify the hosted URL:

```bash
curl https://sepdynamics.co/
curl https://www.sepdynamics.co/
curl https://sepdynamics.co/api/status/network
curl https://sepdynamics.co/api/transit/sources
```

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

## Benchmark Artifact Refresh

Refresh the shareable benchmark bundle before demos, investor updates, or
hosted environment changes:

```bash
make transit-benchmark-artifacts ARGS="--archive-root data/case-packs --labels data/case-packs --artifact-name cross-city-suite"
```

Expected output root:

- `artifacts/benchmarks/cross-city-suite/manifest.json`
- `artifacts/benchmarks/cross-city-suite/calibration_report.json`
- `artifacts/benchmarks/cross-city-suite/calibration_summary.md`
- `artifacts/benchmarks/cross-city-suite/case-packs/*/archive_report.json`

## Live Archive Mode

Do not switch the hosted demo into live archive mode until seeded-demo mode is
already healthy.

When you want live feed freshness:

1. keep the seeded demo command available as a rollback path
2. validate MBTA archive health first
3. validate LA Metro websocket capture health separately
4. only then start the default `archive`, `ingest`, `api`, and `frontend` services

For the public MBTA live site, use the live host override instead of the seeded
demo profile. It binds app ports to loopback, disables replay serving, and
refreshes only `data/feeds/mbta/current/` rather than writing timestamped archive
windows:

```bash
docker compose -f docker-compose.transit.yml -f docker-compose.live-host.yml --profile demo stop api-demo frontend-demo
docker compose -f docker-compose.transit.yml -f docker-compose.live-host.yml up -d --build valkey archive ingest api frontend
```

For a host-based live MBTA backend, prefer the committed `systemd --user`
target instead of ad hoc shell processes:

```bash
systemctl --user enable --now transit-sentinel-mbta-live.target
```

Before switching a host to that target, stop any manually started
`archive.py`, `ingest.py`, or `api.py` loops so the supervised stack is the
only live backend runtime.

When notification capture is enabled, new qualifying incidents can also persist
archive-backed proof windows under `artifacts/proof-windows/` for later replay
and lead-time review.

The demo-only services `api-demo` and `frontend-demo` exist so a hosted demo can
run without `archive` and `ingest` overriding the seeded state.

## Pre-Demo Checklist

- Valkey is running and reachable on `127.0.0.1:6379`
- `make transit-demo-seed` completed without errors
- `/health` returns `status=ok` or an expected seeded status
- `http://127.0.0.1:8080/api/status/network` returns a non-empty payload
- `http://127.0.0.1:8080/api/status/routes` returns route rows
- `http://127.0.0.1:8080/api/transit/sources` shows replay traces
- unauthenticated `https://sepdynamics.co/` returns the frontend
- unauthenticated `https://www.sepdynamics.co/` returns the frontend
- unauthenticated `https://sepdynamics.co/api/status/network` returns JSON
- `#status` loads without frontend errors
- the ops console can switch between `live` and `replay`
- `ss -ltnp` shows app ports `6379`, `8000`, and `8080` bound to `127.0.0.1`
- if live mode is enabled on host, `systemctl --user status transit-sentinel-mbta-live.target` is healthy
- the benchmark artifact bundle was refreshed recently enough for the audience

## Recovery

If the demo state drifts or the data looks wrong:

1. stop `api-demo` and `frontend-demo`
2. rerun `make transit-demo-seed ARGS="--redis redis://localhost:6379/0 --clear-store"`
3. start `api-demo` and `frontend-demo` again

Current recovery commands:

```bash
docker compose -f docker-compose.transit.yml -f docker-compose.demo-host.yml --profile demo stop api-demo frontend-demo
make transit-demo-seed ARGS="--redis redis://localhost:6379/0 --clear-store"
docker compose -f docker-compose.transit.yml -f docker-compose.demo-host.yml --profile demo up -d --build api-demo frontend-demo
```

If live archive mode is unstable, fall back to seeded-demo mode instead of
trying to repair live collectors during the meeting.

If the live backend is running under `systemd --user`, restart only the failed
service:

```bash
systemctl --user restart transit-sentinel-mbta-archive.service
systemctl --user restart transit-sentinel-mbta-ingest.service
systemctl --user restart transit-sentinel-api.service
```
