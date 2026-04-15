# Transit Sentinel `systemd --user` Runtime

These user-service units supervise the MBTA backend outside Docker:

- `transit-sentinel-mbta-archive.service`
- `transit-sentinel-mbta-ingest.service`
- `transit-sentinel-api.service`
- `transit-sentinel-mbta-live.target`

They are optional. The current public `sepdynamics.co` deployment uses Docker
Compose as documented in
[`docs/LIVE_DEPLOYMENT.md`](/sep/transit-sentinel/docs/LIVE_DEPLOYMENT.md).
Use this path only when a host should run archive, ingest, and API as user
services while the frontend is served separately.

## Install

1. Create the user service and config directories:

```bash
mkdir -p ~/.config/systemd/user
mkdir -p ~/.config/transit-sentinel
```

2. Copy the unit files and env file template:

```bash
cp ops/systemd/user/*.service ~/.config/systemd/user/
cp ops/systemd/user/*.target ~/.config/systemd/user/
cp ops/systemd/user/transit-sentinel-mbta.env.example \
  ~/.config/transit-sentinel/transit-sentinel-mbta.env
```

3. Edit `~/.config/transit-sentinel/transit-sentinel-mbta.env` so the repo
   paths and `VALKEY_URL` match the host.

4. Reload the user manager and enable lingering so the services keep running
   after logout:

```bash
systemctl --user daemon-reload
loginctl enable-linger "$USER"
```

5. If the host already has manual `archive.py`, `ingest.py`, or `api.py`
   processes running, stop them before handing control to `systemd --user`.

6. Start and enable the live stack:

```bash
systemctl --user enable --now transit-sentinel-mbta-live.target
```

## Operate

Check status:

```bash
systemctl --user status transit-sentinel-mbta-live.target
systemctl --user status transit-sentinel-mbta-archive.service
systemctl --user status transit-sentinel-mbta-ingest.service
systemctl --user status transit-sentinel-api.service
```

Follow logs:

```bash
journalctl --user -u transit-sentinel-mbta-archive.service -f
journalctl --user -u transit-sentinel-mbta-ingest.service -f
journalctl --user -u transit-sentinel-api.service -f
```

Restart only the API:

```bash
systemctl --user restart transit-sentinel-api.service
```

Stop the full live stack:

```bash
systemctl --user stop transit-sentinel-mbta-live.target
```

## Scope

This path is backend-only. It does not run the React frontend, Caddy, nginx, or
Docker container stack. Do not rely on `npm run dev` as a durable hosted
frontend process.
