# Demo Screenshots

Save screenshots here for offline investor/presentation use. These capture the
live system state when `sepdynamics.co` is serving real MBTA data.

## Recommended Screenshots

1. **public-status-page.png** — The public MBTA status page at
   `https://sepdynamics.co/` showing the network banner, route status cards,
   and live triage queue.

2. **api-status-network.png** — The JSON response from
   `https://sepdynamics.co/api/status/network` showing network severity,
   active route count, and disrupted routes.

3. **api-status-routes.png** — The JSON response from
   `https://sepdynamics.co/api/status/routes` showing per-route severity
   and rider-facing wording.

4. **api-status-feed-quality.png** — The JSON response from
   `https://sepdynamics.co/api/status/feed-quality` showing feed freshness
   checks.

5. **ops-console.png** — The protected operations console (requires bearer
   auth, capture from localhost or a trusted deployment) showing the priority
   queue, map, evidence drawer, and scorecard.

6. **live-health.png** — The output of
   `scripts/transit/live_health.py --json` on the live host.

7. **api-transit-dashboard.png** — The protected JSON response from
   `/api/transit/dashboard` showing the full operations payload.

## Capture Commands

```bash
# Public status page (use browser screenshot tool)
# Open https://sepdynamics.co/ and save as public-status-page.png

# API responses
curl -fsS https://sepdynamics.co/api/status/network | python3 -m json.tool > screenshots/network.json
curl -fsS https://sepdynamics.co/api/status/routes | python3 -m json.tool > screenshots/routes.json
curl -fsS https://sepdynamics.co/api/status/feed-quality | python3 -m json.tool > screenshots/feed-quality.json

# Live health
PYTHONPATH=. python3 scripts/transit/live_health.py --json > screenshots/live-health.json
