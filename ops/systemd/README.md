# Transit Sentinel `systemd --user` Runtime

These optional user-service units supervise a transit backend outside Docker.
Two profiles are checked in:

- MBTA: `transit-sentinel-mbta-archive.service`,
  `transit-sentinel-mbta-ingest.service`, `transit-sentinel-api.service`, and
  `transit-sentinel-mbta-live.target`.
- LA Metro authorized pilot: `transit-sentinel-lametro-archive.service`,
  `transit-sentinel-lametro-ingest.service`,
  `transit-sentinel-lametro-api.service`, and
  `transit-sentinel-lametro-live.target`.

They are optional. The current public `sepdynamics.co` deployment uses Docker
Compose as documented in
[`docs/LIVE_DEPLOYMENT.md`](/sep/transit-sentinel/docs/LIVE_DEPLOYMENT.md), is
MBTA-only, and refreshes only the current feed set. The presence of LA Metro
units here does not mean the public host runs them. Use this path only for an
approved host that should run archive, ingest, and API as user services while
the frontend is served separately.

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
chmod 600 ~/.config/transit-sentinel/transit-sentinel-mbta.env
```

For an authorized LA Metro pilot, copy its template instead:

```bash
cp ops/systemd/user/transit-sentinel-lametro.env.example \
  ~/.config/transit-sentinel/transit-sentinel-lametro.env
chmod 600 ~/.config/transit-sentinel/transit-sentinel-lametro.env
```

3. Edit the selected file under `~/.config/transit-sentinel/` so the repo
   paths and `VALKEY_URL` match the host. LA Metro also requires a valid
   `SWIFTLY_API_KEY` in that host-local file. Never put a real credential in an
   `*.env.example` file or anywhere else in the repository.

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

For an authorized LA Metro pilot, start
`transit-sentinel-lametro-live.target` instead. Do not start both profiles
against the same ports or Valkey database.

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

Use the corresponding `transit-sentinel-lametro-*` unit names when operating
an LA Metro pilot.

## Archive Modes

Both checked-in user-service environment examples default to
`TRANSIT_ARCHIVE_CURRENT_ONLY=1`. The archive units inherit that setting through
`scripts/transit/archive.py`; they do not override it on the command line. The
public Compose override is also current-only and remains an MBTA deployment.

The LA Metro template preconfigures `TRANSIT_ARCHIVE_RETENTION_DAYS=90`, but
that setting is inert while current-only mode is enabled. Set current-only to
`0` only after the feed license permits retention and host disk capacity is
verified. The archive service then prunes expired timestamped snapshots after
successful captures while preserving static-feed snapshots referenced by
retained manifests. Raw archive retention is separate from Valkey's rolling
history settings.

Both templates also set `TRANSIT_EVIDENCE_RETENTION_DAYS=90`. Unlike raw
capture, this applies while archive current-only mode is enabled because ingest
continues writing derived operational evidence. Back up any approved long-term
partitions before deploying the setting; the next successful evidence write
removes older UTC-date partitions.

## Credential Incident Response

A credential-like Swiftly value was committed in
`ops/systemd/user/transit-sentinel-lametro.env.example` before the template was
sanitized. Treat that credential as exposed even after the current branch no
longer contains it.

1. Revoke the exposed credential with Swiftly, issue a replacement, and update
   only the host-local file under `~/.config/transit-sentinel/`. Restart the LA
   archive unit and verify successful feed fetches.
2. Review deployment logs and access records for unexpected use. Removing the
   text from the current branch does not invalidate the old credential.
3. Have a repository administrator coordinate a history rewrite from a fresh
   mirror clone. One conservative approach is to remove the affected template
   from every ref, then restore only the sanitized template in a new commit:

   ```bash
   git filter-repo --invert-paths \
     --path ops/systemd/user/transit-sentinel-lametro.env.example
   ```

4. Force-update all affected branches and tags under repository-admin controls,
   purge or close stale pull-request refs and cached views with the hosting
   provider, and require collaborators to re-clone instead of merging old
   history back in.
5. Re-run secret scanning across all refs and deployment artifacts. History
   rewriting is cleanup, not a substitute for revocation.

Do not perform the rewrite casually on a shared checkout: it changes commit IDs
and requires coordinated force pushes and collaborator recovery.

## Scope

This path is backend-only. It does not run the React frontend, Caddy, nginx, or
Docker container stack. Do not rely on `npm run dev` as a durable hosted
frontend process.
