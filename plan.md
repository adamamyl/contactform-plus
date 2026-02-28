# EMF Conduct System — Implementation Plan

## Document purpose

This plan translates the requirements in `spec.md` and analysis in `research.md` into a concrete, step-by-step implementation guide. It covers repository structure, infrastructure scaffolding, each application component, security hardening, and observability. Code snippets are indicative — they establish patterns, naming conventions, and approach; they are not copy-paste complete implementations.

---

## 1. Repository Structure

One monorepo, multiple services. Each service is independently deployable but shares infrastructure config and common libraries.

```
emf-conduct/
├── .github/
│   └── workflows/
│       ├── ci.yml              # lint, test, bandit on every PR
│       └── security.yml        # gitleaks scheduled scan
├── infra/
│   ├── caddy/
│   │   ├── snippets/           # shared Caddyfile snippets (TLS, headers, etc.)
│   │   ├── Caddyfile.local     # imports snippets + local site blocks
│   │   └── Caddyfile.prod      # imports snippets + prod site blocks
│   ├── postgres/
│   │   ├── 00_roles.sql        # roles, grants, RLS (run on init)
│   │   └── migrations/         # alembic-managed migrations
│   ├── grafana/
│   │   └── dashboards/         # one JSON file per service
│   └── docker-compose.yml
├── shared/
│   ├── pyproject.toml          # shared lib: models, config, auth helpers
│   ├── emf_forms/
│   │   ├── config.py
│   │   ├── auth.py
│   │   ├── db.py
│   │   ├── friendly_id.py
│   │   └── phase.py
│   └── tests/
├── apps/
│   ├── form/                   # App 1 — public report form
│   ├── panel/                  # App 2 — conduct team panel + 2b dispatcher
│   ├── router/                 # App 3 — notification router
│   ├── tts/                    # App 4 — text-to-speech service
│   └── jambonz/                # App 5 — Jambonz adapter
├── scripts/
│   ├── install.py              # guided installation script (python + rich)
│   ├── generate_wordlist.py    # curates the friendly-ID wordlist
│   ├── generate_secrets.py     # populates .env from .env-example template
│   └── backup.py               # compressed, encrypted database backup
├── .env-example                # committed; never .env itself
├── config.json-example         # committed; never config.json itself
├── .gitleaks.toml
├── .pre-commit-config.yaml
├── spec.md
├── research.md
└── plan.md
```

### API-first architecture

Each service exposes a JSON REST API. The Jinja2 server-rendered HTML pages consume these same internal APIs (via HTMX or direct fetch). Benefits:
- Health endpoints are a natural consequence of having an API (`GET /health`)
- The dispatcher view, mobile clients, and future integrations all use the same interface
- Easier to test — API tests cover both programmatic and UI paths
- Avoids duplicating business logic between "page" routes and "API" routes

The pattern: every route that mutates state has a corresponding `POST /api/...` endpoint that returns JSON. Template routes are GET-only wrappers that render the page with initial data baked in.

---

## 2. Infrastructure Scaffolding

### 2.1 `uv` and Python project setup

Every Python service uses the same pattern:

```bash
uv init apps/form
cd apps/form
uv add fastapi uvicorn[standard] sqlalchemy[asyncio] asyncpg pydantic pydantic-settings

uv add --dev pytest pytest-asyncio httpx ruff bandit[toml] pre-commit \
        mypy types-jsonschema pip-audit
```

**On mypy**: Yes, add it. SQLAlchemy 2.x and Pydantic v2 ship stubs; mypy catches type errors that ruff misses (e.g. passing wrong dict structure into a Pydantic model). `types-jsonschema` is needed because we validate form field definitions against a JSON schema. `pre-commit` is a dev dep so the hooks work in CI as well as locally.

Each service `pyproject.toml`:

```toml
[project]
name = "emf-forms-form"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.30",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
]

[tool.ruff]
line-length = 100
target-version = "py312"
select = ["E", "W", "F", "I", "S", "B", "A", "C4", "UP"]

[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["pydantic.mypy", "sqlalchemy.ext.mypy.plugin"]

[tool.bandit]
skips = []
targets = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

### 2.2 Pre-commit hooks

`.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/PyCQA/bandit
    rev: 1.8.0
    hooks:
      - id: bandit
        args: ["-c", "pyproject.toml"]

  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.21.0
    hooks:
      - id: gitleaks

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.13.0
    hooks:
      - id: mypy
        additional_dependencies: ["pydantic>=2.9", "sqlalchemy>=2.0", "types-jsonschema"]
```

`.gitleaks.toml`:

```toml
[extend]
useDefault = true

[[rules]]
id = "emf-api-key"
description = "EMF/Jambonz API key pattern"
regex = '''(?i)(jambonz|emf)[_\-]?(api[_\-]?key|secret|token)\s*=\s*['"]?[A-Za-z0-9\-_]{16,}'''
```

### 2.3 Secrets and configuration

**Approach**: Two-tier.

1. **Non-sensitive config** — `config.json` (`.gitignore`'d; committed as `config.json-example`). Contains event dates, email addresses, urgency levels, Signal group ID, etc.
2. **Sensitive secrets** — `.env` file (`.gitignore`'d; committed as `.env-example`). Contains DB passwords, OIDC client secret, `secret_key` for JWT signing. File permissions: `chmod 600 .env`.

Docker file-based secrets (`/run/secrets/<name>`) work without Docker Swarm — they mount as read-only files. Use them for production deployments where secrets should not appear in environment variable listings. For local dev, `.env` with strict permissions is sufficient.

`.env-example`:
```dotenv
# Database passwords (one per service role)
FORM_DB_PASSWORD=changeme
PANEL_VIEWER_DB_PASSWORD=changeme
TEAM_MEMBER_DB_PASSWORD=changeme
SERVICE_DB_PASSWORD=changeme
ROUTER_DB_PASSWORD=changeme
ADMIN_DB_PASSWORD=changeme
BACKUP_DB_PASSWORD=changeme

# OIDC
OIDC_ISSUER=https://auth.emfcamp.org
OIDC_CLIENT_ID=emf-forms
OIDC_CLIENT_SECRET=changeme

# JWT signing for dispatcher tokens — generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
SECRET_KEY=changeme
```

`config.json-example`:
```json
{
  "events": [
    {
        "name": "emfcamp2026", 
        "start_date": "2026-07-12", 
        "end_date": "2026-07-20",
        "signal_padding": {
            "before_event_days": 2,
            "after_event_days": 2
        },
        "signal_group_id": null,
        "signal_mode": "fallback_only",
        "dispatcher_emails": ["event-dispatcher@emfcamp.org"],
        "dispatcher_session_ttl_hours": 8,
        "dispatcher_session_max_devices": 2
    }
  ],
  "conduct_emails": ["conduct@emfcamp.org"],
  "urgency_levels": ["low", "medium", "high", "urgent"],
  "pronouns": [
    "Ze/Zir/Zirs", "Xe/Xem/Xyrs", "Fae/Faer/Faerself", "Fur/Furs/Furself",
    "He/Him/His", "She/Her/Hers", "They/Them/Theirs"
  ],
  "smtp": {
    "host": "host.docker.internal",
    "port": 587,
    "from_addr": "conduct@emfcamp.org",
    "use_tls": true,
    "username": "conduct@emfcamp.org"
  },
  "_comment_smtp": "smtp_password goes in .env as SMTP_PASSWORD — never in config.json",
  "panel_base_url": "https://panel.emfcamp.org",
  "mattermost_webhook": null,
  "slack_webhook": null
}
```

**On `signal_mode`**: The default is `"fallback_only"` — Signal activates only when the phone system is unavailable. Set to `"always"` to send to Signal on every event-time alert, or `"high_priority_and_fallback"` for high/urgent cases plus fallback. Signal and phone routing are only active during the event window (± `signal_padding`); outside that window, only email is used. In non-prod/testing environments, `signal_mode` behaviour is always treated as `"always"` so the full routing path can be exercised without waiting for an event window.

**On `signal_group_id`**: If the signal module is enabled during install, the install script will walk through Signal group registration and populate this automatically.

**On `smtp`**: All email adapter settings live in `config.json` (non-secret). `smtp_password` is the exception — it lives in `.env` as `SMTP_PASSWORD` and is injected at runtime. The `SmtpConfig` model reads it via `pydantic-settings` env overlay. `username` in `config.json` is non-secret (it's just the sending account name).

`scripts/generate_secrets.py` reads `.env-example`, replaces every `changeme` value with a `secrets.token_urlsafe(32)` value, and writes `.env`. Idempotent — never overwrites existing non-default values.

### 2.4 Docker Compose (local)

The installation script (see Section 14) generates `docker-compose.yml` based on selected components. The base template:

`infra/docker-compose.yml`:

```yaml
services:
  caddy:
    image: caddy:2-alpine
    ports:
      - "443:443"
      - "80:80"
    volumes:
      # Mount the whole caddy/ directory — Caddyfile imports snippets from it
      - ./caddy:/etc/caddy:ro
      - caddy_data:/data
    depends_on:
      - form
      - panel
      - msg-router
    networks:
      - contactform

  postgres:
    image: postgres:17-alpine
    environment:
      POSTGRES_DB: emf_forms
      POSTGRES_USER: emf_forms_admin
      POSTGRES_PASSWORD: ${ADMIN_DB_PASSWORD}
      # TLS: mount server cert + key; set ssl=on in postgresql.conf
      POSTGRES_INITDB_ARGS: "--auth-host=scram-sha-256"
    env_file: ../.env
    volumes:
      - pg_data:/var/lib/postgresql/data
      - ./postgres:/docker-entrypoint-initdb.d:ro   # all .sql files run on first init
      - ./postgres/certs:/var/lib/postgresql/certs:ro
    healthcheck:
      # pg_isready checks TCP connectivity only — no credentials needed or used.
      # The -h flag targets the container-local socket; no admin role required.
      test: ["CMD-SHELL", "pg_isready -h localhost -d emf_forms"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - contactform

  form:
    build: ../apps/form
    env_file: ../.env
    environment:
      DATABASE_URL: postgresql+asyncpg://form_user:${FORM_DB_PASSWORD}@postgres/emf_forms
      CONFIG_PATH: ./config.json
    volumes:
      - ../config.json:/app/config.json:ro
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - contactform

  panel:
    build: ../apps/panel
    env_file: ../.env
    environment:
      DATABASE_URL: postgresql+asyncpg://panel_viewer:${PANEL_VIEWER_DB_PASSWORD}@postgres/emf_forms
      OIDC_ISSUER: ${OIDC_ISSUER}
      OIDC_CLIENT_ID: ${OIDC_CLIENT_ID}
      OIDC_CLIENT_SECRET: ${OIDC_CLIENT_SECRET}
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - contactform

  msg-router:
    build: ../apps/router
    env_file: ../.env
    environment:
      DATABASE_URL: postgresql+asyncpg://router_user:${ROUTER_DB_PASSWORD}@postgres/emf_forms
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - contactform

  mock-oidc:
    # Local only — removed for prod by the install script
    image: ghcr.io/navikt/mock-oauth2-server:2.1.10
    environment:
      JSON_CONFIG: '{"interactiveLogin":true,"httpServer":"NettyWrapper"}'
    profiles:
      - local

  signal-api:
    # Self-hosted signal-cli-rest-api — handles Signal group messaging.
    # Requires a registered Signal account (phone number); the install script
    # walks through first-time registration and populates SIGNAL_SENDER in .env.
    image: bbernhard/signal-cli-rest-api:latest
    environment:
      MODE: native
    volumes:
      - signal_data:/home/.local/share/signal-cli
    networks:
      - contactform

volumes:
  caddy_data:
  pg_data:
  signal_data:

networks:
  contactform:
    driver: bridge
```

**Notes**:
- Passwords come from `.env` via `env_file`. No file-based Docker secrets needed for local dev; prod deployments can switch to `secrets:` blocks with the same variable names.
- PostgreSQL TLS: mount `./postgres/certs/server.crt` and `server.key`; set `ssl = on` in `postgresql.conf`. The `connect_args={"ssl": "require"}` in SQLAlchemy enforces this from the client side. Use python module `cryptography` to create (idempotently) certs needed in install script and to install client-certs+CA trust to app (if needed).
- The `./postgres/` directory is mounted as `docker-entrypoint-initdb.d/` — all `.sql` files run alphabetically on first container init. `00_roles.sql` creates roles; subsequent files can add tables or seed data.
- Migrations (schema changes after initial deploy) use **Alembic**, not init.sql. Alembic runs as a one-shot container or a startup task in CI.

### 2.5 Caddy config

Caddy config uses snippets to share common settings (TLS policy, security headers) between local and prod.

`infra/caddy/snippets/tls.caddy`:
```caddyfile
(tls_policy) {
    tls_connection_policies {
        min_version tls1.3
    }
    servers {
        protocols h2
    }
}
```

`infra/caddy/snippets/headers.caddy`:
```caddyfile
(security_headers) {
    header {
        Strict-Transport-Security "max-age=63072000; includeSubDomains; preload"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'"
        Referrer-Policy "strict-origin-when-cross-origin"
        Permissions-Policy "geolocation=(self), camera=(), microphone=()"
        -Server
    }
}
```

`infra/caddy/Caddyfile.local`:
```caddyfile
{
    import snippets/tls.caddy
    local_certs
}

# Local: use .internal TLD (not .local — that's mDNS/Bonjour and causes conflicts)
report.emf-forms.internal {
    import snippets/headers.caddy
    reverse_proxy form:8000
}

panel.emf-forms.internal {
    import snippets/headers.caddy
    reverse_proxy panel:8000
}

router.emf-forms.internal {
    import snippets/headers.caddy
    reverse_proxy router:8000
}
```

`infra/caddy/Caddyfile.prod`:
```caddyfile
{
    import snippets/tls.caddy
    email sysadmin@emfcamp.org
}

# Production public names — set these in config / install script.
# Service names are prefixed with the Docker Compose project name so multiple
# instances can coexist on the same host. The install script substitutes
# PROJECT_NAME when generating this file.
report.emfcamp.org {
    import snippets/headers.caddy
    reverse_proxy ${PROJECT_NAME}-form:8000
}

panel.emfcamp.org {
    import snippets/headers.caddy
    reverse_proxy ${PROJECT_NAME}-panel:8000
}
```

### 2.6 PostgreSQL roles, schema, and PoLP design

#### Roles

| Role | Type | Purpose |
|---|---|---|
| `form_user` | service | INSERT cases only |
| `router_user` | service | SELECT minimal non-PII case fields via view; INSERT+UPDATE notifications |
| `service_user` | service | UPDATE case status/assignee; INSERT case_history — automated workflows |
| `panel_viewer` | service | SELECT non-PII fields via security_barrier view — dispatcher view only |
| `team_member` | service | Full SELECT+UPDATE on cases and case_history — conduct team panel |
| `backup_user` | service | pg_dump read access to full database; no application access |
| `emf_forms_admin` | superuser | Schema management only — not used by any running app |

**On `service_user`**: State transitions triggered by automated processes (router marking a notification sent, dispatcher triggering a state change) use `service_user`, not `team_member`. This separates machine actions from human ones. Human changes are audited in `case_history` with a username; automated changes are identified by `changed_by = "system"`.

**On multi-tenancy and `created_by`**: `team_id` on each case is sufficient for RLS row isolation. A separate `created_by` column is not needed — submitter identity (if provided) lives in `form_data.reporter`, and team-level change audit lives in `case_history.changed_by`.

**On `location_hint`**: A new non-PII `TEXT` column on `cases`, populated by the form service at submission time with the plain-text portion of the location. This lets `router_user` and `panel_viewer` show location context without needing access to `form_data`. The full structured location (text + coordinates) remains in `form_data`, accessible only to `team_member`.

#### Schema with per-column PoLP

```sql
-- infra/postgres/00_roles.sql

CREATE ROLE form_user        LOGIN PASSWORD :'form_password';
CREATE ROLE router_user      LOGIN PASSWORD :'router_password';
CREATE ROLE service_user     LOGIN PASSWORD :'service_password';
CREATE ROLE panel_viewer     LOGIN PASSWORD :'panel_viewer_password';
CREATE ROLE team_member      LOGIN PASSWORD :'team_member_password';
CREATE ROLE backup_user      LOGIN PASSWORD :'backup_password';
CREATE ROLE emf_forms_admin LOGIN PASSWORD :'admin_password' SUPERUSER;

-- Schema and database are named generically (`forms`, `emf_forms`) rather than after any
-- specific team. The conduct team is the first tenant; other EMF teams can be provisioned
-- without schema changes. All tenant isolation is via the `team_id` column + RLS — not
-- separate schemas or databases.

CREATE SCHEMA IF NOT EXISTS forms;
GRANT USAGE ON SCHEMA forms TO
    form_user, router_user, service_user, panel_viewer, team_member;

-- backup_user: read-only dump access; no schema mutation possible
-- Future multi-tenant: provision one backup_user per team and restrict it to that
-- team's rows via RLS. Each team's dump can then be encrypted with their own public
-- key/cert so only they can restore it — useful for GDPR portability and separation.

GRANT CONNECT ON DATABASE emf_forms TO backup_user;
GRANT USAGE ON SCHEMA forms TO backup_user;
GRANT SELECT ON ALL TABLES IN SCHEMA forms TO backup_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA forms
    GRANT SELECT ON TABLES TO backup_user;

-- -------------------------------------------------------------------------
-- forms.cases — column access matrix
--
-- Column        form_user  router_user  service_user  panel_viewer  team_member
-- id            INSERT     (view)       SELECT        (view)        SELECT
-- friendly_id   INSERT     (view)       SELECT        (view)        SELECT
-- event_name    INSERT     (view)       —             —             SELECT
-- urgency       INSERT     (view)       SELECT/UPDATE (view)        SELECT/UPDATE
-- phase         INSERT     —            —             —             SELECT
-- form_data     INSERT     —            —             —             SELECT
-- location_hint INSERT     (view)       —             (view)        SELECT
-- status        INSERT(new)(view)       SELECT/UPDATE (view)        SELECT/UPDATE
-- assignee      —          —            SELECT/UPDATE —             SELECT/UPDATE/DELETE
-- tags          —          —            —             —             SELECT/UPDATE/DELETE
-- team_id       INSERT(null)—           —             —             SELECT
-- created_at    INSERT     (view)       SELECT        (view)        SELECT
-- updated_at    —          (view)       UPDATE        (view)        SELECT
-- -------------------------------------------------------------------------

-- form_user: insert new cases
GRANT INSERT ON forms.cases TO form_user;

-- router_user: read-only access to non-PII fields via security_barrier view.
-- The security_barrier attribute forces view predicates to be evaluated before
-- any user-supplied filters, preventing timing/planning side-channel leaks.
CREATE VIEW forms.cases_router WITH (security_barrier = true) AS
    SELECT id, friendly_id, event_name, urgency, status, location_hint, created_at
    FROM forms.cases;
GRANT SELECT ON forms.cases_router TO router_user;
GRANT INSERT, UPDATE ON forms.notifications TO router_user;

-- service_user: update workflow fields; append audit rows
GRANT SELECT (id, friendly_id, urgency, status, assignee, updated_at)
    ON forms.cases TO service_user;
GRANT UPDATE (status, assignee, updated_at) ON forms.cases TO service_user;
GRANT INSERT ON forms.case_history TO service_user;

-- panel_viewer: dispatcher view via security_barrier view (no PII)
CREATE VIEW forms.cases_dispatcher WITH (security_barrier = true) AS
    SELECT id, friendly_id, urgency, status, location_hint, created_at, updated_at
    FROM forms.cases;
GRANT SELECT ON forms.cases_dispatcher TO panel_viewer;

-- team_member: full SELECT/UPDATE access, scoped by RLS (team_id matched from SSO session).
-- No WHERE clause on the GRANT — RLS policy team_isolation filters rows transparently.
-- The app sets app.current_team_id = <uuid from OIDC group> before any query.
GRANT SELECT, UPDATE ON forms.cases TO team_member;
GRANT SELECT, INSERT ON forms.case_history TO team_member;
GRANT SELECT ON forms.notifications TO team_member;

-- -------------------------------------------------------------------------
-- Row-Level Security
-- Single-tenant now: form_user inserts cases with team_id = <conduct team UUID> at submission
-- time (seeded in config); this means no UPDATE is needed when multi-tenancy is activated.
-- Multi-tenant: app sets app.current_team_id at session open time (from OIDC group claim).
-- Onboarding a new team: install script guides sysadmin through team creation questions
-- (team name, group ID, contact emails) and provisions team_id via a super-admin role.
-- Moving cases between teams also requires super-admin; tracked in case_history.
-- -------------------------------------------------------------------------
ALTER TABLE forms.cases ENABLE ROW LEVEL SECURITY;

-- Bypass RLS for emf_forms_admin (superuser already bypasses, but explicit)
ALTER TABLE forms.cases FORCE ROW LEVEL SECURITY;

-- form_user inserts cases with team_id already set to the correct team UUID (from config),
-- so this policy works from day one even before multi-tenancy is fully activated.
CREATE POLICY team_isolation ON forms.cases
    USING (
        team_id IS NULL
        OR team_id = current_setting('app.current_team_id', true)::uuid
    );

-- RLS applies to security_barrier views too — the view's WHERE clause is
-- evaluated before user predicates, and RLS policies stack on top.
```


---

## 3. Shared Library (`shared/`)

The shared library provides config loading, phase detection, friendly ID generation, and DB session helpers — used by all services.

### 3.1 Config model

All dates in config and in the database are ISO 8601 (`YYYY-MM-DD` for dates, RFC 3339 for datetimes). Python's `date.fromisoformat()` and `datetime.isoformat()` handle this without any extra libraries.

`shared/emf_forms/config.py`:

```python
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings


class SignalPadding(BaseModel):
    before_event_days: int = 2
    after_event_days: int = 2


class EventConfig(BaseModel):
    name: str
    start_date: date   # ISO 8601: "2026-07-12"
    end_date: date
    # Per-event Signal/dispatcher settings — each event can have its own group and routing.
    signal_group_id: str | None = None
    signal_mode: str = "fallback_only"  # "always" | "fallback_only" | "high_priority_and_fallback"
    signal_padding: SignalPadding = SignalPadding()
    dispatcher_emails: list[str] = []
    dispatcher_session_ttl_hours: int = 8
    dispatcher_session_max_devices: int = 2

    @field_validator("end_date")
    @classmethod
    def end_after_start(cls, v: date, info) -> date:
        if "start_date" in info.data and v < info.data["start_date"]:
            raise ValueError("end_date must be after start_date")
        return v


class SmtpConfig(BaseModel):
    host: str = "host.docker.internal"
    port: int = 587
    from_addr: str
    use_tls: bool = True
    username: str | None = None
    # password is NOT stored here — it comes from SMTP_PASSWORD in .env via pydantic-settings


class AppConfig(BaseModel):
    events: list[EventConfig]
    conduct_emails: list[str]
    urgency_levels: list[str] = ["low", "medium", "high", "urgent"]
    # Pronouns list — sourced from config so it can be updated without code changes.
    # Frontend renders as a datalist (predefined options + free text entry).
    # "Other" is always appended as the last option at render time so it stays at the bottom.
    # Only one pronoun value is accepted per submission (single text field).
    pronouns: list[str] = [
        "Ze/Zir/Zirs", "Xe/Xem/Xyrs", "Fae/Faer/Faerself", "Fur/Furs/Furself",
        "He/Him/His", "She/Her/Hers", "They/Them/Theirs", "Other",
    ]
    smtp: SmtpConfig
    panel_base_url: str  # used to construct case URLs in notifications
    mattermost_webhook: str | None = None
    slack_webhook: str | None = None


class Settings(BaseSettings):
    database_url: str
    # Default: ./config.json relative to CWD (repo root when running locally).
    # .gitignore this file; commit config.json.example instead.
    config_path: Path = Path("config.json")
    # JWT signing key for dispatcher tokens — read from .env, never from config.json
    secret_key: str

    @property
    def app_config(self) -> AppConfig:
        return AppConfig.model_validate(
            json.loads(self.config_path.read_text())
        )

    class Config:
        env_file = ".env"
```

### 3.2 Phase detection

Phase drives routing behaviour (phone calls during event time, email-only outside). However, **the form also lets users select which event they're reporting about** — the conduct team works year-round and a complaint filed months later should be linked to the event it relates to, not the current date. Phase detection is used only for routing decisions; event association is user-supplied.

`shared/emf_forms/phase.py`:

```python
from datetime import datetime, timezone
from enum import StrEnum

from .config import AppConfig, EventConfig


class Phase(StrEnum):
    PRE_EVENT  = "pre_event"
    EVENT_TIME = "event_time"
    POST_EVENT = "post_event"


def current_phase(config: AppConfig, at: datetime | None = None) -> Phase:
    """
    Return the operational phase based on the current date.
    Used for routing decisions (e.g. whether to call DECT phones or Signal).
    NOT used to determine which event a case relates to — that is user-supplied.
    """
    now = (at or datetime.now(tz=timezone.utc)).date()

    for event in sorted(config.events, key=lambda e: e.start_date, reverse=True):
        if event.start_date <= now <= event.end_date:
            return Phase.EVENT_TIME
        if now < event.start_date:
            return Phase.PRE_EVENT
        if now > event.end_date:
            return Phase.POST_EVENT

    return Phase.PRE_EVENT


def is_active_routing_window(config: AppConfig, at: datetime | None = None) -> bool:
    """
    Returns True if phone/Signal routing should be active.
    This extends the EVENT_TIME phase by signal_padding days either side,
    so the team is reachable in the run-up to and wind-down after the event.
    """
    from datetime import timedelta
    now = (at or datetime.now(tz=timezone.utc)).date()
    padding = config.signal_padding

    for event in config.events:
        window_start = event.start_date - timedelta(days=padding.before_event_days)
        window_end   = event.end_date   + timedelta(days=padding.after_event_days)
        if window_start <= now <= window_end:
            return True
    return False


def events_for_form(config: AppConfig) -> list[EventConfig]:
    """
    Returns events to show in the 'which event does this relate to?' dropdown,
    sorted most recent first. Includes past events so post-event reports still
    reference the correct event.
    """
    return sorted(config.events, key=lambda e: e.start_date, reverse=True)
```

### 3.3 Friendly ID generation

With a 10,000-word wordlist, 4-word IDs give 10^16 combinations — effectively inexhaustible for this use case. The DB `UNIQUE` constraint guarantees actual uniqueness; the random generation just makes collisions astronomically unlikely.

`scripts/generate_wordlist.py` — produces `shared/emf_forms/wordlist.txt`:
- Source: standard English word corpus (e.g. `wordfreq` library top 20k)
- Filter: 4–8 letters, no ambiguous spellings, no homographs, no proper nouns
- Inclusive language: apply the principles at https://developers.google.com/style/inclusive-documentation; use `better-profanity` library for automated screening, then commit
- Target: ~10,000 words
- Commit the output file; the script is for regeneration only

`shared/emf_forms/friendly_id.py`:

```python
from __future__ import annotations

import secrets
from importlib.resources import files

_WORDLIST: list[str] = (
    files("emf_forms").joinpath("wordlist.txt").read_text().splitlines()
)
# Should be ~10,000 words for adequate cardinality


def generate() -> str:
    return "-".join(secrets.choice(_WORDLIST) for _ in range(4))


def generate_unique(existing: set[str], existing_uuid: str = "") -> str:
    for _ in range(10):
        candidate = generate()
        if candidate not in existing:
            return candidate
    # Soft-fail: log a warning and return a UUID-derived fallback.
    # The UUID is the authoritative identifier; friendly_id is human convenience.
    # If we somehow exhaust 10 attempts (astronomically unlikely with 10k words),
    # we fall back rather than raising so the submission is never lost.
    import logging
    logging.getLogger(__name__).warning(
        "Could not generate unique friendly ID in 10 attempts; using UUID fallback"
    )
    return existing_uuid[:8] if existing_uuid else generate()
```

### 3.4 Database session

```python
# shared/emf_forms/db.py
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_engine = None
_session_factory = None


def init_db(database_url: str) -> None:
    global _engine, _session_factory
    _engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        connect_args={"ssl": "require"},  # TLS required, even inside Docker
    )
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with _session_factory() as session:
        yield session
```

---

## 4. App 1 — Public Report Form

**Framework**: FastAPI (API) + Jinja2 templates (server-rendered, mobile-first, no heavy JS framework). Follows the API-first pattern: `POST /api/submit` returns JSON; the HTML form posts to this same endpoint.

### 4.1 Data model

**Design decisions**:
- `status` is a `String`, not a DB enum. Enums in Postgres require a migration to add values; strings do not. Validation is enforced in the application layer (Pydantic + explicit state machine). This is consistent with storing `form_data` as JSONB.
- `form_data` is JSONB — new form fields don't require schema migrations.
- `case_history` is a separate append-only table. `updated_at` on `cases` is a convenience timestamp for the most recent change; the full audit trail lives in `case_history`.
- `event_name` records which event the reporter selected (user-supplied, not inferred).
- All datetimes stored as UTC; `timezone=True` on all DateTime columns.
- Location is stored as a structured object: `{"text": "Near the bar", "lat": 52.04, "lon": -2.37}`. Both subfields are optional — the user may provide text, coordinates, or both.

`apps/form/src/models.py`:

```python
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = {"schema": "conduct"}

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    friendly_id = Column(String(64), unique=True, nullable=False, index=True)
    event_name  = Column(String(64), nullable=False)   # user-selected event
    urgency     = Column(String(16), nullable=False, default="medium")
    phase       = Column(String(16), nullable=False)   # routing phase at submission time
    form_data   = Column(JSONB, nullable=False, default=dict)
    # form_data includes: reporter details, what_happened, incident_date, incident_time,
    # location (structured), additional_info, support_needed, others_involved,
    # why_it_happened, can_contact, anything_else

    # Workflow fields — written by panel, not form
    status      = Column(String(32), nullable=False, default="new")
    assignee    = Column(String(128), nullable=True)
    tags        = Column(JSONB, nullable=False, default=list)
    team_id     = Column(UUID(as_uuid=True), nullable=True)  # future multi-tenancy

    created_at  = Column(DateTime(timezone=True), nullable=False,
                         default=lambda: datetime.now(tz=timezone.utc))
    updated_at  = Column(DateTime(timezone=True), nullable=False,
                         default=lambda: datetime.now(tz=timezone.utc),
                         onupdate=lambda: datetime.now(tz=timezone.utc))


class CaseHistory(Base):
    """Append-only audit trail. Every field change gets a row."""
    __tablename__ = "case_history"
    __table_args__ = {"schema": "conduct"}

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id     = Column(UUID(as_uuid=True), nullable=False, index=True)
    changed_by  = Column(String(128), nullable=False)  # username or "system"
    field       = Column(String(64), nullable=False)   # e.g. "status", "assignee"
    old_value   = Column(Text, nullable=True)
    new_value   = Column(Text, nullable=True)
    changed_at  = Column(DateTime(timezone=True), nullable=False,
                         default=lambda: datetime.now(tz=timezone.utc))
```

### 4.2 Form validation

**Phone numbers**: DECT extensions (e.g. `1234`) and T9 letter codes (e.g. `ADAM`) must be allowed alongside standard international numbers. Widen the allowed character set accordingly.

**Location**: Accept a structured object with optional `text`, `lat`, and `lon` fields. The map pin-drop sends coordinates; the text box is a fallback for users without JS or who want to describe the location in words.

**All text fields have explicit max_length** — we do not trust any input, so server-side length limits are applied uniformly at the Pydantic layer regardless of what the HTML `maxlength` attribute says.

**Inline real-time validation**: Validate as the user types or leaves a field — not only on submit. Use JS to mirror the same rules as the backend. Errors are specific and helpful, not generic:
- Over-length: `"⚠️ I can only accept 5,000 characters here — you've got 7,000. Can you shorten it a little?"`
- Phone format: `"⚠️ I don't recognise '@' in a phone number. I accept: +, ., spaces, numbers, and A–Z letters (for DECT codes like ADAM)."`
- The backend is always authoritative; JS validation is a UX improvement, not a security control.

**Phone normalisation**: The backend strips and normalises phone numbers (trim whitespace, collapse multiple spaces). The team panel renders stored numbers with spaces for readability. Phone allowed characters: digits, `+`, `-`, `.`, `(`, `)`, spaces, and letters A–Z (for T9/DECT codes).

`apps/form/src/schemas.py`:

```python
from __future__ import annotations

import re
from datetime import date, time

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


class Location(BaseModel):
    text: str | None = Field(None, max_length=500)
    lat: float | None = None
    lon: float | None = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> "Location":
        if self.text is None and self.lat is None:
            raise ValueError("Provide at least a text description or coordinates")
        return self

    @field_validator("lat")
    @classmethod
    def valid_lat(cls, v: float | None) -> float | None:
        if v is not None and not (-90 <= v <= 90):
            raise ValueError("Latitude must be between -90 and 90")
        return v

    @field_validator("lon")
    @classmethod
    def valid_lon(cls, v: float | None) -> float | None:
        if v is not None and not (-180 <= v <= 180):
            raise ValueError("Longitude must be between -180 and 180")
        return v


class ReporterDetails(BaseModel):
    name: str | None = Field(None, max_length=200)
    pronouns: str | None = Field(None, max_length=50)
    # Phone: allow digits, spaces, +, -, ., (, ), and letters A-Z (DECT T9 codes like "ADAM")
    # Valid examples: "+44 23 4566 7789", "1234" (DECT), "ADAM" (T9), "34.3434242242"
    phone: str | None = Field(None, max_length=30)
    email: EmailStr | None = None
    camping_with: str | None = Field(None, max_length=200)

    @field_validator("phone")
    @classmethod
    def sanitise_phone(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = re.sub(r"[^\w\s\+\-\.\(\)]", "", v)[:30]
        return " ".join(cleaned.split()) or None


class CaseSubmission(BaseModel):
    # Which event this relates to (user-selected, not inferred from date)
    event_name: str = Field(..., max_length=64) # default to the current event during event time in the UI

    # Section 1 — reporter details (all optional for anonymity)
    reporter: ReporterDetails = Field(default_factory=ReporterDetails)

    # Section 1 — incident details (what_happened is the only required field)
    what_happened: str = Field(..., min_length=10, max_length=10_000)
    incident_date: date    # stored as ISO 8601
    incident_time: time    # 24hr HH:MM
    location: Location
    additional_info: str | None = Field(None, max_length=5_000)
    support_needed: str | None = Field(None, max_length=2_000)
    urgency: str = Field("medium", max_length=16)

    # Section 2 — optional context (can be filled later)
    others_involved: str | None = Field(None, max_length=2_000)
    why_it_happened: str | None = Field(None, max_length=2_000)
    can_contact: bool | None = None
    anything_else: str | None = Field(None, max_length=5_000)

    # Bot protection — must be empty for real submissions
    website: str | None = Field(None, max_length=200)  # honeypot

    @field_validator("urgency")
    @classmethod
    def valid_urgency(cls, v: str) -> str:
        # Allowed values are also validated against config at route level
        if v not in {"low", "medium", "high", "urgent"}:
            raise ValueError("Invalid urgency level")
        return v

    @field_validator(
        "what_happened", "additional_info", "support_needed",
        "others_involved", "why_it_happened", "anything_else",
        mode="before",
    )
    @classmethod
    def strip_whitespace(cls, v: object) -> object:
        if isinstance(v, str):
            return " ".join(v.split()) or None
        return v
```

### 4.3 Route handlers

```python
# apps/form/src/routes.py
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from emf_forms.db import get_session
from emf_forms.friendly_id import generate_unique
from emf_forms.phase import Phase, current_phase, events_for_form

from .models import Case
from .schemas import CaseSubmission

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
async def form_page(request: Request, settings=Depends(get_settings)):
    config = settings.app_config
    phase = current_phase(config)
    return templates.TemplateResponse("form.html", {
        "request": request,
        "phase": phase,
        "config": config,
        "events": events_for_form(config),
    })


@router.post("/api/submit", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute;20/hour")
async def submit_case(
    request: Request,
    submission: CaseSubmission,
    session: AsyncSession = Depends(get_session),
    settings=Depends(get_settings),
):
    # Honeypot: silently discard bot submissions without revealing detection
    if submission.website:
        return {"case_id": "00000000-0000-0000-0000-000000000000", "friendly_id": "ok"}

    config = settings.app_config

    if submission.urgency not in config.urgency_levels:
        raise HTTPException(status_code=422, detail="Invalid urgency level")

    if submission.event_name not in {e.name for e in config.events}:
        raise HTTPException(status_code=422, detail="Unknown event")

    phase = current_phase(config)
    existing = await _get_existing_friendly_ids(session)
    friendly_id = generate_unique(existing)

    case = Case(
        friendly_id=friendly_id,
        event_name=submission.event_name,
        urgency=submission.urgency,
        phase=phase.value,
        form_data=submission.model_dump(exclude={"website"}),
    )
    session.add(case)
    await session.commit()
    await session.refresh(case)

    # Notify the router service via PostgreSQL NOTIFY (see Section 6.6)
    await session.execute(
        "SELECT pg_notify('new_case', :payload)",
        {"payload": str(case.id)},
    )

    return {"case_id": str(case.id), "friendly_id": case.friendly_id}


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)):
    await session.execute("SELECT 1")
    return {"status": "ok"}
```

### 4.4 Rate limiting

```python
# apps/form/src/main.py
from fastapi import FastAPI
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="EMF Conduct Form")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.include_router(router)
```

Note: during event time, attendees will mostly connect from on-site network blocks. Consider configuring rate limiting to be more permissive for known EMF IP ranges, or making the rate limit thresholds configurable in `config.json`.

### 4.5 HTML template (mobile-first)

Key design decisions from annotations:
- **Alert banner**: use a high-contrast amber/orange background, not red/green (accessibility; red/green colour-blind users)
- **Email input**: use `inputmode="email"` so mobile browsers show the `@` key prominently
- **Phone input**: `type="tel"` brings up the numpad on mobile; also accept text (DECT)
- **Section 1 of 2 / Section 2 of 2**: label consistently across both fieldsets
- **`what_happened`**: marked `required`; the only mandatory field
- **Map pin**: embedded map iframe with a JS click handler that writes lat/lon into hidden inputs. Progressive enhancement — form works without JS, user can type location text instead.
- **Urgency**: `<select>` dropdown, never a free-text field
- **Honeypot**: CSS-hidden `<div>`, not `type="hidden"` (bots fill visible-but-hidden fields)
- **Validate input**: Validate all user submitted inputs in frontend as well as backend

`apps/form/templates/form.html` — illustrative structure:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>EMF Conduct Report</title>
    <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <main class="form-container">
    <h1>Conduct Report</h1>

    {% if phase == "event_time" %}
    <div class="alert alert--event" role="alert">
      <!-- High-contrast amber background, large text, visible on all screens -->
      ⚠️ For urgent issues, call us on DECT <strong>1234</strong> — we'll respond much faster than this form.
    </div>
    {% endif %}

    <form id="report-form" method="post" action="/api/submit" novalidate>
      <!-- Honeypot: visually hidden via CSS .visually-hidden, not type="hidden" -->
      <div class="visually-hidden" aria-hidden="true">
        <label for="website">Website</label>
        <input type="text" id="website" name="website" tabindex="-1" autocomplete="off">
      </div>

      <!-- Event selection -->
      <!--
        During the active routing window (is_active_routing_window == true), the current event
        is pre-selected in the UI. Outside the window (pre/post-event), the user picks manually.
        event_name is user-supplied and validated server-side against config.events[].name.
      -->
      <fieldset>
        <legend>Which event does this relate to?</legend>
        <select name="event_name" required>
          {% for event in events %}
          <option value="{{ event.name }}"
            {% if event.name == current_event_name %}selected{% endif %}>
            {{ event.name }} ({{ event.start_date }})
          </option>
          {% endfor %}
        </select>
      </fieldset>

      <!-- Section 1 of 2 — About you -->
      <fieldset>
        <legend>Section 1 of 2 — About you <span class="hint">(all optional)</span></legend>

        <label for="name">Name / fur/persona</label>
        <!-- Describes what to call them at EMF — persona, fur name, preferred name all valid -->
        <p class="hint">What should we call you? Use whatever people call you at EMF.</p>
        <input type="text" id="name" name="name" maxlength="200" autocomplete="nickname">

        <label for="pronouns">Pronouns</label>
        <!--
          Rendered as <input> + <datalist>: user gets predefined suggestions but can type anything.
          The datalist options are loaded from config.pronouns (so they can be updated without code changes).
          Backend validates against the same list, but also accepts any free-text value up to 50 chars.
        -->
        <input type="text" id="pronouns" name="pronouns" maxlength="50"
               list="pronouns-list" autocomplete="off">
        <datalist id="pronouns-list">
          {% for p in config.pronouns %}
          <option value="{{ p }}">
          {% endfor %}
        </datalist>

        <label for="email">Email address</label>
        <!-- inputmode="email" ensures the @ key is visible on mobile keyboards -->
        <input type="email" id="email" name="email" autocomplete="email" inputmode="email">

        {% if is_active_routing_window %}
        <!-- Phone and camping location are only useful when the team can act on them in real time -->
        <label for="phone">Phone number <span class="hint">(mobile, DECT, or T9 code)</span></label>
        <!-- type="tel" brings up the numpad on mobile -->
        <input type="tel" id="phone" name="phone" autocomplete="tel">

        <label for="camping_with">Camping with…</label>
        <p class="hint">This helps us find you — your camping group, village name, or area.</p>
        <input type="text" id="camping_with" name="camping_with" maxlength="200">
        {% endif %}
      </fieldset>

      <!-- Section 2 of 2 — What happened -->
      <fieldset>
        <legend>Section 2 of 2 — What happened</legend>

        <label for="what_happened">
          What are you reporting? <span class="required">*</span>
          <span class="hint">Try: "I was by… when I saw…"</span>
        </label>
        <textarea id="what_happened" name="what_happened" required
                  rows="6" minlength="10" maxlength="10000"></textarea>

        <label for="incident_date">When did this happen?</label>
        <input type="date" id="incident_date" name="incident_date">

        <label for="incident_time">Approximate time (24hr)</label>
        <input type="time" id="incident_time" name="incident_time">

        <label for="location_text">Where did it happen?</label>
        <input type="text" id="location_text" name="location[text]" maxlength="500"
               placeholder="Near the bar, Stage 2, Camping field B…">
        <!-- map.emfcamp.org (no /embed — use the real map, not a stripped iframe version) -->
        <p class="hint">Not sure exactly where? <a href="https://map.emfcamp.org" target="_blank"
           rel="noopener noreferrer">map.emfcamp.org</a> may help.</p>
        <!-- Progressive enhancement: map pin drop if JS available -->
        <div id="map-container" class="map-hidden">
          <div id="location-map"></div>
          <p class="hint">Click on the map to drop a pin</p>
        </div>
        <!-- Hidden inputs populated by JS map click handler -->
        <input type="hidden" id="location_lat" name="location[lat]">
        <input type="hidden" id="location_lon" name="location[lon]">

        {% if is_active_routing_window %}
        <!-- Urgency is only actionable during the event window (including signal_padding days) -->
        <label for="urgency">How urgent is this?</label>
        <select id="urgency" name="urgency">
          {% for level in config.urgency_levels %}
          <option value="{{ level }}" {% if level == "medium" %}selected{% endif %}>
            {{ level | capitalize }}
          </option>
          {% endfor %}
        </select>
        {% endif %}

        <label for="additional_info">Any more information?</label>
        <textarea id="additional_info" name="additional_info" rows="4" maxlength="5000">
        </textarea>
        <p class="hint">If you have a photo or video link, include it here.</p>

        <label for="support_needed">Do you need any additional support?</label>
        <textarea id="support_needed" name="support_needed" rows="3" maxlength="2000">
        </textarea>
        <p class="hint">First Aid and the Info Desk may also be able to help.</p>
      </fieldset>

      <!-- Section 2 optional context — labelled consistently -->
      <fieldset>
        <legend>Additional context <span class="hint">(optional — can be filled in later)</span></legend>

        <label for="others_involved">Were other people involved?</label>
        <textarea id="others_involved" name="others_involved" rows="3" maxlength="2000">
        </textarea>
        <p class="hint">Names, or descriptions if you don't know names.</p>

        <label for="why_it_happened">Do you know why this happened?</label>
        <textarea id="why_it_happened" name="why_it_happened" rows="3" maxlength="2000">
        </textarea>

        <fieldset class="inline">
          <legend>Can we contact you for more information?</legend>
          <label><input type="radio" name="can_contact" value="true"> Yes</label>
          <label><input type="radio" name="can_contact" value="false"> No</label>
        </fieldset>

        <label for="anything_else">Is there anything else we should know?</label>
        <textarea id="anything_else" name="anything_else" rows="3" maxlength="2000">
        </textarea>
      </fieldset>

      <button type="submit" class="btn btn--primary">Submit report</button>
    </form>
  </main>
  <script src="/static/form.js" defer></script>
</body>
</html>
```

---

## 5. App 2 — Conduct Team Panel

**Framework**: FastAPI + Jinja2. SSO via `authlib` (OIDC against UFFD — https://github.com/emfcamp/uffd). Local dev uses `ghcr.io/navikt/mock-oauth2-server`.

### 5.1 OIDC authentication

```python
# apps/panel/src/auth.py
from authlib.integrations.starlette_client import OAuth
from fastapi import HTTPException, Request, status

oauth = OAuth()


def configure_oauth(settings) -> None:
    oauth.register(
        name="emf",
        server_metadata_url=f"{settings.oidc_issuer}/.well-known/openid-configuration",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        client_kwargs={"scope": "openid email profile groups"},
    )


async def require_conduct_team(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    if "team_conduct" not in user.get("groups", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user
```

### 5.2 Case list and status transitions

Status is a `String`; the state machine is enforced in the application layer:

```python
# apps/panel/src/routes.py
VALID_TRANSITIONS: dict[str, set[str]] = {
    "new":              {"assigned"},
    "assigned":         {"in_progress", "new", "closed"},
    "in_progress":      {"action_needed", "decision_needed", "closed"},
    "action_needed":    {"in_progress", "decision_needed", "closed"},
    "decision_needed":  {"closed", "in_progress"},
    "closed":           set(),  # terminal
}
```

Every status change also appends a row to `case_history`:

```python
@router.patch("/api/cases/{case_id}/status")
async def transition_status(
    case_id: str,
    body: StatusTransition,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_conduct_team),
):
    case = await session.get(Case, case_id)
    if not case:
        raise HTTPException(404)

    allowed = VALID_TRANSITIONS.get(case.status, set())
    if body.status not in allowed:
        raise HTTPException(422, detail=f"Cannot transition '{case.status}' → '{body.status}'")

    old_status = case.status
    await session.execute(
        update(Case).where(Case.id == case_id)
        .values(status=body.status, updated_at=datetime.now(tz=timezone.utc))
    )
    session.add(CaseHistory(
        case_id=case_id,
        changed_by=user["preferred_username"],
        field="status",
        old_value=old_status,
        new_value=body.status,
    ))
    await session.commit()
    return {"status": body.status}
```

### 5.3 Dispatcher session URL generation

The `POST /api/dispatcher-session` endpoint is gated by `require_conduct_team`. It also accepts an optional `send_to` email address — when provided, the session URL is emailed to that address (e.g. the on-shift dispatcher's email).

```python
# apps/panel/src/dispatcher.py
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException

from .auth import require_conduct_team

router = APIRouter(dependencies=[Depends(require_conduct_team)])

_revoked: set[str] = set()
_active_sessions: dict[str, list[str]] = {}


def create_dispatcher_token(settings, ttl_hours: int) -> str:
    jti = secrets.token_urlsafe(16)
    return jwt.encode({
        "sub": "dispatcher",
        "jti": jti,
        "exp": datetime.now(tz=timezone.utc) + timedelta(hours=ttl_hours),
        "iat": datetime.now(tz=timezone.utc),
        "scope": "dispatcher",
    }, settings.secret_key, algorithm="HS256")


def validate_dispatcher_token(token: str, device_id: str, settings) -> dict:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid session token")

    jti = payload["jti"]
    if jti in _revoked:
        raise HTTPException(401, "Session revoked")
    if payload.get("scope") != "dispatcher":
        raise HTTPException(403, "Insufficient scope")

    devices = _active_sessions.setdefault(jti, [])
    if device_id not in devices:
        if len(devices) >= 2:
            raise HTTPException(403, "Maximum devices for this session reached")
        devices.append(device_id)

    return payload


@router.post("/api/dispatcher-session")
async def create_session(
    body: DispatcherSessionRequest,  # optional: send_to email
    settings=Depends(get_settings),
    email_adapter=Depends(get_email_adapter),
) -> dict:
    ttl = settings.app_config.dispatcher_session_ttl_hours
    token = create_dispatcher_token(settings, ttl)
    url = f"{settings.base_url}/dispatcher?token={token}"

    if body.send_to:
        await email_adapter.send_dispatcher_link(url, body.send_to, ttl)

    return {"url": url, "expires_in_hours": ttl}
```

---

## 6. App 3 — Router / Notification System

The router is a standalone FastAPI service that:
1. Listens for `new_case` events via PostgreSQL `LISTEN/NOTIFY` (explained below)
2. Determines routing based on phase, urgency, and config
3. Dispatches through pluggable channel adapters
4. Tracks per-notification state in `forms.notifications`

### 6.1 How PostgreSQL LISTEN/NOTIFY works

PostgreSQL has a built-in publish/subscribe mechanism that requires no external message broker.

**Publishing** (in the form service, after saving a case):
```sql
SELECT pg_notify('new_case', '<case-uuid>');
```
This can be called from SQL or via SQLAlchemy. It fires immediately when the transaction commits.

**Subscribing** (in the router service):
```python
conn = await asyncpg.connect(dsn)
await conn.add_listener("new_case", callback)
```
asyncpg registers `callback` with the PostgreSQL server. When any connection calls `pg_notify('new_case', ...)`, every listening connection receives the notification asynchronously — no polling, no delay. The payload is the string passed to `pg_notify` (here, the case UUID).

The router keeps a long-lived asyncpg connection open solely for listening. SQLAlchemy connections are used separately for queries. This is intentional — LISTEN/NOTIFY requires a persistent connection that isn't returned to a pool.

```python
# apps/router/src/listener.py
import asyncio
import asyncpg
import logging

log = logging.getLogger(__name__)


async def listen_for_cases(dsn: str, router: "AlertRouter", settings) -> None:
    conn = await asyncpg.connect(dsn)
    await conn.add_listener(
        "new_case",
        lambda _conn, _pid, _channel, case_id: asyncio.create_task(
            _handle_new_case(case_id, router, settings)
        ),
    )
    log.info("👂 Listening for new_case notifications on PostgreSQL")
    try:
        while True:
            await asyncio.sleep(3600)  # keep-alive; the listener fires on notify
    finally:
        await conn.close()


async def _handle_new_case(case_id: str, router: "AlertRouter", settings) -> None:
    case = await fetch_case(case_id)
    alert = CaseAlert(
        case_id=case.id,
        friendly_id=case.friendly_id,
        urgency=case.urgency,
        location=case.form_data.get("location"),
        is_urgent=case.urgency == "urgent",
    )
    phase = Phase(case.phase)
    await router.route(alert, phase)
```

### 6.2 Notification state model

```python
# apps/router/src/models.py
from enum import StrEnum

class NotifState(StrEnum):
    PENDING   = "pending"
    SENT      = "sent"
    ACKED     = "acked"    # requires a human to have explicitly acknowledged
    FAILED    = "failed"
    RETRYING  = "retrying"
    ESCALATED = "escalated"


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = {"schema": "conduct"}

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id         = Column(UUID(as_uuid=True), ForeignKey("forms.cases.id"))
    # Channel names: "email" | "signal" | "telephony" | "mattermost" | "slack"
    # "telephony" is technology-agnostic; Jambonz implements this channel for EMF 2026
    channel         = Column(String(32))
    state           = Column(String(16), default=NotifState.PENDING)
    attempt_count   = Column(Integer, default=0)
    last_attempt_at = Column(DateTime(timezone=True))
    message_id      = Column(String(256), nullable=True)  # email Message-ID for threading
    acked_by        = Column(String(128), nullable=True)
    acked_at        = Column(DateTime(timezone=True), nullable=True)
    created_at      = Column(DateTime(timezone=True),
                             default=lambda: datetime.now(tz=timezone.utc))
```

### 6.3 Channel adapter interface

`phase` is removed from `CaseAlert` — the router already knows the phase and doesn't need to pass it into the dataclass. `summary` is removed — adapters extract what they need from the structured fields; we don't pre-generate a text summary (that could leak PII or omit relevant context).

```python
# apps/router/src/channels/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class CaseAlert:
    case_id: str       # UUID — used internally; humans reference friendly_id
    friendly_id: str   # four-word hyphenated ID — used in all human-facing output
    urgency: str
    location: dict | None    # {"text": ..., "lat": ..., "lon": ...}
    is_urgent: bool
    case_url: str      # full URL to the case in the conduct panel, e.g. https://panel.example.org/cases/<uuid>


class ChannelAdapter(ABC):

    @abstractmethod
    async def is_available(self) -> bool: ...

    @abstractmethod
    async def send(self, alert: CaseAlert) -> str | None:
        """Send the alert. Return the message ID on success, None on failure."""
        ...

    @abstractmethod
    async def send_ack_confirmation(
        self, alert: CaseAlert, acked_by: str, original_message_id: str | None = None
    ) -> None:
        """Notify other recipients that someone ACK'd. Thread on original_message_id if possible."""
        ...
```

### 6.4 Email adapter

Email threading: `send()` returns the `Message-ID` of the sent message. This is stored in the `Notification` record. When `send_ack_confirmation()` is called, it sets `References` and `In-Reply-To` headers so mail clients thread the ACK under the original alert.

```python
# apps/router/src/channels/email.py
import email.utils
import aiosmtplib
from email.mime.text import MIMEText

from .base import ChannelAdapter, CaseAlert

URGENCY_EMOJI = {"urgent": "🚨", "high": "⚠️", "medium": "📋", "low": "ℹ️"}

# EmailAdapter reads SMTP settings from AppConfig.smtp (not constructor args).
# to_addrs comes from AppConfig.notification_emails (a list for multi-recipient send).
class EmailAdapter(ChannelAdapter):
    def __init__(self, smtp: "SmtpConfig", to_addrs: list[str]):
        self._smtp = smtp
        self._to = to_addrs

    async def is_available(self) -> bool:
        try:
            async with aiosmtplib.SMTP(self._smtp.host, self._smtp.port,
                                        use_tls=self._smtp.use_tls) as smtp:
                await smtp.noop()
            return True
        except Exception:
            return False

    async def send(self, alert: CaseAlert) -> str | None:
        emoji = URGENCY_EMOJI.get(alert.urgency, "📋")
        location_text = (alert.location or {}).get("text") or "not specified"

        msg = MIMEText(
            f"New {alert.urgency} conduct case.\n\n"
            f"Case: {alert.friendly_id}\n"
            f"Urgency: {alert.urgency}\n"
            f"Location: {location_text}\n"
            f"Case URL: {alert.case_url}\n\n"
            f"— EMF Conduct System"
        )
        msg["Subject"] = f"{emoji} [{alert.urgency.upper()}] Conduct case {alert.friendly_id}"
        msg["From"] = self._smtp.from_addr
        msg["To"] = ", ".join(self._to)
        # Generate a deterministic Message-ID so we can reference it later
        msg["Message-ID"] = email.utils.make_msgid(domain="emf_forms")

        try:
            async with aiosmtplib.SMTP(self._smtp.host, self._smtp.port,
                                        use_tls=self._smtp.use_tls) as smtp:
                await smtp.send_message(msg)
            return msg["Message-ID"]   # caller stores this for threading
        except Exception:
            return None

    async def send_ack_confirmation(
        self, alert: CaseAlert, acked_by: str, original_message_id: str | None = None
    ) -> None:
        msg = MIMEText(f"✅ Case {alert.friendly_id} acknowledged by {acked_by}")
        msg["Subject"] = f"✅ ACK: {alert.friendly_id}"
        msg["From"] = self._smtp.from_addr
        msg["To"] = ", ".join(self._to)
        msg["Message-ID"] = email.utils.make_msgid(domain="emf_forms")
        if original_message_id:
            # These headers cause mail clients to thread the ACK under the original alert
            msg["In-Reply-To"] = original_message_id
            msg["References"] = original_message_id

        async with aiosmtplib.SMTP(self._smtp.host, self._smtp.port,
                                    use_tls=self._smtp.use_tls) as smtp:
            await smtp.send_message(msg)
```

### 6.5 Signal adapter

```python
# apps/router/src/channels/signal.py
import httpx
from .base import ChannelAdapter, CaseAlert

URGENCY_EMOJI = {"urgent": "🚨", "high": "⚠️", "medium": "📋", "low": "ℹ️"}


class SignalAdapter(ChannelAdapter):
    """Wraps self-hosted signal-cli-rest-api."""

    def __init__(self, api_url: str, sender: str, group_id: str):
        self._api_url = api_url.rstrip("/")
        self._sender = sender
        self._group_id = group_id

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(verify=True) as client:
                r = await client.get(f"{self._api_url}/v1/health", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    async def send(self, alert: CaseAlert) -> str | None:
        emoji = URGENCY_EMOJI.get(alert.urgency, "📋")
        location_text = (alert.location or {}).get("text") or "unknown"
        message = (
            f"{emoji} *New {alert.urgency} case: {alert.friendly_id}*\n"
            f"Location: {location_text}\n"
            f"Case: {alert.case_url}"
        )
        try:
            async with httpx.AsyncClient(verify=True) as client:
                r = await client.post(
                    f"{self._api_url}/v2/send",
                    json={"message": message, "number": self._sender,
                          "recipients": [f"group/{self._group_id}"]},
                    timeout=10,
                )
            # Signal doesn't return a stable message ID we can use for threading
            return "signal-sent" if r.status_code in (200, 201) else None
        except Exception:
            return None

    async def send_ack_confirmation(
        self, alert: CaseAlert, acked_by: str, original_message_id: str | None = None
    ) -> None:
        message = f"✅ Case {alert.friendly_id} ACK'd by {acked_by}"
        async with httpx.AsyncClient(verify=True) as client:
            await client.post(
                f"{self._api_url}/v2/send",
                json={"message": message, "number": self._sender,
                      "recipients": [f"group/{self._group_id}"]},
                timeout=10,
            )
```

### 6.6 Routing logic

Signal routing is config-driven via `signal_mode` in `AppConfig`:
- `"always"` — Signal receives all event-time alerts alongside phone
- `"fallback_only"` — Signal only if phone system is unavailable
- `"high_priority_and_fallback"` — Signal for high/urgent cases + fallback if phone down

```python
# apps/router/src/router.py
import asyncio
import logging

from emf_forms.phase import Phase
from .channels.base import CaseAlert, ChannelAdapter

log = logging.getLogger(__name__)
RETRY_DELAYS_MINUTES = [5, 10, 15]


class AlertRouter:
    def __init__(
        self,
        email: ChannelAdapter,
        signal: ChannelAdapter | None = None,
        phone: ChannelAdapter | None = None,
        signal_mode: str = "always",
    ):
        self._email = email
        self._signal = signal
        self._phone = phone
        self._signal_mode = signal_mode

    async def route(self, alert: CaseAlert, phase: Phase) -> None:
        if phase == Phase.EVENT_TIME:
            await self._route_event_time(alert)
        else:
            await self._route_off_event(alert)

    async def _route_event_time(self, alert: CaseAlert) -> None:
        # Email always — audit trail
        asyncio.create_task(self._send_with_retry(self._email, alert))

        phone_available = self._phone and await self._phone.is_available()
        if phone_available:
            asyncio.create_task(self._send_with_retry(self._phone, alert))
            log.info("📞 Routing %s via telephony", alert.friendly_id)

        await self._maybe_signal(alert, phone_available=bool(phone_available))

    async def _maybe_signal(self, alert: CaseAlert, phone_available: bool) -> None:
        if not self._signal:
            return
        signal_available = await self._signal.is_available()
        if not signal_available:
            return

        send_signal = False
        if self._signal_mode == "always":
            send_signal = True
        elif self._signal_mode == "fallback_only":
            send_signal = not phone_available
        elif self._signal_mode == "high_priority_and_fallback":
            send_signal = alert.urgency in ("high", "urgent") or not phone_available

        if send_signal:
            asyncio.create_task(self._send_with_retry(self._signal, alert))

    async def _route_off_event(self, alert: CaseAlert) -> None:
        await self._send_with_retry(self._email, alert)

    async def _send_with_retry(self, adapter: ChannelAdapter, alert: CaseAlert) -> None:
        for attempt, delay in enumerate([0] + RETRY_DELAYS_MINUTES):
            if delay:
                log.info("Retry %d for %s in %d min", attempt, alert.friendly_id, delay)
                await asyncio.sleep(delay * 60)
            try:
                result = await adapter.send(alert)
                if result is not None:
                    return
            except Exception:
                log.exception("Send failed (attempt %d) for %s", attempt + 1, alert.friendly_id)
        log.error("❌ All retries exhausted for case %s", alert.friendly_id)
```

---

## 7. App 4 — TTS Service

Use [Piper TTS](https://github.com/rhasspy/piper) — fast, local, open-source neural TTS. No cloud dependency or data egress.

**Streaming vs file**: Piper can stream WAV to stdout. The TTS service exposes two endpoints:
- `POST /synthesise` — returns a `StreamingResponse` (WAV audio). For direct consumers that can play streaming audio.
- `POST /synthesise/file` — saves to a temp file and returns a URL via `GET /audio/{token}`. For Jambonz, which needs a URL to pull audio from.

Long text is **truncated** silently at `MAX_TEXT_LEN`, not rejected with an error. The TTS service is internal-only and receives pre-built messages — truncation is a safety net, not a user-facing error.

```python
# apps/tts/src/main.py
import re
import secrets
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse, JSONResponse

app = FastAPI(title="EMF TTS Service")
PIPER_MODEL = Path("/models/en_GB-alan-medium.onnx")
MAX_TEXT_LEN = 500

# In-memory map of token -> temp file path (for /audio/<token> endpoint)
_audio_files: dict[str, Path] = {}


def _sanitise(text: str) -> str:
    return re.sub(r"[^\w\s\.,!?'\-:]", "", text)[:MAX_TEXT_LEN]


@app.post("/synthesise")
async def synthesise_stream(req: TTSRequest) -> StreamingResponse:
    safe_text = _sanitise(req.text)

    def generate():
        proc = subprocess.Popen(
            ["piper", "--model", str(PIPER_MODEL), "--output-raw"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        stdout, _ = proc.communicate(input=safe_text.encode(), timeout=30)
        yield stdout

    return StreamingResponse(generate(), media_type="audio/wav")


@app.post("/synthesise/file")
async def synthesise_file(req: TTSRequest) -> JSONResponse:
    """Saves audio to a temp file and returns a URL. Used by Jambonz."""
    safe_text = _sanitise(req.text)
    token = secrets.token_urlsafe(16)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        outpath = Path(f.name)

    subprocess.run(
        ["piper", "--model", str(PIPER_MODEL), "--output_file", str(outpath)],
        input=safe_text.encode(), capture_output=True, timeout=30, check=True,
    )
    _audio_files[token] = outpath
    return JSONResponse({"audio_url": f"/audio/{token}"})


@app.get("/audio/{token}")
async def serve_audio(token: str):
    path = _audio_files.get(token)
    if not path or not path.exists():
        from fastapi import HTTPException
        raise HTTPException(404)
    from fastapi.responses import FileResponse
    return FileResponse(path, media_type="audio/wav")


@app.get("/health")
async def health():
    return {"status": "ok" if PIPER_MODEL.exists() else "degraded",
            "model": str(PIPER_MODEL)}
```

TTS message builder:

```python
# apps/tts/src/builder.py
from emf_forms_router.channels.base import CaseAlert

URGENCY_WORD = {"urgent": "URGENT", "high": "high priority",
                "medium": "medium priority", "low": "low priority"}


def build_tts_message(alert: CaseAlert) -> str:
    urgency = URGENCY_WORD.get(alert.urgency, alert.urgency)
    spoken_id = alert.friendly_id.replace("-", " ")  # "tiger lamp blue moon", not "tiger-lamp..."
    location = (alert.location or {}).get("text") or "location not specified"
    return (
        f"New {urgency} conduct case. "
        f"Case reference: {spoken_id}. "
        f"Location: {location}. "
        f"Press 1 to acknowledge. Press 2 to pass to the next responder."
    )
```

---

## 8. App 5 — Jambonz Adapter

EMF 2026 specific. Implements the `telephony` channel. Explicitly treat as throw-away — the `ChannelAdapter` interface it satisfies must remain stable.

```python
# apps/jambonz/src/adapter.py
import httpx
from emf_forms_router.channels.base import ChannelAdapter, CaseAlert
from .builder import build_tts_message


class JambonzAdapter(ChannelAdapter):
    def __init__(self, api_url, api_key, account_sid, application_sid,
                 call_group_number, shift_leader_number, escalation_number,
                 tts_service_url):
        self._api_url = api_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._account_sid = account_sid
        self._application_sid = application_sid
        self._call_group = call_group_number
        self._shift_leader = shift_leader_number
        self._escalation = escalation_number
        self._tts_url = tts_service_url

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(verify=True) as client:
                r = await client.get(
                    f"{self._api_url}/v1/Accounts/{self._account_sid}",
                    headers=self._headers, timeout=5,
                )
            return r.status_code == 200
        except Exception:
            return False

    async def send(self, alert: CaseAlert) -> str | None:
        # Request a file URL from TTS (Jambonz pulls audio by URL)
        async with httpx.AsyncClient(verify=True) as client:
            tts = await client.post(f"{self._tts_url}/synthesise/file",
                                    json={"text": build_tts_message(alert)}, timeout=15)
        if tts.status_code != 200:
            return None

        audio_url = tts.json()["audio_url"]
        async with httpx.AsyncClient(verify=True) as client:
            r = await client.post(
                f"{self._api_url}/v1/Accounts/{self._account_sid}/Calls",
                json={
                    "from": "conduct",
                    "to": {"type": "phone", "number": self._call_group},
                    "application_sid": self._application_sid,
                    "tag": {"case_id": alert.case_id, "friendly_id": alert.friendly_id,
                            "tts_audio_url": audio_url},
                },
                headers=self._headers, timeout=10,
            )
        return str(r.json().get("sid")) if r.status_code in (200, 201) else None

    async def send_ack_confirmation(self, alert, acked_by, original_message_id=None):
        pass  # ACK confirmation delegated to SignalAdapter by the router
```

Escalation wrapper:

```python
# apps/jambonz/src/escalation.py
ESCALATION_SEQUENCE = [
    ("_call_group",      0),
    ("_shift_leader",    5),
    ("_escalation",     10),
]


async def escalating_call(adapter: JambonzAdapter, alert: CaseAlert) -> None:
    for attr, delay_minutes in ESCALATION_SEQUENCE:
        if delay_minutes:
            await asyncio.sleep(delay_minutes * 60)
        number = getattr(adapter, attr, None)
        if not number:
            continue
        call_id = await adapter.send_to(alert, number)
        if call_id and await wait_for_ack(call_id, timeout_minutes=5):
            return
    log.error("🚨 No ACK after full escalation for case %s", alert.friendly_id)
```

---

## 9. Security Hardening

### 9.1 OWASP Top 10 (2025) test checklist

```python
# tests/security/test_owasp.py

# A01 — Broken Access Control
async def test_dispatcher_token_cannot_read_case_form_data(client):
    token = create_dispatcher_token(ttl_hours=1)
    r = await client.get(f"/api/cases/{CASE_ID}",
                         headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403

async def test_panel_viewer_role_cannot_read_form_data(db):
    result = await db.execute(
        "SET ROLE panel_viewer; SELECT form_data FROM forms.cases LIMIT 1"
    )
    # Should raise PermissionError — panel_viewer has no SELECT on form_data column
    ...

# A02 — Cryptographic Failures
def test_caddy_enforces_tls13_and_h2(caddy_config_text):
    assert "min_version tls1.3" in caddy_config_text
    assert "protocols h2" in caddy_config_text

# A03 — Injection
async def test_sql_injection_in_form_fields(client, db):
    r = await client.post("/api/submit", json={
        **VALID_PAYLOAD, "what_happened": "'; DROP TABLE forms.cases; --"
    })
    assert r.status_code in (201, 422)
    count = await db.scalar("SELECT COUNT(*) FROM forms.cases")
    assert count >= 0  # table must still exist

# A04 — Insecure Design (honeypot)
async def test_honeypot_submission_returns_fake_ok_but_is_not_saved(client, db):
    r = await client.post("/api/submit", json={**VALID_PAYLOAD, "website": "http://bot.example"})
    assert r.status_code in (200, 201)
    assert r.json()["case_id"] == "00000000-0000-0000-0000-000000000000"
    result = await db.fetchrow(
        "SELECT id FROM forms.cases WHERE friendly_id = 'ok'"
    )
    assert result is None

# A05 — Security Misconfiguration
def test_no_debug_mode_in_prod():
    settings = Settings(_env_file=".env.prod")
    assert not getattr(settings, "debug", False)

# A06 — Vulnerable Components — enforced in CI via pip-audit

# A07 — Identification & Auth Failures
async def test_non_conduct_team_user_cannot_access_panel(client, regular_user_token):
    r = await client.get("/api/cases", headers={"Authorization": f"Bearer {regular_user_token}"})
    assert r.status_code == 403

async def test_expired_dispatcher_token_is_rejected(client):
    token = create_dispatcher_token(ttl_hours=-1)  # already expired
    r = await client.get("/dispatcher", params={"token": token})
    assert r.status_code == 401

# A08 — Software and Data Integrity — pinned uv.lock + gitleaks hooks

# A09 — Security Logging & Monitoring
async def test_status_transition_creates_history_row(client, db):
    r = await client.patch(f"/api/cases/{CASE_ID}/status", json={"status": "assigned"})
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT * FROM forms.case_history WHERE case_id = $1 ORDER BY changed_at DESC LIMIT 1",
        CASE_ID,
    )
    assert row["new_value"] == "assigned"

# A10 — SSRF
async def test_url_in_additional_info_is_stored_not_fetched(client):
    r = await client.post("/api/submit", json={
        **VALID_PAYLOAD, "additional_info": "http://169.254.169.254/latest/meta-data/"
    })
    assert r.status_code in (201, 422)
    # No outbound HTTP request should have been made (verify via mock or network policy)
```

---

## 10. Observability

### 10.1 Health endpoints (every service)

```json
{
  "status": "ok",
  "checks": {
    "database": "ok",
    "signal": "ok",
    "telephony": "degraded"
  },
  "version": "0.1.0"
}
```

### 10.2 Prometheus metrics

```python
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Counter, Histogram

Instrumentator().instrument(app).expose(app, endpoint="/metrics")

cases_submitted = Counter(
    "emf_cases_submitted_total", "Total conduct cases submitted",
    ["urgency", "phase", "event_name"],
)
notification_dispatch_seconds = Histogram(
    "emf_notification_dispatch_seconds", "Time to dispatch a notification",
    ["channel"],
)
# Hash the IP — never store raw IPs in metrics (PII)
submission_attempts = Counter(
    "emf_form_submission_attempts_total", "Form submission attempts",
    ["result"],  # "success" | "honeypot" | "rate_limited" | "validation_error"
)
```

### 10.3 Grafana dashboard panels

`infra/grafana/dashboards/<service>.json`. Key panels per service:

| Panel | Type | Metric |
|---|---|---|
| Cases submitted per phase | Bar | `emf_cases_submitted_total` |
| Urgency breakdown | Pie | `emf_cases_submitted_total{urgency}` |
| Notification state | Stacked bar | `emf_notification_state_total` |
| Submission rate anomaly | Time series | `rate(emf_form_submission_attempts_total[5m])` |
| Channel health | Stat (green/amber/red) | `/health` scrape |
| p50/p99 dispatch latency | Time series | `emf_notification_dispatch_seconds` |

---

## 11. CI Pipeline

`.github/workflows/ci.yml`:

```yaml
name: CI
on: [push, pull_request]

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:17-alpine
        env:
          POSTGRES_DB: emf_forms_test
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
        options: >-
          --health-cmd pg_isready --health-interval 10s --health-timeout 5s --health-retries 5

    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv sync --all-extras
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy apps/ shared/
      - run: uv run bandit -r apps/ shared/ -c pyproject.toml
      - run: uv run pytest --tb=short -q
        env:
          DATABASE_URL: postgresql+asyncpg://test:test@localhost/emf_forms_test
      - run: uv run pip-audit
```

---

## 12. Implementation Sequence

| Phase | Deliverable | Depends on |
|---|---|---|
| 0 | Repo scaffold, pre-commit, Docker Compose, Caddy snippets, Postgres roles, shared lib, `generate_secrets.py` | — |
| 1 | App 1: form (models + history table, validation, routes, full template, rate limiting, honeypot, map pin) | Phase 0 |
| 2 | App 2: panel (OIDC/UFFD auth, case list, status machine with history, mypy clean) | Phase 1 |
| 3 | App 2b: dispatcher view (token generation, hard expiry, device limit, send-by-email) | Phase 2 |
| 4 | App 3: router (email adapter with threading, Signal adapter, config-driven signal mode, pg LISTEN) | Phase 1 |
| 5 | App 4: TTS service (Piper, streaming + file endpoints, message builder) | Phase 0 |
| 6 | App 5: Jambonz adapter (call flow, escalation chain) | Phases 4, 5 |
| 7 | Observability (health endpoints, Prometheus, Grafana dashboard JSON) | All |
| 8 | OWASP test suite, column-level DB permission tests, security hardening review | All |
| 9 | `install.py` script, `backup.py` script, wordlist generation script | Phase 0 |
| 10 | App 2c: admin app | Phase 9+ |

---

## 13. Supporting Scripts

### 13.1 Guided installation script (`scripts/install.py`)

Python + [Rich](https://github.com/Textualize/rich) for interactive TUI. Generates `docker-compose.yml` based on selected components.

Use alive-progress for progress bars, especially on docker pulls and such (multi-stage/nested tasks) -- don't leave the user/sysadmin wondering what's happening.

Capabilities:
- **Component selection**: which apps to install (form, panel, router, TTS, Jambonz)
- **Proxy choice**: Caddy (default), nginx, Traefik
  - For nginx/Traefik: add certbot + systemd timer for cert renewal (warn if renewal not configured before 47-day ACME expiry)
- **TLS cert method**: HTTP challenge (default), DNS challenge, manual
- **Standard flags** — all must be supported; order-independent; conflicting flags print help and exit:
  - `-q` / `--quiet` — suppress non-essential output
  - `-v` / `--verbose` — verbose output
  - `-d` / `--debug` — debug output (implies verbose)
  - `-h` / `--help` — show help
  - `--dry-run` — print what would be done, make no changes
- **Validation** -  validates all files are good, e.g. docker-compose is valid; we have things populated that we need in .env etc; we don't do https on nginx until after certbot's run etc.

```python
# scripts/install.py — structural sketch
# Dependencies: rich, alive-progress (both added to scripts/pyproject.toml)
import argparse
from alive_progress import alive_bar  # for docker pull / multi-stage progress bars
from rich.console import Console
from rich.prompt import Confirm, Prompt

def parse_args():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group()
    g.add_argument("-q", "--quiet", action="store_true")
    g.add_argument("-v", "--verbose", action="store_true")
    g.add_argument("-d", "--debug", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()

def main():
    args = parse_args()
    console = Console(quiet=args.quiet)
    # ... interactive prompts, then generate docker-compose.yml + Caddyfile
```

### 13.2 Secret generation (`scripts/generate_secrets.py`)

Reads `.env.example`, replaces `changeme` placeholders with `secrets.token_urlsafe(32)`, writes `.env`. Skips values that are already non-default (idempotent).

### 13.3 Database backup (`scripts/backup.py`)

- `pg_dump` with `--format=custom` (binary, supports selective restore)
- Compress with `zstd` (fast, good ratio)
- Encrypt with `age` (simple, modern; recipient = sysadmin's public key)
- Filename: `emf_forms-<ISO8601-datetime>.dump.zst.age`
- Store locally + optionally rsync to a remote
- (optional, via `--systemd` flag) Install as a systemd timer on the host machine; generates a `.service` + `.timer` unit file and runs `systemctl enable --now`

### 13.4 Wordlist generation (`scripts/generate_wordlist.py`)

- Source: `wordfreq` top 20k English words (or `/usr/share/dict/words`)
- Filters: 4–8 characters, alpha only, no proper nouns, profanity block list, no homographs
- Output: `shared/emf_forms/wordlist.txt` (~10,000 words)
- Commit the output; re-run only when wordlist needs updating

---

## 14. Open Questions — Status

| # | Question | Status | Decision |
|---|---|---|---|
| 1 | TTS provider | ✅ Decided | Piper TTS (local, open-source, UK English `en_GB-alan-medium`) |
| 2 | Bot protection | ✅ Decided | Honeypot + rate limiting always; hCaptcha optional (configurable in `config.json`); rate limit thresholds relaxed for known EMF IP ranges |
| 3 | SSO provider | ✅ Decided | UFFD (https://github.com/emfcamp/uffd). Local dev: `ghcr.io/navikt/mock-oauth2-server` |
| 4 | Urgency levels | ✅ Decided | `low`, `medium`, `high`, `urgent` |
| 5 | Signal deployment | ✅ Decided | Self-hosted `signal-cli-rest-api` in Docker on the same VPS |
| 6 | Data retention | ✅ Decided | Manual process post-event. Export to CSV required in the panel. PII purge schedule TBD with conduct team. |
| 7 | Admin app (2c) | ✅ Decided | Defer to post-launch (Phase 10) |
| 8 | ACK mechanism (Signal/email) | ✅ Decided | Signal: emoji reply (🤙 = ACK), parsed by signal-cli-rest-api webhook. Email: magic link with one-time token. |
| 9 | Mattermost + Slack instances | 🔲 Pending | Both use incoming webhook URLs. Confirm Mattermost workspace with EMF team. Slack webhook similarly configurable. Both set in `config.json`; same payload shape used for both. |
| 10 | Sub-roles in conduct team | ✅ Decided | Start flat (`team_member` = full access). `panel_viewer` is a separate DB role for the dispatcher view only, not an SSO group. |
| 11 | Phase selection for post-event reports | ✅ Decided | User selects event from dropdown; `current_phase()` used for routing only |
| 12 | Email threading | ✅ Decided | Capture `Message-ID` from initial send; use `References` + `In-Reply-To` for ACK/update emails |


## Additional design requirements

### Form submission idempotency (App 1)
If a user retries a submission (e.g. double-tap, flaky connection), the system must not create a duplicate case. Design:
- Generate a client-side idempotency token (UUID) in the form page on load; include as a hidden field.
- The form service stores the token with the case on first submission; a second submission with the same token returns the existing case's `friendly_id` with a friendly message rather than creating a new case.
- Token scope: per browser session (no login required on the public form).
- Show a clear message: "It looks like this was already submitted — your reference is `tiger-lamp-blue-moon`."

### Back-button / browser history state (App 1)
If the user presses back after submitting, form fields must remain populated. Design:
- Use the History API (`history.replaceState`) to persist form state in the session history entry.
- On page load, restore from `history.state` if present.
- Do not re-submit on restore; show a "previously entered" notice if restoring from state.

### Slack adapter (App 3 — Router)
Add Slack as a notification channel alongside Mattermost. Both use incoming webhooks:
- `slack_webhook` in `config.json` (same pattern as `mattermost_webhook`).
- A `SlackAdapter` implementing the same `ChannelAdapter` interface.
- Same alert format: urgency emoji + friendly_id + location + case URL.
- ACK via Slack emoji reaction is a future consideration (requires Slack Events API, not just webhooks).

## TO DO list


### Image attachments (App 1 — public form)
Allow reporters to attach up to 3 images per submission. Design notes:

- **Upload endpoint**: separate `POST /attachments` endpoint on the form service; returns a token used in the case submission payload (not stored inline in the case JSON).
- **Content scanning**: before accepting, run a content-safety check (`libmagic` for MIME type verification; reject anything that isn't an allowed image type regardless of file extension).
- **Virus/malware scan**: integrate ClamAV (self-hosted, Docker) as part of the upload pipeline; reject on positive hit.
- **Storage**: configurable in `config.json` (`attachment_backend`: `"local"` or `"minio"`). If MinIO: run as a Docker service alongside the stack, enable server-side encryption (`SSE-S3` or `SSE-KMS`), and include MinIO data volume in the backup script.
- **Access control**: attachment retrieval requires a valid `team_conduct` session (same SSO as the panel). Implement a signed-URL proxy endpoint on the panel service: `GET /cases/<uuid>/attachments/<id>` — checks session, streams the file. Direct file paths never exposed.
- **Admin panel display**: show thumbnails inline in the case detail view; clicking opens the signed-URL proxy.
- **Limits**: max 3 files per case, max 10 MB per file; accepted types: JPEG, PNG, HEIC, WebP.
- **Retention**: attachments follow the same retention schedule as the case record.

---

## 15. Detailed Implementation TODO

Granular task checklist, organised by phase. Each phase maps to the sequence in Section 12.
Tick items off as they are completed. Phases can overlap where dependencies allow.

---

### Phase 0 — Repository & Infrastructure Scaffold

#### Repository setup
- [ ] Create monorepo directory skeleton: `.github/workflows/`, `infra/caddy/snippets/`, `infra/postgres/migrations/`, `infra/grafana/dashboards/`, `shared/emf_forms/`, `apps/form/`, `apps/panel/`, `apps/router/`, `apps/tts/`, `apps/jambonz/`, `scripts/`
- [ ] Commit `.gitignore` (`.env`, `config.json`, `__pycache__/`, `.venv/`, `*.pyc`, `*.pyo`, `uv.lock` per-service, `*.egg-info/`)
- [ ] Commit `.gitleaks.toml` with EMF-specific API key pattern rule
- [ ] Commit `.pre-commit-config.yaml` (ruff, ruff-format, bandit, gitleaks, mypy hooks)
- [ ] Run `pre-commit install` locally; verify all hooks execute cleanly on an empty commit
- [ ] Commit `.env-example` with all secret placeholders (`changeme`)
- [ ] Commit `config.json-example` with full example config (events, SMTP, urgency levels, pronouns, signal settings)

#### Shared library (`shared/`)
- [ ] `uv init shared/` and configure `pyproject.toml` (ruff, mypy strict, bandit, pytest-asyncio)
- [ ] Implement `shared/emf_forms/config.py` (`SignalPadding`, `EventConfig`, `SmtpConfig`, `AppConfig`, `Settings`)
- [ ] Implement `shared/emf_forms/phase.py` (`Phase` enum, `current_phase()`, `is_active_routing_window()`, `events_for_form()`)
- [ ] Implement `shared/emf_forms/db.py` (`init_db()`, `get_session()` async generator, TLS-required connection args)
- [ ] Write `scripts/generate_wordlist.py` (wordfreq source, 4–8 char filter, profanity screen, inclusive-language pass, ~10k output)
- [ ] Run wordlist generator; commit `shared/emf_forms/wordlist.txt`
- [ ] Implement `shared/emf_forms/friendly_id.py` (`generate()`, `generate_unique()` with UUID fallback)
- [ ] Write unit tests for shared lib:
  - [ ] `test_phase.py`: pre-event, event-time, post-event, active routing window with padding, multi-event config
  - [ ] `test_config.py`: end_date < start_date raises, missing required fields raise, example file validates cleanly
  - [ ] `test_friendly_id.py`: output is four hyphen-separated words, collision avoidance, UUID fallback after 10 attempts

#### PostgreSQL
- [ ] Write `infra/postgres/00_roles.sql`: all roles (`form_user`, `router_user`, `service_user`, `panel_viewer`, `team_member`, `backup_user`, `emf_forms_admin`), schema creation, all GRANT statements, `security_barrier` views, RLS policy
- [ ] Generate self-signed TLS cert + key for PostgreSQL; add to `infra/postgres/certs/` (gitignored); add cert generation to install script
- [ ] Document `postgresql.conf` TLS settings needed (ssl=on, cert paths)

#### Caddy
- [ ] Write `infra/caddy/snippets/tls.caddy` (TLS 1.3 min, HTTP/2 only)
- [ ] Write `infra/caddy/snippets/headers.caddy` (HSTS, X-Content-Type-Options, X-Frame-Options, CSP, Referrer-Policy, Permissions-Policy, strip Server header)
- [ ] Write `infra/caddy/Caddyfile.local` (`.internal` TLD, imports snippets, reverse proxies for form/panel/router)
- [ ] Write `infra/caddy/Caddyfile.prod` (real domains, ACME email, `${PROJECT_NAME}` prefix on service names)

#### Docker Compose
- [ ] Write `infra/docker-compose.yml` base (caddy, postgres with TLS + healthcheck, form, panel, msg-router; `mock-oidc` under `local` profile; `signal-api`)
- [ ] Verify `docker compose --profile local up` starts all services and postgres healthcheck passes
- [ ] Verify Caddy serves HTTPS on `.internal` hostnames with local certs
- [ ] Verify inter-service TLS (PostgreSQL `ssl=require` enforced from app side)

#### Secret generation script
- [ ] Write `scripts/generate_secrets.py` (reads `.env-example`, replaces `changeme` with `secrets.token_urlsafe(32)`, idempotent — skips existing non-default values, writes `.env` with `chmod 600`)

---

### Phase 1 — App 1: Public Report Form

#### Project setup
- [ ] `uv init apps/form`; configure `pyproject.toml` (fastapi, uvicorn[standard], sqlalchemy[asyncio], asyncpg, pydantic, pydantic-settings, slowapi, jinja2, aiosmtplib)
- [ ] Add dev deps: pytest, pytest-asyncio, httpx, ruff, bandit, mypy, pre-commit, pip-audit

#### Data model & migrations
- [ ] Write `apps/form/src/models.py` (`Case` and `CaseHistory` SQLAlchemy models, schema `forms`)
- [ ] Initialise Alembic in `apps/form/`; write initial migration creating `forms.cases` and `forms.case_history`
- [ ] Verify migration runs cleanly against a fresh Postgres container

#### Validation schemas
- [ ] Write `apps/form/src/schemas.py`:
  - [ ] `Location` model (optional text + lat/lon, at-least-one validator, lat/lon range validators)
  - [ ] `ReporterDetails` model (name, pronouns, phone with T9/DECT/international allowance and normalisation, email, camping_with)
  - [ ] `CaseSubmission` model (all Section 1 + Section 2 fields, urgency validator against config, honeypot field, strip_whitespace validator on all long-text fields)

#### Route handlers
- [ ] Write `apps/form/src/routes.py`:
  - [ ] `GET /` — render form with phase context, events list, `is_active_routing_window` flag
  - [ ] `POST /api/submit` — honeypot check, urgency/event validation, friendly_id generation, Case insert, `pg_notify('new_case', ...)`, idempotency token check
  - [ ] `GET /health` — DB ping, return structured JSON
- [ ] Write `apps/form/src/main.py` — FastAPI app, slowapi rate limiter middleware, router include

#### Frontend assets
- [ ] Download Material Design 3 CSS + fonts locally to `apps/form/static/` (no external CDN references)
- [ ] Write `apps/form/templates/base.html`:
  - [ ] `<HOME>` nav link top-left
  - [ ] Footer nav: privacy policy | code of conduct | about | map
  - [ ] Responsive meta viewport, accessible font sizes (WCAG AA minimum)
- [ ] Write `apps/form/templates/form.html`:
  - [ ] Phase-aware DECT alert banner (amber background, not red/green; large accessible text)
  - [ ] CSS-hidden honeypot field (`tabindex="-1"`, `autocomplete="off"`, `aria-hidden="true"`)
  - [ ] Event selection `<select>` (pre-selects current event during active routing window)
  - [ ] Section 1 of 2 — reporter details: name, pronouns (`<input>` + `<datalist>` from config), email (`inputmode="email"`), phone (`type="tel"`) and camping_with (both conditional on `is_active_routing_window`)
  - [ ] Section 2 of 2 — incident: `what_happened` (required, `minlength="10"`), date picker, time (`type="time"`, defaults to now via JS), location text + progressive-enhancement map pin
  - [ ] Urgency `<select>` (conditional on `is_active_routing_window`)
  - [ ] Additional context fieldset (optional, labelled "can be filled later")
  - [ ] `<button type="submit">` with accessible loading state
- [ ] Write `apps/form/templates/success.html` (friendly_id display, idempotency "already submitted" variant)
- [ ] Write `apps/form/static/form.js`:
  - [ ] Client-side field validation mirroring backend rules (length, phone charset, email format, required fields)
  - [ ] Specific, helpful error messages (over-length message with current/max counts; phone invalid-char message naming the bad character)
  - [ ] Datetime defaults: populate time field with current HH:MM on page load; populate date with today
  - [ ] Map pin-drop (Leaflet.js — downloaded locally): show map on JS available; click writes lat/lon into hidden fields; hide map if JS unavailable
  - [ ] Back-button state persistence: `history.replaceState` on field changes; restore from `history.state` on load; "previously entered" notice when restoring

#### Tests
- [ ] Test valid submission → 201, Case row in DB, correct fields
- [ ] Test honeypot filled → fake 200, no Case row in DB
- [ ] Test rate limit: 6th request in 1 minute → 429
- [ ] Test idempotency: same token twice → 200 with existing friendly_id, single DB row
- [ ] Test invalid urgency → 422
- [ ] Test unknown event_name → 422
- [ ] Test `what_happened` below 10 chars → 422
- [ ] Test `what_happened` above 10,000 chars → 422
- [ ] Test SQL injection string in all text fields → 201 or 422, table still exists
- [ ] Test Location with neither text nor coords → 422
- [ ] Test DECT extension (`1234`), T9 code (`ADAM`), international (`+44 7700 900000`) all accepted as phone
- [ ] Test `@` in phone number → 422
- [ ] Test `pg_notify` fired after successful insert (mock listener)
- [ ] Test `/health` returns `{"status": "ok"}` when DB up, `{"status": "degraded"}` when DB down

#### Docker
- [ ] Write `apps/form/Dockerfile` (multi-stage: build with uv, run as non-root user)
- [ ] Add form service to `docker-compose.yml` with correct env, volumes, depends_on

---

### Phase 2 — App 2: Conduct Team Panel

#### Project setup
- [ ] `uv init apps/panel`; configure `pyproject.toml` (fastapi, uvicorn, sqlalchemy, asyncpg, pydantic, authlib, itsdangerous, jinja2)

#### Authentication
- [ ] Write `apps/panel/src/auth.py`:
  - [ ] `configure_oauth()` — register UFFD OIDC provider via `authlib`
  - [ ] `require_conduct_team()` — dependency: check session, check `team_conduct` in groups claim, 303 to `/login` if unauthenticated, 403 if unauthorised
- [ ] Write login (`GET /login`), callback (`GET /auth/callback`), and logout (`GET /logout`) routes
- [ ] Verify mock-oauth2-server works end-to-end locally (login → callback → session → protected route)

#### Case management routes
- [ ] Write `apps/panel/src/routes.py`:
  - [ ] `GET /` → case list (filter by status, urgency, assignee, tag; sort by created_at / urgency)
  - [ ] `GET /cases/{id}` → case detail (full form_data, history timeline)
  - [ ] `PATCH /api/cases/{id}/status` → transition with `VALID_TRANSITIONS` enforcement + `CaseHistory` row
  - [ ] `PATCH /api/cases/{id}/assignee` → update assignee + history row
  - [ ] `PATCH /api/cases/{id}/tags` → update tags (merge / replace) + history row
  - [ ] `GET /api/tags` → return list of all distinct existing tags (for autocomplete)
  - [ ] `GET /health` → DB ping + structured JSON

#### Templates
- [ ] Download Material Design 3 CSS + fonts locally to `apps/panel/static/`
- [ ] Write `apps/panel/templates/base.html` (nav, SSO username display, logout link, responsive)
- [ ] Write `apps/panel/templates/cases.html` (sortable/filterable case list; urgency badge colours; status chip)
- [ ] Write `apps/panel/templates/case_detail.html` (full case view; history timeline; inline tag editor with autocomplete; assignee picker; status transition buttons showing only valid next states)
- [ ] Write `apps/panel/templates/dispatcher_share.html` (generate dispatcher URL, optional "email to" field, active sessions count, revoke button)
- [ ] Add privacy policy, code of conduct, about, map footer pages (static templates)

#### Tests
- [ ] Test unauthenticated `GET /` → 303 to `/login`
- [ ] Test user not in `team_conduct` → 403
- [ ] Test `team_conduct` member can list cases
- [ ] Test valid status transition → 200, DB updated, `CaseHistory` row added with correct `changed_by`
- [ ] Test `closed` → any transition rejected → 422
- [ ] Test `new` → `in_progress` (skipping `assigned`) rejected → 422
- [ ] Test tag autocomplete returns existing tags
- [ ] Test case detail does not expose `form_data` to `panel_viewer` DB role

#### Docker
- [ ] Write `apps/panel/Dockerfile`
- [ ] Add panel service to `docker-compose.yml`

---

### Phase 3 — App 2b: Dispatcher View

#### Token management
- [ ] Write `apps/panel/src/dispatcher.py`:
  - [ ] `create_dispatcher_token()` — JWT with `jti`, `exp`, `iat`, `scope="dispatcher"`
  - [ ] `validate_dispatcher_token()` — decode, check revocation set, check scope, enforce max-2-device limit
  - [ ] In-memory revocation set + device map (Redis-backed for restart resilience — add redis to docker-compose)
- [ ] `POST /api/dispatcher-session` route (conduct team only): create token, build URL, optional send-to-email, return `{url, expires_in_hours}`
- [ ] `POST /api/dispatcher-session/{jti}/revoke` route (conduct team only): add jti to revocation set

#### Dispatcher routes & view
- [ ] `GET /dispatcher` — authenticate via `?token=` query param; set device_id cookie; render stripped view
- [ ] `POST /api/dispatcher/ack/{case_id}` — mark notification acked; trigger ACK confirmation on all channels
- [ ] `POST /api/dispatcher/trigger/{case_id}` — manually re-trigger routing for a case
- [ ] Write `apps/panel/templates/dispatcher.html`: urgency badge, friendly_id, status, ACK button, trigger-call button — no reporter PII visible

#### Tests
- [ ] Test valid token → 200, dispatcher view rendered
- [ ] Test expired token (`exp` in past) → 401
- [ ] Test token with wrong `scope` → 403
- [ ] Test revoked token → 401
- [ ] Test first device → allowed; second device → allowed; third device → 403
- [ ] Test dispatcher `GET /api/cases/{id}` → 403 (no access to full case data)
- [ ] Test ACK updates notification state and fires ACK confirmation on channels
- [ ] Test `send_to` email sends dispatcher URL to specified address

---

### Phase 4 — App 3: Router / Notification System

#### Project setup
- [ ] `uv init apps/router`; configure `pyproject.toml` (fastapi, uvicorn, sqlalchemy, asyncpg, aiosmtplib, httpx, pydantic)

#### Data model & migrations
- [ ] Write `apps/router/src/models.py` (`Notification` model, `NotifState` StrEnum)
- [ ] Write Alembic migration creating `forms.notifications`

#### Channel adapters
- [ ] Write `apps/router/src/channels/base.py` (`CaseAlert` dataclass, `ChannelAdapter` ABC with `is_available()`, `send()`, `send_ack_confirmation()`)
- [ ] Write `apps/router/src/channels/email.py` (`EmailAdapter`: SMTP via aiosmtplib, urgency emoji subject prefix, Message-ID capture, In-Reply-To/References on ACK)
- [ ] Write `apps/router/src/channels/signal.py` (`SignalAdapter`: signal-cli-rest-api v2/send, group recipient, ACK confirmation message)
- [ ] Write `apps/router/src/channels/mattermost.py` (`MattermostAdapter`: incoming webhook POST, same alert format)
- [ ] Write `apps/router/src/channels/slack.py` (`SlackAdapter`: incoming webhook POST, same alert format)

#### Routing logic
- [ ] Write `apps/router/src/router.py` (`AlertRouter`: phase-aware routing, `signal_mode` logic, `_send_with_retry` with [0, 5, 10, 15] min delays, notification state persistence per attempt)
- [ ] Write `apps/router/src/listener.py` (long-lived asyncpg connection, `LISTEN new_case`, dispatch to `AlertRouter.route()` via `asyncio.create_task`)
- [ ] Write `apps/router/src/main.py` (FastAPI app, startup event launches listener, `GET /health` with checks for email/signal/telephony availability)

#### ACK handling
- [ ] Write Signal webhook handler: receive emoji reactions from signal-cli-rest-api; 🤙 emoji → mark notification acked, fire ACK confirmations
- [ ] Write email ACK handler: one-time magic link endpoint (`GET /ack/{token}`) → mark acked, fire ACK confirmations, invalidate token

#### Tests
- [ ] Test `EmailAdapter.send()` sends correct headers, returns Message-ID
- [ ] Test `EmailAdapter.send_ack_confirmation()` sets `In-Reply-To` and `References`
- [ ] Test `EmailAdapter.is_available()` returns False when SMTP unreachable
- [ ] Test `SignalAdapter.send()` posts to correct group endpoint
- [ ] Test `AlertRouter` event-time: email + phone always sent; Signal per `signal_mode`
- [ ] Test `signal_mode="always"` → Signal sent even when phone available
- [ ] Test `signal_mode="fallback_only"` → Signal only when phone unavailable
- [ ] Test `signal_mode="high_priority_and_fallback"` → Signal for urgent + fallback
- [ ] Test `_route_off_event()` → only email sent
- [ ] Test retry: `send()` returns None → retried 3× at correct intervals
- [ ] Test notification state updated in DB: `pending` → `sent` on success, `failed` after all retries exhausted
- [ ] Test LISTEN/NOTIFY: mock `pg_notify`, verify `AlertRouter.route()` called
- [ ] Test Signal emoji ACK → notification marked `acked`, ACK confirmation sent
- [ ] Test email magic link ACK → notification marked `acked`, token invalidated, second use rejected

#### Docker
- [ ] Write `apps/router/Dockerfile`
- [ ] Add `msg-router` service to `docker-compose.yml`

---

### Phase 5 — App 4: TTS Service

#### Project setup
- [ ] `uv init apps/tts`; configure `pyproject.toml` (fastapi, uvicorn, pydantic)

#### Service implementation
- [ ] Write `apps/tts/src/main.py`:
  - [ ] `POST /synthesise` — sanitise input, run Piper subprocess, return `StreamingResponse(audio/wav)`
  - [ ] `POST /synthesise/file` — sanitise, run Piper to temp file, store token→path map, return `{audio_url}`
  - [ ] `GET /audio/{token}` — serve temp file, 404 on unknown/expired token
  - [ ] `GET /health` — check Piper model file exists; return `{status: "ok"|"degraded", model: ...}`
- [ ] Write `apps/tts/src/builder.py` (`build_tts_message()`: urgency word map, spoken friendly_id with hyphens-to-spaces, location fallback, DTMF prompts)
- [ ] Add temp file cleanup: purge `_audio_files` entries older than N minutes (configurable)

#### Tests
- [ ] Test `build_tts_message()` output for all urgency levels (correct urgency word)
- [ ] Test friendly_id hyphens replaced with spaces in spoken output
- [ ] Test `_sanitise()` strips disallowed characters
- [ ] Test `_sanitise()` truncates at `MAX_TEXT_LEN`
- [ ] Test `POST /synthesise` returns `audio/wav` content-type (mock Piper subprocess)
- [ ] Test `POST /synthesise/file` returns JSON with `audio_url`
- [ ] Test `GET /audio/{token}` serves file; unknown token → 404
- [ ] Test `GET /health` returns `degraded` when model path absent

#### Docker
- [ ] Write `apps/tts/Dockerfile` (download Piper binary + `en_GB-alan-medium.onnx` model at build time; run as non-root)
- [ ] Add TTS service to `docker-compose.yml`

---

### Phase 6 — App 5: Jambonz Adapter

#### Project setup
- [ ] `uv init apps/jambonz`; configure `pyproject.toml` (httpx, fastapi, pydantic)

#### Adapter implementation
- [ ] Write `apps/jambonz/src/adapter.py` (`JambonzAdapter`: `is_available()` against Accounts endpoint; `send()` — get TTS file URL, POST to Jambonz Calls API with `tag` payload; `send_ack_confirmation()` — no-op, delegated to Signal)
- [ ] Write `apps/jambonz/src/escalation.py` (`ESCALATION_SEQUENCE` with delays, `escalating_call()`, `wait_for_ack()` polling notification state)
- [ ] Write Jambonz webhook handler (`POST /webhook/jambonz`): receive DTMF input from Jambonz application; digit `1` → ACK; digit `2` → pass to next in escalation sequence
- [ ] Integrate escalation with `AlertRouter`'s `_send_with_retry` (replace generic retry with `escalating_call` for telephony channel)

#### Tests (all against mocked Jambonz API)
- [ ] Test `is_available()` → True on 200, False on non-200 / exception
- [ ] Test `send()` → calls TTS `/synthesise/file`, then Jambonz Calls API; returns call SID on 201
- [ ] Test `send()` → returns None when TTS fails
- [ ] Test DTMF digit `1` webhook → marks notification `acked`
- [ ] Test DTMF digit `2` webhook → triggers next escalation target
- [ ] Test escalation sequence: call_group → (5 min) shift_leader → (10 min) escalation number
- [ ] Test no ACK after full sequence → logs error with `🚨`

#### Docker
- [ ] Write `apps/jambonz/Dockerfile`
- [ ] Add Jambonz service to `docker-compose.yml`

---

### Phase 7 — Observability

#### Prometheus instrumentation (all services)
- [ ] Add `prometheus-fastapi-instrumentator` to all service dependencies
- [ ] Add `Instrumentator().instrument(app).expose(app, endpoint="/metrics")` to all `main.py` files
- [ ] Add `emf_cases_submitted_total` counter (labels: urgency, phase, event_name) to form service
- [ ] Add `emf_form_submission_attempts_total` counter (labels: result = success/honeypot/rate_limited/validation_error) to form service
- [ ] Add `emf_notification_dispatch_seconds` histogram (label: channel) to router service
- [ ] Add `emf_notification_state_total` gauge (label: state) to router service
- [ ] Verify no raw IP addresses in any metric labels (hash if needed)

#### Health endpoints (all services)
- [ ] Extend `/health` on form service: `{status, checks: {database}, version}`
- [ ] Extend `/health` on panel service: `{status, checks: {database, oidc_reachable}, version}`
- [ ] Extend `/health` on router service: `{status, checks: {database, email, signal, telephony}, version}`
- [ ] Extend `/health` on TTS service: `{status, checks: {piper_model}, version}`
- [ ] Extend `/health` on Jambonz adapter: `{status, checks: {jambonz_api}, version}`

#### Grafana dashboards
- [ ] Write `infra/grafana/dashboards/form.json` (panels: cases submitted per phase bar, urgency breakdown pie, submission rate anomaly time-series, p50/p99 request latency)
- [ ] Write `infra/grafana/dashboards/router.json` (panels: notification state stacked bar, dispatch latency histogram, channel health stat panels, retry/escalation counters)
- [ ] Write `infra/grafana/dashboards/panel.json` (panels: case status distribution, SSO login events, active dispatcher sessions)
- [ ] Write `infra/grafana/dashboards/tts.json` (panels: synthesis request rate, synthesis latency, health status)
- [ ] Add Prometheus + Grafana services to `docker-compose.yml` under `monitoring` profile
- [ ] Verify dashboards import cleanly and all panels resolve their metrics

---

### Phase 8 — Security Hardening

#### OWASP Top 10 (2025) test suite
- [ ] Write `tests/security/test_owasp.py` covering all 10 categories (reference Section 9.1):
  - [ ] A01 Broken Access Control: dispatcher token cannot read `form_data`; `panel_viewer` DB role cannot SELECT `form_data` column; `form_user` cannot UPDATE
  - [ ] A02 Cryptographic Failures: Caddy config enforces TLS 1.3 + HTTP/2; no secrets in `config.json`; `.env` permissions check
  - [ ] A03 Injection: SQL injection strings in all form fields; XSS payloads in text fields stored as-is, not executed
  - [ ] A04 Insecure Design: honeypot returns fake-OK, no DB row; idempotency token prevents duplicate case
  - [ ] A05 Security Misconfiguration: no debug mode in prod; server header stripped; no stack traces in API errors
  - [ ] A06 Vulnerable Components: `pip-audit` clean run in CI
  - [ ] A07 Identification & Auth Failures: non-`team_conduct` user → 403; expired dispatcher token → 401; brute-force on dispatcher token → rate limited
  - [ ] A08 Software and Data Integrity: `uv.lock` committed and pinned; gitleaks pre-commit hook fires on test cred
  - [ ] A09 Security Logging & Monitoring: status transitions create `CaseHistory` rows; failed auth attempts logged
  - [ ] A10 SSRF: URL in `additional_info` stored, not fetched; no outbound HTTP triggered by user input

#### Database permission tests
- [ ] Test `panel_viewer` role cannot SELECT `form_data` from `forms.cases`
- [ ] Test `form_user` role cannot UPDATE any row
- [ ] Test `router_user` can only SELECT from `cases_router` view, not base table
- [ ] Test RLS `team_isolation`: team A rows not visible when `app.current_team_id` set to team B UUID
- [ ] Test `backup_user` can SELECT all tables, cannot INSERT/UPDATE/DELETE

#### General hardening checks
- [ ] Verify Caddy rejects TLS 1.2 connections (`curl --tlsv1.2 --tls-max 1.2 https://...` → connection refused)
- [ ] Verify all required security headers present on all Caddy-proxied responses
- [ ] Verify CSP does not include `unsafe-eval`
- [ ] Verify `bandit -r apps/ shared/` reports zero findings (or all suppressed with justification)
- [ ] Verify `mypy --strict` passes on all services and shared lib
- [ ] Verify gitleaks hook fires on a test commit containing a fake credential pattern
- [ ] Review and document all data minimisation decisions (what is collected, legal basis, retention period placeholder)
- [ ] Write abuse detection test: simulate >20 submissions in 1 hour from same hashed source → `emf_form_submission_attempts_total{result="rate_limited"}` counter increments

---

### Phase 9 — Supporting Scripts

#### `scripts/install.py`
- [ ] Implement CLI arg parsing (`-q`, `-v`, `-d`, `--dry-run`, `--help`; mutually exclusive verbosity flags; conflicting flags print help and exit)
- [ ] Implement component selection prompts (form, panel, router, TTS, Jambonz)
- [ ] Implement proxy selection (Caddy default; nginx/Traefik with certbot warning re: 47-day ACME expiry)
- [ ] Implement TLS cert method selection (HTTP challenge, DNS challenge, manual)
- [ ] Docker Compose template generation from selections (enable/disable service blocks)
- [ ] Caddyfile generation (substitute `${PROJECT_NAME}`, select local vs prod template)
- [ ] PostgreSQL TLS cert generation (idempotent, using `cryptography` library)
- [ ] Signal group registration walkthrough (if Signal component selected): register phone number, list groups, populate `signal_group_id` in config
- [ ] Validation pass: `docker compose config` check; `.env` completeness (no remaining `changeme`); required config keys present; TLS certs exist
- [ ] Progress bars with `alive-progress` for docker pull / image build stages
- [ ] `--dry-run` mode prints all actions without executing

#### `scripts/backup.py`
- [ ] `pg_dump --format=custom` via subprocess
- [ ] `zstd` compression (piped)
- [ ] `age` encryption (recipient = sysadmin public key from config/arg)
- [ ] Filename: `emf_forms-<ISO8601>.dump.zst.age`
- [ ] Optional rsync to remote path (from config)
- [ ] `--systemd` flag: generate `.service` + `.timer` unit files, run `systemctl enable --now`

---

### Phase 10 — CI / CD

- [ ] Write `.github/workflows/ci.yml`: checkout, setup-uv, `uv sync --all-extras`, ruff check, ruff format check, mypy, bandit, pytest (with Postgres service container), pip-audit
- [ ] Write `.github/workflows/security.yml`: scheduled (weekly) gitleaks scan across full git history
- [ ] Verify CI pipeline passes on a clean clone
- [ ] Add branch protection rules: `main` and `develop` require CI pass + 1 review before merge

---

### Phase 11 — Documentation

- [ ] Write root `README.md`: project overview, prerequisites, quick-start (`docker compose up`), architecture diagram, link to each app README
- [ ] Write `apps/form/README.md`: purpose, config options, form field reference
- [ ] Write `apps/panel/README.md`: SSO setup, user roles, dispatcher session usage
- [ ] Write `apps/router/README.md`: channel adapters, `signal_mode` values, ACK mechanisms
- [ ] Write `apps/tts/README.md`: Piper model selection, endpoint reference
- [ ] Write `apps/jambonz/README.md`: Jambonz API prerequisites, escalation config
- [ ] Write root `CLAUDE.md`: project conventions, key file paths, dev commands, test commands, deploy commands
- [ ] Write per-app `CLAUDE.md` files (app-specific conventions and entry points)
- [ ] Write privacy policy page content (`apps/form/templates/privacy.html`)
- [ ] Write code of conduct page content (`apps/form/templates/conduct.html`)
- [ ] Write about page content (`apps/form/templates/about.html`)

---

### Phase 12 — Image Attachments (App 1 add-on)

_Depends on Phase 1 complete. Can be delivered post-launch._

- [ ] Add `POST /attachments` endpoint to form service (returns opaque token)
- [ ] Integrate `libmagic` MIME verification (reject non-image regardless of extension)
- [ ] Add ClamAV container to `docker-compose.yml`; integrate scan into upload pipeline
- [ ] Implement configurable attachment backend (`attachment_backend: "local" | "minio"`)
- [ ] If MinIO: add MinIO service to Docker Compose, enable SSE, add MinIO volume to backup script
- [ ] Add `GET /cases/{uuid}/attachments/{id}` signed-URL proxy to panel service (team_conduct session required)
- [ ] Show thumbnails in case detail view; link to proxy endpoint
- [ ] Write attachment tests: MIME check rejects non-image; ClamAV positive → 400; max 3 files enforced; max 10 MB enforced; unauthenticated access → 403

---

### Phase 13 — App 2c: Admin App (deferred, post-launch)

_Low priority. Scope to be confirmed with EMF team._

- [ ] Define scope with EMF team (team provisioning, SSO group management, form config)
- [ ] Implement team provisioning UI (guided sysadmin flow from install script model)
- [ ] Implement SSO group → team mapping management
- [ ] Implement form/config management (does not expose case data)
- [ ] Write admin app tests