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
| `swagger` | 8080 | API docs (local/swagger profile only) |

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

```bash
# Core stack (form, panel, router, tts, jambonz, postgres, caddy, signal-api, redis)
docker compose -f infra/docker-compose.yml up -d

# With local extras (mock OIDC + Swagger UI on :8080)
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
