# EMF Conduct & Accessibility System

A multi-service system for managing conduct and accessibility reports at EMF Festival.

## Services

| Service | Port | Description |
|---------|------|-------------|
| `form` | 8000 | Public incident report form |
| `panel` | 8001 | Conduct team case management panel |
| `msg-router` | 8002 | Notification router (email, Signal, Mattermost/Slack) |
| `tts` | 8003 | Text-to-speech synthesis (Piper) |
| `jambonz-adapter` | 8004 | Jambonz telephony escalation adapter |
| `swagger` | — | API docs aggregator (local/swagger profile; served via Caddy) |

## Architecture

```
User → Caddy → form (8000)
                  ↓ pg_notify
             msg-router (8002) → email
                               → Signal
                               → Mattermost / Slack
                               → jambonz-adapter (8004) → TTS (8003) → Jambonz
Conduct team → Caddy → panel (8001)
```

---

## First-time setup

```bash
# 1. Copy and fill in secrets
cp .env-example .env
uv run scripts/generate_secrets.py   # prints generated values to paste into .env

# 2. Copy and edit app config
cp config.json-example config.json

# 3. Or run the interactive installer (does both + cert generation)
uv run scripts/install.py
```

---

## Running locally

The form service has `LOCAL_DEV=true` set in `docker-compose.yml`. This overrides
the event-phase check so all form fields are visible (phone, DECT, camping group,
urgency selector) regardless of whether the event window is active. Remove or set to
`false` when deploying to production.

```bash
# Core stack (form, panel, router, tts, jambonz, postgres, caddy, signal-api, redis)
docker compose -f infra/docker-compose.yml up -d

# With local extras (mock OIDC + Swagger UI at swagger.emf-forms.internal)
docker compose -f infra/docker-compose.yml --profile local up -d

# Swagger UI only (if stack already running)
docker compose -f infra/docker-compose.yml --profile swagger up -d

# Monitoring (Prometheus + Grafana on :3000)
docker compose -f infra/docker-compose.yml --profile monitoring up -d

# Tear down (keep volumes)
docker compose -f infra/docker-compose.yml down

# Tear down and wipe all data
docker compose -f infra/docker-compose.yml down -v
```

### Local URLs

| URL | Description | Profile |
|-----|-------------|---------|
| [https://report.emf-forms.internal/](https://report.emf-forms.internal/) | Public incident report form | always |
| [https://panel.emf-forms.internal/](https://panel.emf-forms.internal/) | Conduct team case management panel | always |
| [https://oidc.emf-forms.internal/default](https://oidc.emf-forms.internal/default) | Mock OIDC provider (panel login) | `local` |
| [https://map.emf-forms.internal/](https://map.emf-forms.internal/) | EMF site map (also embedded in form) | always |
| [https://swagger.emf-forms.internal/](https://swagger.emf-forms.internal/) | API docs index | `local`, `swagger` |
| [https://swagger.emf-forms.internal/all](https://swagger.emf-forms.internal/all) | All APIs — merged spec with service tags | `local`, `swagger` |
| [https://swagger.emf-forms.internal/sysadmin](https://swagger.emf-forms.internal/sysadmin) | Sysadmin — health & metrics for all services | `local`, `swagger` |
| [http://localhost:3000](http://localhost:3000) | Grafana dashboards | `monitoring` |

> Requires dnsmasq mapping `*.emf-forms.internal → 127.0.0.1` and the Caddy local CA cert trusted in your system keychain. See memory notes for setup details.

---

## Development

Each service is an independent `uv` project. From any service directory:

```bash
uv sync                        # install deps
uv run pytest tests/ -q        # run tests
uv run python -m mypy src/ --strict   # type check
uv run ruff check src/         # lint
uv run ruff format src/        # format
```

Run everything at once across all services:

```bash
for svc in shared apps/form apps/panel apps/router apps/tts apps/jambonz; do
  echo "=== $svc ===" && (cd $svc && uv run pytest tests/ -q && uv run python -m mypy src/ --strict)
done
```

### Shared library

`shared/` provides config loading, phase detection, DB session factory, and friendly ID generation. Install it into a service with:

```bash
uv add --editable ../../shared   # from inside apps/<service>/
```

---

## Testing

### Unit tests (per service)

```bash
cd apps/form && uv run pytest tests/ -q
cd apps/panel && uv run pytest tests/ -q
cd shared && uv run pytest tests/ -q
```

### Security tests (OWASP Top 10)

```bash
cd tests && uv run pytest security/ -q
```

### End-to-end tests (Playwright + Schemathesis + httpx)

Requires the e2e stack to be running. The script handles the full lifecycle:

```bash
bash scripts/run_e2e.sh                  # run all e2e tests
bash scripts/run_e2e.sh -k test_name     # filter by test name
```

Or manually:

```bash
docker compose -f infra/docker-compose.yml -f infra/docker-compose.e2e.yml up -d
cd tests/e2e && uv sync && uv run playwright install chromium --with-deps
FORM_BASE_URL=http://localhost:8000 PANEL_BASE_URL=http://localhost:8001 uv run pytest -v
docker compose -f infra/docker-compose.yml -f infra/docker-compose.e2e.yml down -v
```

### Bad strings test (manual, ad-hoc)

Feeds [BLNS](https://github.com/minimaxir/big-list-of-naughty-strings) through the form API with a rich progress display. Run against the e2e stack to keep data isolated:

```bash
docker compose -f infra/docker-compose.yml -f infra/docker-compose.e2e.yml up -d

uv run scripts/bad_strings_test.py --url http://localhost:8000 --sample 50 --seed 42
uv run scripts/bad_strings_test.py --url http://localhost:8000 --all   # all ~500 strings

docker compose -f infra/docker-compose.yml -f infra/docker-compose.e2e.yml down -v
```

### OWASP ZAP security scan (manual, on-demand)

```bash
# Start the application stack first
docker compose -f infra/docker-compose.yml --profile local up -d

# Run ZAP (prints risk summary, optionally opens HTML report)
uv run scripts/run_zap.py
# Reports written to reports/zap/
```

---

## Map

The EMF site map ([emf/map](https://github.com/emfcamp/map)) is a separate repository cloned alongside this one. The conduct system embeds it in the report form (click-to-pin) and in the panel case detail and dispatcher views (read-only marker).

### Embed integration patch

The map app requires a small patch to send `postMessage` events back to parent frames — enabling the form's pin-drop workflow and the panel's location display. The patch lives in `map/embed-readonly-view-postmessage.patch` in this repo and should **not** be committed to the map repo.

The patch adds:
- `emf-marker` postMessage from `marker.ts` when a pin is set or cleared
- `emf-view` postMessage on `moveend` and `load` so parent frames can display zoom/centre
- `?readonly=true` embed mode — suppresses click-to-pin (used by panel iframes)
- `?marker=lat,lon` query param to pre-set a pin on load
- `resize()` on map load to fix a WebGL green-box glitch in iframes

### Applying the patch

After cloning the map repo (or after pulling upstream changes):

```bash
cd /path/to/emf/map
git restore web/src/index.ts web/src/marker.ts   # revert any previous apply
git apply /path/to/emf-conduct/map/embed-readonly-view-postmessage.patch
```

Then rebuild the emf-map container:

```bash
cd /path/to/emf-conduct
docker compose -f infra/docker-compose.yml [-f infra/docker-compose.wolfcraig.yml] \
  up -d --build --force-recreate emf-map
```

If the patch no longer applies cleanly after a map repo update, re-create it:

```bash
cd /path/to/emf/map
# make the changes manually to web/src/index.ts and web/src/marker.ts
git diff web/src/ > /path/to/emf-conduct/map/embed-readonly-view-postmessage.patch
```

---

## API documentation (Swagger)

Available under the `local` or `swagger` Docker Compose profile at `https://swagger.emf-forms.internal`.

| Path | Contents |
|------|----------|
| `/` | Index — links to all views |
| `/all` | Single merged Swagger UI; all five services as collapsible tag groups |
| `/sysadmin` | Merged view of every `/health` and `/metrics` endpoint across all services |
| `/form` | Report Form API only |
| `/team` | Panel API only |
| `/dispatch` | Message Router + Jambonz Adapter (internal services) |
| `/tts` | Text-to-Speech API only |

**Auth:** the Panel spec exposes an OAuth2 authorization-code flow pointing at the OIDC issuer; the Router and Jambonz specs expose an `X-Internal-Secret` API-key scheme. Click **Authorize** in Swagger UI to set credentials before using "Try it out".

**Adding future ops endpoints to `/sysadmin`:** tag the route with `tags=["ops"]` and it appears automatically on next swagger restart. The `/metrics` endpoint (added by prometheus-fastapi-instrumentator) is always included regardless of tags.

---

## Configuration

| File | Purpose |
|------|---------|
| `.env` | Secrets — never commit. Generate with `scripts/generate_secrets.py` |
| `config.json` | App config — events, SMTP, urgency levels, notification channels. Copy from `config.json-example` |

---

## Security

- PostgreSQL with row-level security and per-role column grants
- TLS 1.3 minimum enforced at Caddy; HTTP/2 only
- OIDC (UFFD) authentication for the panel
- Time-limited, revocable dispatcher session tokens
- OWASP Top 10 test suite in `tests/security/`
- Weekly gitleaks scan + pip-audit in CI (`security.yml`)
- OWASP ZAP active scan via `scripts/run_zap.py` (manual trigger only)

---

## Backup

```bash
# One-off encrypted backup (age public key required)
uv run scripts/backup.py --recipient <age-pubkey>

# With rsync to remote
uv run scripts/backup.py --recipient <age-pubkey> --rsync user@host:/backups/

# Install systemd timer for automated backups
uv run scripts/backup.py --recipient <age-pubkey> --systemd
```

---

## CI

Workflows in `.github/workflows/`:

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| `ci.yml` | push / PR | lint, typecheck, tests, pip-audit for all services |
| `security.yml` | weekly + manual | gitleaks full-history scan, pip-audit, ZAP scan (manual only) |
