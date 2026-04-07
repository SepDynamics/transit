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

## Prerequisites

- Docker and Docker Compose available on the host
- Python dependencies installed locally if you plan to run the seed command from the repo checkout
- port `6379` available for Valkey
- ports `8000` and `8080` available for the API and frontend

## Seeded Demo Startup

1. Start Valkey only:

```bash
docker compose -f docker-compose.transit.yml up -d valkey
```

2. Seed a clean demo state into Valkey from the preferred archive window or the
   richer committed fallback pack:

```bash
make transit-demo-seed ARGS="--redis redis://localhost:6379/0 --clear-store"
```

3. Start the demo-only API and frontend services:

```bash
docker compose -f docker-compose.transit.yml --profile demo up -d --build api-demo frontend-demo
```

4. Verify the core endpoints:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/status/network
curl http://127.0.0.1:8000/api/status/routes
curl http://127.0.0.1:8000/api/transit/sources
```

5. Open the two primary demo surfaces:

- public status page: `http://127.0.0.1:8080/#status`
- ops console: `http://127.0.0.1:8080/`

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
- `/api/status/network` returns a non-empty payload
- `/api/status/routes` returns route rows
- `/api/transit/sources` shows replay traces
- `#status` loads without frontend errors
- the ops console can switch between `live` and `replay`
- if live mode is enabled on host, `systemctl --user status transit-sentinel-mbta-live.target` is healthy
- the benchmark artifact bundle was refreshed recently enough for the audience

## Recovery

If the demo state drifts or the data looks wrong:

1. stop `api-demo` and `frontend-demo`
2. rerun `make transit-demo-seed ARGS="--redis redis://localhost:6379/0 --clear-store"`
3. start `api-demo` and `frontend-demo` again

If live archive mode is unstable, fall back to seeded-demo mode instead of
trying to repair live collectors during the meeting.

If the live backend is running under `systemd --user`, restart only the failed
service:

```bash
systemctl --user restart transit-sentinel-mbta-archive.service
systemctl --user restart transit-sentinel-mbta-ingest.service
systemctl --user restart transit-sentinel-api.service
```
