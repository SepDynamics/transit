#!/usr/bin/env bash
# Rebuild and restart the complete live Transit Sentinel stack.
#
# Run this from any directory: ./deploy.sh
# The host-local .env file is read automatically by Docker Compose.

set -Eeuo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$repo_dir"

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
mkdir -p data/feeds/mbta/current data/evidence logs/transit
if [ "$(id -u)" -eq 0 ]; then
  chown -R 999:999 data/feeds data/evidence logs/transit
elif ! { [ -w data/feeds ] && [ -w data/evidence ] && [ -w logs/transit ]; }; then
  command -v sudo >/dev/null 2>&1 || die "data/feeds, data/evidence, or logs/transit is not writable; rerun as root."
  printf '%s\n' 'Preparing writable feed and log directories (sudo may prompt)...'
  sudo chown -R 999:999 data/feeds data/evidence logs/transit
fi

printf '%s\n' 'Stopping any seeded-demo containers...'
"${compose[@]}" --profile demo stop api-demo frontend-demo >/dev/null 2>&1 || true

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

"${compose[@]}" ps
printf '%s\n' 'Deployment complete. The frontend is listening on http://127.0.0.1:8080.'
