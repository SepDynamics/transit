#!/usr/bin/env bash
# Rebuild and restart the complete live Transit Sentinel stack.
#
# Run this from any directory: ./deploy.sh
# The host-local .env file is read automatically by Docker Compose.

set -Eeuo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$repo_dir"

lametro_env="${TRANSIT_LAMETRO_ENV_FILE:-/root/.config/transit-sentinel/transit-sentinel-lametro.env}"
if [ -r "$lametro_env" ]; then
  set -a
  # Host-local LA credentials are exported only for Compose interpolation.
  # shellcheck disable=SC1090
  . "$lametro_env"
  set +a
fi

compose=(
  docker compose
  -f docker-compose.transit.yml
  -f docker-compose.live-host.yml
)
services=(valkey archive ingest api frontend)
containers=(
  transit-sentinel-valkey
  transit-sentinel-archive
  transit-sentinel-ingest
  transit-sentinel-api
  transit-sentinel-frontend
)

die() {
  printf 'deploy: %s\n' "$*" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || die "Docker is not installed or is not on PATH."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is not available."

# archive and ingest run as the non-root container user (uid 999 on the
# production image), so their bind-mounted state directories must be writable.
mkdir -p data/feeds/lametro/current data/evidence logs/transit
if [ "$(id -u)" -eq 0 ]; then
  chown -R 999:999 data/feeds data/evidence logs/transit
elif ! { [ -w data/feeds ] && [ -w data/evidence ] && [ -w logs/transit ]; }; then
  command -v sudo >/dev/null 2>&1 || die "data/feeds, data/evidence, or logs/transit is not writable; rerun as root."
  printf '%s\n' 'Preparing writable feed and log directories (sudo may prompt)...'
  sudo chown -R 999:999 data/feeds data/evidence logs/transit
fi

printf '%s\n' 'Stopping any seeded-demo containers...'
"${compose[@]}" --profile demo stop api-demo frontend-demo >/dev/null 2>&1 || true

printf '%s\n' 'Asserting an LA-only live configuration...'
"${compose[@]}" config --format json | python3 -c '
import json, sys
services = json.load(sys.stdin)["services"]
for name in ("archive", "ingest", "api"):
    agency = services[name].get("environment", {}).get("TRANSIT_AGENCY")
    if agency != "lametro":
        raise SystemExit(f"deploy: {name} resolved TRANSIT_AGENCY={agency!r}, expected lametro")
system_name = services["api"].get("environment", {}).get("TRANSIT_SYSTEM_NAME", "")
if "LA Metro" not in system_name or "MBTA" in system_name:
    raise SystemExit(f"deploy: API system identity is not LA-only: {system_name!r}")
'

printf '%s\n' 'Building and recreating the live services...'
"${compose[@]}" up -d --build --force-recreate --remove-orphans "${services[@]}"

printf '%s\n' 'Waiting for containers to become ready...'
deadline=$((SECONDS + 180))
while (( SECONDS < deadline )); do
  all_ready=1
  for container in "${containers[@]}"; do
    state="$(docker inspect --format '{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$container" 2>/dev/null || true)"
    case "$state" in
      'true|'|'true|healthy') ;;
      *) all_ready=0; break ;;
    esac
  done
  if (( all_ready )); then
    break
  fi
  sleep 2
done

if (( ! all_ready )); then
  "${compose[@]}" ps
  "${compose[@]}" logs --tail=100 api frontend ingest archive >&2 || true
  die "services did not become ready within 180 seconds"
fi

printf '%s\n' 'Running local API and frontend smoke checks...'
PYTHONPATH=. python3 scripts/transit/smoke.py --base-url http://127.0.0.1:8000
curl --fail --silent --show-error http://127.0.0.1:8080/health >/dev/null
curl --fail --silent --show-error http://127.0.0.1:8080/api/status/network >/dev/null

printf '%s\n' 'Loading curated LA case packs as replay traces (live state is preserved)...'
"${compose[@]}" run --rm --no-deps ingest \
  python3 /app/scripts/transit/demo_seed.py \
  --redis redis://valkey:6379/0 \
  --skip-live \
  --replay-case-pack-catalog /app/data/case-packs/lametro \
  --trace-prefix casepack \
  --output /app/logs/lametro-case-pack-seed.json

printf '%s\n' 'Verifying LA replay traces through the public read-only API...'
for trace_id in \
  casepack-lametro-saturday-mixed-alert-controls \
  casepack-lametro-weekday-bus-instability-sequence
do
  TRACE_ID="$trace_id" python3 - <<'PY'
import json
import os
from urllib.parse import urlencode
from urllib.request import urlopen

trace_id = os.environ["TRACE_ID"]
query = urlencode({"scope": "replay", "trace_id": trace_id})
with urlopen(f"http://127.0.0.1:8000/api/status/map?{query}", timeout=20) as response:
    payload = json.load(response)
if payload.get("trace_id") != trace_id:
    raise SystemExit(f"deploy: replay trace unavailable: {trace_id}")
if int(payload.get("corridor_count") or 0) < 1:
    raise SystemExit(f"deploy: replay trace is empty: {trace_id}")
print(f"ok replay trace {trace_id}")
PY
done

"${compose[@]}" ps
printf '%s\n' 'Deployment complete. The frontend is listening on http://127.0.0.1:8080.'
