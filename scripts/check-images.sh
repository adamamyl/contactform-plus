#!/usr/bin/env bash
# scripts/check-images.sh — check pre-built and base Docker images for updates
#
# Checks:
#   - image: entries in docker-compose files (pulled services)
#   - FROM lines in Dockerfiles (base images for built services)
#
# Usage:
#   scripts/check-images.sh [OPTIONS]
#
# Options:
#   --pull           Pull updated images and restart affected services
#   -n, --dry-run    Show what would be done without doing it
#   -v, --verbose    Show unchanged images too
#   -h, --help       Show this help

set -euo pipefail; IFS=$'\n\t'

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

PULL=0
DRY_RUN=0
VERBOSE=0

usage() { sed -n '2,13p' "$0" | sed 's/^# \?//'; exit 0; }
die()  { echo "error: $*" >&2; exit 1; }
info() { echo "==> $*"; }
ok()   { echo "  [ok]  $*"; }
upd()  { echo "  [NEW] $*"; }
skip() { echo "  [--]  $*"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)    usage ;;
    --pull)       PULL=1;     shift ;;
    -n|--dry-run) DRY_RUN=1;  shift ;;
    -v|--verbose) VERBOSE=1;  shift ;;
    *) die "unknown option: $1" ;;
  esac
done

cd "${REPO_ROOT}"

# --- Compose file detection ---
COMPOSE_FILES=("${REPO_ROOT}/infra/docker-compose.yml")
if [[ -f "${REPO_ROOT}/infra/docker-compose.wolfcraig.yml" ]]; then
  if hostname | grep -qi "wolfcraig" || [[ "${WOLFCRAIG:-}" == "1" ]]; then
    COMPOSE_FILES+=("${REPO_ROOT}/infra/docker-compose.wolfcraig.yml")
  fi
fi

# --- Collect images ---
declare -A SEEN
COMPOSE_IMAGES=()
DOCKERFILE_IMAGES=()

for f in "${COMPOSE_FILES[@]}"; do
  while IFS= read -r line; do
    img="${line#*image: }"
    img="${img//[[:space:]]/}"
    [[ -z "$img" || "${SEEN[$img]+set}" == "set" ]] && continue
    SEEN[$img]=1
    COMPOSE_IMAGES+=("$img")
  done < <(grep -E "^\s+image:" "$f")
done

while IFS= read -r line; do
  img="${line#FROM }"
  img="${img%% *}"   # drop AS builder
  img="${img//[[:space:]]/}"
  [[ -z "$img" || "${SEEN[$img]+set}" == "set" ]] && continue
  SEEN[$img]=1
  DOCKERFILE_IMAGES+=("$img")
done < <(grep -rh "^FROM " apps/*/Dockerfile infra/swagger/Dockerfile 2>/dev/null | sort -u)

# --- Check an image for updates ---
UPDATED=()

check_image() {
  local img="$1"
  local before after

  before=$(docker image inspect "${img}" --format '{{.Id}}' 2>/dev/null || echo "not_present")

  if [[ $DRY_RUN -eq 1 ]]; then
    skip "${img} (dry-run)"
    return
  fi

  if ! docker pull -q "${img}" >/dev/null 2>&1; then
    echo "  [ERR] ${img} — pull failed" >&2
    return
  fi

  after=$(docker image inspect "${img}" --format '{{.Id}}' 2>/dev/null || echo "")

  if [[ "$before" == "not_present" ]]; then
    upd "${img} (newly pulled)"
    UPDATED+=("${img}")
  elif [[ "$before" != "$after" ]]; then
    upd "${img}"
    UPDATED+=("${img}")
  else
    [[ $VERBOSE -eq 1 ]] && ok "${img}"
  fi
}

info "Checking compose service images..."
for img in "${COMPOSE_IMAGES[@]}"; do
  check_image "${img}"
done

echo
info "Checking Dockerfile base images..."
for img in "${DOCKERFILE_IMAGES[@]}"; do
  check_image "${img}"
done

echo
if [[ ${#UPDATED[@]} -eq 0 ]]; then
  info "All images up to date."
  exit 0
fi

info "${#UPDATED[@]} image(s) updated: ${UPDATED[*]}"

if [[ $PULL -eq 0 && $DRY_RUN -eq 0 ]]; then
  echo
  read -r -p "Rebuild and restart affected services? [y/N] " answer
  [[ "${answer,,}" == "y" ]] && PULL=1
fi

if [[ $PULL -eq 1 && $DRY_RUN -eq 0 ]]; then
  COMPOSE=(docker compose)
  for f in "${COMPOSE_FILES[@]}"; do COMPOSE+=(-f "$f"); done

  info "Rebuilding local services (base images may have changed)..."
  "${COMPOSE[@]}" build

  info "Restarting all services..."
  "${COMPOSE[@]}" up -d
fi
