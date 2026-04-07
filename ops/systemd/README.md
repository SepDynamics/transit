# Transit Sentinel `systemd --user` Runtime

These user-service units supervise the live MBTA stack outside Docker:

- `transit-sentinel-mbta-archive.service`
- `transit-sentinel-mbta-ingest.service`
- `transit-sentinel-api.service`
- `transit-sentinel-mbta-live.target`

They are designed for a persistent repo checkout on a Linux host where the
frontend is served separately and the backend processes should survive terminal
disconnects.

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

This path is for the backend live stack only. The React frontend should still
be served via the existing container/nginx path or another separate static-host
setup. Do not rely on `npm run dev` as the durable hosted frontend process.
