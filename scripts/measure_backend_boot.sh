#!/usr/bin/env bash
# Measure the backend image's cold boot: container start -> first HTTP 200
# on the liveness endpoint.
#
# This is the measurement the HEALTHCHECK --start-period in
# docker/backend/Dockerfile is derived from, and the one CI enforces a
# fraction of via .github/actions/smoke-test-backend-image. Re-run it
# before changing either number: the budget is only defensible while it
# keeps its headroom over an actual measurement.
#
# It reproduces the shipped topology deliberately -- the CPU and memory
# limits the generated compose file applies to the backend service -- so
# the number means something for the machine a user runs on. Boot is
# CPU-bound (interpreter start plus the eager import graph), so an
# unconstrained measurement flatters the image by several multiples.
#
# Usage:
#   scripts/measure_backend_boot.sh [IMAGE]
#
# Environment:
#   CPUS       CPU limit (default 2, matching compose.yml.tmpl)
#   MEMORY     memory limit (default 4g, matching compose.yml.tmpl)
#   HOST_PORT  host port to bind (default 3001)
#   RUNS       number of consecutive boots to time (default 1)
set -euo pipefail

IMAGE="${1:-ghcr.io/aureliolo/synthorg-backend:latest}"
CPUS="${CPUS:-2}"
MEMORY="${MEMORY:-4g}"
HOST_PORT="${HOST_PORT:-3001}"
RUNS="${RUNS:-1}"

DOCKERFILE="$(dirname "$0")/../docker/backend/Dockerfile"
START_PERIOD=$(sed -n 's/.*--start-period=\([0-9][0-9]*\)s.*/\1/p' "$DOCKERFILE" | head -1)

echo "image:        $IMAGE"
echo "limits:       ${CPUS} CPU, ${MEMORY} memory (compose backend limits)"
echo "health budget: ${START_PERIOD:-unknown}s (HEALTHCHECK --start-period)"
echo

measure_one() {
  # No volume and no DATABASE_URL: the image falls back to an ephemeral
  # SQLite database, so every migration applies on every run. That is
  # the slow path, which is the one worth measuring.
  local cid
  cid=$(docker run -d -p "${HOST_PORT}:3001" \
    --cpus "$CPUS" --memory "$MEMORY" \
    -e SYNTHORG_PAGINATION_CURSOR_SECRET=measurement-not-a-real-cursor-secret \
    "$IMAGE")
  # shellcheck disable=SC2064  # cid must expand now, not at trap time
  trap "docker rm -f '$cid' >/dev/null 2>&1 || true" RETURN

  local started
  started=$SECONDS
  while true; do
    if [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
            "http://localhost:${HOST_PORT}/api/v1/healthz" 2>/dev/null)" = "200" ]; then
      echo $((SECONDS - started))
      return 0
    fi
    if ! docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null | grep -q true; then
      echo "container exited before answering; last logs:" >&2
      docker logs "$cid" 2>&1 | tail -30 >&2
      return 1
    fi
    sleep 1
  done
}

for run in $(seq 1 "$RUNS"); do
  boot=$(measure_one)
  if [ -n "$START_PERIOD" ]; then
    printf 'run %s: first 200 after %ss (%s%% of the %ss health budget)\n' \
      "$run" "$boot" "$((boot * 100 / START_PERIOD))" "$START_PERIOD"
  else
    printf 'run %s: first 200 after %ss\n' "$run" "$boot"
  fi
done
