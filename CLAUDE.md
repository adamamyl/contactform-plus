# EMF Conduct System — Claude Code conventions

## Project layout

```
apps/form/          Public report form (FastAPI, port 8000)
apps/panel/         Conduct team panel (FastAPI + OIDC, port 8001)
apps/router/        Notification router (pg_notify listener, port 8002)
apps/tts/           Text-to-speech via Piper (port 8003)
apps/jambonz/       Jambonz telephony adapter (port 8004)
shared/             Shared library (emf_shared package)
infra/              Docker Compose, Caddy, Postgres, Grafana, ZAP
tests/security/     OWASP Top 10 test suite
tests/e2e/          End-to-end tests (Playwright + Schemathesis + httpx)
scripts/            Utility scripts (PEP 723 inline deps where standalone)
reports/zap/        ZAP scan output — gitignored, kept via .gitkeep
plan.md             Implementation plan + Section 15 task checklist
```

## Tech stack

- Python 3.12+, FastAPI, SQLAlchemy 2 async, asyncpg, Pydantic v2
- `uv` for all package management — never use `pip` directly
- PostgreSQL 17, Caddy (TLS termination), Docker Compose
- mypy strict, ruff (lint + format), bandit, pytest-asyncio

## Key conventions

- No `Any` or `unknown` types — use proper annotations everywhere
- No unnecessary comments or docstrings on obvious code
- No `# type: ignore` without a comment explaining why
- All standalone scripts use PEP 723 inline dependency metadata (`# /// script`)
- Shared lib imported as `from emf_shared.xxx import yyy` (package: `emf_shared`)
- Config: `config.json` + `.env` only — no YAML/TOML for app config
- Internal Docker network uses `http://` — TLS terminates at Caddy only

## Commands

### Per-service (run from inside `apps/<svc>/` or `shared/`)

```bash
uv sync                              # install deps
uv run pytest tests/ -q              # tests
uv run python -m mypy src/ --strict  # typecheck
uv run ruff check src/               # lint
uv run ruff format src/              # format
uv run bandit -r src/ -ll            # security lint
```

### Docker Compose

```bash
docker compose -f infra/docker-compose.yml up -d                    # core stack
docker compose -f infra/docker-compose.yml --profile local up -d    # + mock OIDC + Swagger
docker compose -f infra/docker-compose.yml --profile monitoring up -d  # + Prometheus/Grafana
docker compose -f infra/docker-compose.yml down -v                  # tear down + wipe data
```

### Testing

```bash
bash scripts/run_e2e.sh              # e2e: spin up isolated stack, run, tear down
uv run scripts/run_zap.py            # ZAP scan (requires stack already running)
uv run scripts/bad_strings_test.py --url http://localhost:8000 --sample 50 --seed 42
```

### Secrets / setup

```bash
uv run scripts/generate_secrets.py  # print secrets to paste into .env
uv run scripts/install.py           # interactive guided installer
uv run scripts/backup.py --recipient <age-pubkey>  # encrypted DB backup
uv run scripts/generate_bruno_collection.py  # regenerate Bruno API collection from docs/swagger-spec.json
```

## Git workflow

- Branches: `main`, `develop`, `feature/*`
- Commit style: `feat(scope): description` / `fix(scope): description` / `chore: description`
- Mark `plan.md` Section 15 tasks `[x]` before committing
- Never commit `.env` or `config.json`

## plan.md

Section 15 is the detailed task checklist. Mark items `[x]` as work is completed.
Phases 0–13 = core implementation. Phases 15–19 = extras (e2e, bad strings, swagger, accented chars, ZAP) — all complete.
