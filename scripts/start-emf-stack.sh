#!/usr/bin/env bash
# scripts/start-emf-stack.sh — run docker compose with the wolfcraig override baked in.
#
# Usage:
#   scripts/start-emf-stack.sh [OPTIONS] [COMPOSE ARGS...]
#
# Options:
#   -n, --dry-run    Print the command without running it
#   -x, --debug      Trace shell execution (set -x)
#   -v, --verbose    Pass --progress=plain to docker compose
#   -s, --silent     Suppress all script output (overrides --verbose/--debug)
#   -h, --help       Show this help
#
# Examples:
#   scripts/start-emf-stack.sh up -d
#   scripts/start-emf-stack.sh up -d --force-recreate form
#   scripts/start-emf-stack.sh logs -f form
#   scripts/start-emf-stack.sh down
#   scripts/start-emf-stack.sh --dry-run up -d

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="docker compose -f infra/docker-compose.yml -f infra/docker-compose.wolfcraig.yml"

DRY_RUN=0
DEBUG=0
VERBOSE=0
SILENT=0

usage() {
  sed -n '2,14p' "$0" | sed 's/^# \?//'
  exit 0
}

die() { echo "error: $*" >&2; exit 1; }

# Parse options (order-independent; stop at first non-option arg)
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)    usage ;;
    -n|--dry-run) DRY_RUN=1;  shift ;;
    -x|--debug)   DEBUG=1;    shift ;;
    -v|--verbose) VERBOSE=1;  shift ;;
    -s|--silent)  SILENT=1;   shift ;;
    -*) die "unknown option: $1" ;;
    *)  break ;;
  esac
done

[[ $SILENT -eq 1 && ($VERBOSE -eq 1 || $DEBUG -eq 1) ]] \
  && die "--silent conflicts with --verbose / --debug"

[[ $DEBUG -eq 1 ]] && set -x
[[ $VERBOSE -eq 1 ]] && COMPOSE="$COMPOSE --progress=plain"

cd "$REPO_ROOT"

CMD="$COMPOSE $*"

if [[ $DRY_RUN -eq 1 ]]; then
  echo "$CMD"
  exit 0
fi

[[ $SILENT -eq 0 ]] && echo "+ $CMD"
eval "$CMD"
