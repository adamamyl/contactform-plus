#!/usr/bin/env bash
# scripts/run_e2e.sh — spin up the isolated e2e test stack, run tests, tear down.
#
# Usage:
#   bash scripts/run_e2e.sh               # run all e2e tests
#   bash scripts/run_e2e.sh -k test_name  # pass extra pytest args
#
# Requirements: docker, uv
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_BASE="-f infra/docker-compose.yml -f infra/docker-compose.e2e.yml"

cd "$REPO_ROOT"

echo "=== Starting e2e stack ==="
docker compose $COMPOSE_BASE up -d

echo "=== Waiting for postgres to be healthy ==="
timeout 60 bash -c \
  "until docker compose $COMPOSE_BASE ps --format '{{.Health}}' | grep -q 'healthy'; do sleep 2; done" \
  || { echo "ERROR: postgres did not become healthy in 60s"; docker compose $COMPOSE_BASE down -v; exit 1; }

echo "=== Waiting for services to start ==="
sleep 5

echo "=== Installing e2e test dependencies ==="
cd tests/e2e
uv sync
uv run playwright install chromium --with-deps

echo "=== Running e2e tests ==="
FORM_BASE_URL=http://localhost:8000 \
PANEL_BASE_URL=http://localhost:8001 \
  uv run pytest -v "$@"
EXIT_CODE=$?

cd "$REPO_ROOT"

echo "=== Tearing down e2e stack and wiping test data ==="
docker compose $COMPOSE_BASE down -v

echo "=== Done (exit code: $EXIT_CODE) ==="
exit $EXIT_CODE
