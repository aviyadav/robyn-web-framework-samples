#!/usr/bin/env sh
# Deploys the Inventory API (+ Postgres + Redis) with Docker Compose, waits
# for it to become healthy, runs the load test, then tears the stack back
# down. Pass extra arguments straight through to load_test.py, e.g.:
#
#   ./loadtest/run.sh --scenario mixed --requests 200 --concurrency 20
#   ./loadtest/run.sh --keep-up   # leave containers running afterwards
set -eu

cd "$(dirname "$0")/.."

KEEP_UP=0
ARGS=""
for arg in "$@"; do
    if [ "$arg" = "--keep-up" ]; then
        KEEP_UP=1
    else
        ARGS="$ARGS $arg"
    fi
done

COMPOSE="docker compose -f docker-compose.yml -f loadtest/docker-compose.loadtest.yml"

cleanup() {
    if [ "$KEEP_UP" -eq 0 ]; then
        echo "Tearing down docker compose stack..."
        $COMPOSE down
    else
        echo "Leaving the docker compose stack running (--keep-up was passed)."
    fi
}
trap cleanup EXIT

echo "Building and starting the API stack (Postgres + Redis + API)..."
$COMPOSE up -d --build

echo "Running load test (waits up to 60s for the API to become healthy first)..."
# shellcheck disable=SC2086
uv run loadtest/load_test.py --wait-timeout 60 $ARGS
