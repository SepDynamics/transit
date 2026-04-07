# Live Operations Runbook

This runbook is for a durable live MBTA backend on a Linux host.

Use this path when you want the live archive, ingest loop, and API to survive
terminal disconnects and machine reboots. It is the preferred non-container
runtime for host-based live operations.

## Runtime Shape

The live backend is split into three supervised processes:

- `archive.py`: refreshes `data/feeds/mbta/current/` and appends archive windows
- `ingest.py`: writes the current working set into Valkey
- `api.py`: serves `/api/transit/*` and `/api/status/*`

The frontend should be served separately through the existing container/nginx
path or another static host. Do not rely on `npm run dev` as the durable hosted
frontend.

## Operator Vocabulary

The store and API still retain internal regime tokens for replay and scoring,
but the live console should be read through the operator-facing labels:

- `Service irregularity` instead of `corridor_unstable`
- `Severe bunching / service gap` instead of `headway_collapse`
- `Risk score` instead of `hazard`
- `Immediate`, `High`, `Watch`, `Monitor` as the action-priority tiers

## Preferred Supervisor

Use the committed `systemd --user` units under:

- [`ops/systemd/README.md`](/sep/transit-sentinel/ops/systemd/README.md)
- [`ops/systemd/user/transit-sentinel-mbta.env.example`](/sep/transit-sentinel/ops/systemd/user/transit-sentinel-mbta.env.example)

Those units supervise:

- `transit-sentinel-mbta-archive.service`
- `transit-sentinel-mbta-ingest.service`
- `transit-sentinel-api.service`
- `transit-sentinel-mbta-live.target`

## Install

1. Copy the user units and env file template:

```bash
mkdir -p ~/.config/systemd/user
mkdir -p ~/.config/transit-sentinel
cp ops/systemd/user/*.service ~/.config/systemd/user/
cp ops/systemd/user/*.target ~/.config/systemd/user/
cp ops/systemd/user/transit-sentinel-mbta.env.example \
  ~/.config/transit-sentinel/transit-sentinel-mbta.env
```

2. Edit `~/.config/transit-sentinel/transit-sentinel-mbta.env`:

- set `TRANSIT_SENTINEL_HOME` to the repo checkout
- set `VALKEY_URL` to the live Valkey DB
- verify the MBTA feed paths under `data/feeds/mbta/current/`

3. Enable lingering and start the target:

```bash
systemctl --user daemon-reload
loginctl enable-linger "$USER"
systemctl --user enable --now transit-sentinel-mbta-live.target
```

If the host was previously started from shell processes, stop any manual
`archive.py`, `ingest.py`, or `api.py` loops before enabling the target so the
collector and API runtime are not duplicated.

## Verify

Check supervisor state:

```bash
systemctl --user status transit-sentinel-mbta-live.target
systemctl --user status transit-sentinel-mbta-archive.service
systemctl --user status transit-sentinel-mbta-ingest.service
systemctl --user status transit-sentinel-api.service
```

Check live endpoints:

```bash
curl http://127.0.0.1:8000/api/transit/health?scope=live
curl http://127.0.0.1:8000/api/transit/entities?scope=live
curl http://127.0.0.1:8000/api/transit/incidents?scope=live
```

Healthy live state should show:

- non-zero `active_line_count`
- non-zero `vehicle_count`
- recent `feed_status.updated_at`
- active incidents ordered by operational priority

## Logs

Follow logs with journald:

```bash
journalctl --user -u transit-sentinel-mbta-archive.service -f
journalctl --user -u transit-sentinel-mbta-ingest.service -f
journalctl --user -u transit-sentinel-api.service -f
```

## Recovery

If the live dashboard drops to zero again:

1. verify `transit-sentinel-api.service` is still active
2. verify `transit-sentinel-mbta-ingest.service` is still writing fresh snapshots
3. check `/api/transit/health?scope=live` before debugging the frontend
4. restart only the failing unit instead of tearing down the full stack

Examples:

```bash
systemctl --user restart transit-sentinel-api.service
systemctl --user restart transit-sentinel-mbta-ingest.service
systemctl --user restart transit-sentinel-mbta-archive.service
```
