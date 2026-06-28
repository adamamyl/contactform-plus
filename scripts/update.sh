#!/usr/bin/env bash
# scripts/update.sh — pull latest, apply map patch, sync .env, rebuild, restart
#
# Usage:
#   scripts/update.sh [OPTIONS]
#
# Options:
#   --no-cache       Pass --no-cache to docker compose build
#   --skip-pull      Skip git pull (useful if already pulled)
#   --skip-patch     Skip map patch application
#   --skip-env       Skip .env sync from .env-example
#   -n, --dry-run    Print actions without running them
#   -v, --verbose    Show verbose build output
#   -h, --help       Show this help

set -euo pipefail; IFS=$'\n\t'

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PATCH_FILE="${REPO_ROOT}/map/embed-readonly-view-postmessage.patch"

NO_CACHE=0
SKIP_PULL=0
SKIP_PATCH=0
SKIP_ENV=0
DRY_RUN=0
VERBOSE=0

usage() { sed -n '2,15p' "$0" | sed 's/^# \?//'; exit 0; }
die()   { echo "error: $*" >&2; exit 1; }
info()  { echo "==> $*"; }
run()   {
  if [[ $DRY_RUN -eq 1 ]]; then echo "[dry-run] $*"; return; fi
  "$@"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)       usage ;;
    --no-cache)      NO_CACHE=1;    shift ;;
    --skip-pull)     SKIP_PULL=1;   shift ;;
    --skip-patch)    SKIP_PATCH=1;  shift ;;
    --skip-env)      SKIP_ENV=1;    shift ;;
    -n|--dry-run)    DRY_RUN=1;     shift ;;
    -v|--verbose)    VERBOSE=1;     shift ;;
    *) die "unknown option: $1" ;;
  esac
done

cd "${REPO_ROOT}"

# --- Compose file detection ---
COMPOSE=(docker compose -f "${REPO_ROOT}/infra/docker-compose.yml")
if [[ -f "${REPO_ROOT}/infra/docker-compose.wolfcraig.yml" ]]; then
  if hostname | grep -qi "wolfcraig" || [[ "${WOLFCRAIG:-}" == "1" ]]; then
    COMPOSE+=(-f "${REPO_ROOT}/infra/docker-compose.wolfcraig.yml")
    info "wolfcraig detected — using wolfcraig compose override"
  fi
fi
[[ $VERBOSE -eq 1 ]] && COMPOSE+=(--progress=plain)

# --- 1. Git pull ---
if [[ $SKIP_PULL -eq 0 ]]; then
  info "Pulling latest from origin..."
  run git pull --ff-only
fi

# --- 2. Map patch ---
if [[ $SKIP_PATCH -eq 0 ]]; then
  MAP_PATH="${EMF_MAP_PATH:-$(cd "${REPO_ROOT}/../emf/map/web" 2>/dev/null && pwd)}"
  if [[ ! -d "${MAP_PATH}" ]]; then
    echo "warn: map path '${MAP_PATH}' not found — skipping patch (set EMF_MAP_PATH to override)" >&2
  else
    info "Pulling map repo..."
    # Reset any previously applied patch before pulling, then re-apply
    if ! git -C "${MAP_PATH}" apply --check "${PATCH_FILE}" 2>/dev/null; then
      info "Resetting map patch before pull..."
      run git -C "${MAP_PATH}" apply --reverse "${PATCH_FILE}"
    fi
    run git -C "${MAP_PATH}" pull --ff-only
    info "Applying map patch..."
    run git -C "${MAP_PATH}" apply "${PATCH_FILE}"
  fi
fi

# --- 3. Sync .env-example → .env (additive only) ---
if [[ $SKIP_ENV -eq 0 ]]; then
  ENV_FILE="${REPO_ROOT}/.env"
  ENV_EXAMPLE="${REPO_ROOT}/.env-example"
  if [[ ! -f "${ENV_EXAMPLE}" ]]; then
    echo "warn: .env-example not found — skipping env sync" >&2
  elif [[ ! -f "${ENV_FILE}" ]]; then
    info ".env not found — copying from .env-example (fill in secrets before continuing)"
    run cp "${ENV_EXAMPLE}" "${ENV_FILE}"
  else
    info "Syncing new keys from .env-example into .env..."
    added=0
    while IFS= read -r line; do
      # Skip comments and blank lines
      [[ "$line" =~ ^[[:space:]]*# ]] && continue
      [[ -z "${line// }" ]] && continue
      key="${line%%=*}"
      [[ -z "$key" ]] && continue
      if ! grep -qE "^${key}=" "${ENV_FILE}"; then
        if [[ $DRY_RUN -eq 1 ]]; then
          echo "[dry-run] would add to .env: ${line}"
        else
          { echo ""; echo "# Added by update.sh — review and set a real value"; echo "${line}"; } >> "${ENV_FILE}"
        fi
        added=$((added + 1))
      fi
    done < "${ENV_EXAMPLE}"
    if [[ $added -gt 0 ]]; then
      info "Added ${added} new key(s) to .env — generating secrets for any 'changeme' values..."
      run uv run --no-sync scripts/generate_secrets.py
    else
      info ".env already up to date"
    fi
  fi
fi

# --- 4. Build ---
BUILD_ARGS=""
[[ $NO_CACHE -eq 1 ]] && BUILD_ARGS="--no-cache"

info "Building all services..."
run "${COMPOSE[@]}" build ${BUILD_ARGS}

# --- 5. Up ---
info "Starting services..."
run "${COMPOSE[@]}" up -d

info "Done."
