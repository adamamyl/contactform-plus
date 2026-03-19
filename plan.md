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
- [x] Create monorepo directory skeleton: `.github/workflows/`, `infra/caddy/snippets/`, `infra/postgres/migrations/`, `infra/grafana/dashboards/`, `shared/emf_forms/`, `apps/form/`, `apps/panel/`, `apps/router/`, `apps/tts/`, `apps/jambonz/`, `scripts/`
- [x] Commit `.gitignore` (`.env`, `config.json`, `__pycache__/`, `.venv/`, `*.pyc`, `*.pyo`, `uv.lock` per-service, `*.egg-info/`)
- [x] Commit `.gitleaks.toml` with EMF-specific API key pattern rule
- [x] Commit `.pre-commit-config.yaml` (ruff, ruff-format, bandit, gitleaks, mypy hooks)
- [x] Run `pre-commit install` locally; verify all hooks execute cleanly on an empty commit
- [x] Commit `.env-example` with all secret placeholders (`changeme`)
- [x] Commit `config.json-example` with full example config (events, SMTP, urgency levels, pronouns, signal settings)

#### Shared library (`shared/`)
- [x] `uv init shared/` and configure `pyproject.toml` (ruff, mypy strict, bandit, pytest-asyncio)
- [x] Implement `shared/emf_forms/config.py` (`SignalPadding`, `EventConfig`, `SmtpConfig`, `AppConfig`, `Settings`)
- [x] Implement `shared/emf_forms/phase.py` (`Phase` enum, `current_phase()`, `is_active_routing_window()`, `events_for_form()`)
- [x] Implement `shared/emf_forms/db.py` (`init_db()`, `get_session()` async generator, TLS-required connection args)
- [x] Write `scripts/generate_wordlist.py` (wordfreq source, 4–8 char filter, profanity screen, inclusive-language pass, ~10k output)
- [x] Run wordlist generator; commit `shared/emf_forms/wordlist.txt`
- [x] Implement `shared/emf_forms/friendly_id.py` (`generate()`, `generate_unique()` with UUID fallback)
- [x] Write unit tests for shared lib:
  - [x] `test_phase.py`: pre-event, event-time, post-event, active routing window with padding, multi-event config
  - [x] `test_config.py`: end_date < start_date raises, missing required fields raise, example file validates cleanly
  - [x] `test_friendly_id.py`: output is four hyphen-separated words, collision avoidance, UUID fallback after 10 attempts

#### PostgreSQL
- [x] Write `infra/postgres/00_roles.sql`: all roles (`form_user`, `router_user`, `service_user`, `panel_viewer`, `team_member`, `backup_user`, `emf_forms_admin`), schema creation, all GRANT statements, `security_barrier` views, RLS policy
- [x] Generate self-signed TLS cert + key for PostgreSQL; add to `infra/postgres/certs/` (gitignored); add cert generation to install script
- [x] Document `postgresql.conf` TLS settings needed (ssl=on, cert paths)

#### Caddy
- [x] Write `infra/caddy/snippets/tls.caddy` (TLS 1.3 min, HTTP/2 only)
- [x] Write `infra/caddy/snippets/headers.caddy` (HSTS, X-Content-Type-Options, X-Frame-Options, CSP, Referrer-Policy, Permissions-Policy, strip Server header)
- [x] Write `infra/caddy/Caddyfile.local` (`.internal` TLD, imports snippets, reverse proxies for form/panel/router)
- [x] Write `infra/caddy/Caddyfile.prod` (real domains, ACME email, `${PROJECT_NAME}` prefix on service names)

#### Docker Compose
- [x] Write `infra/docker-compose.yml` base (caddy, postgres with TLS + healthcheck, form, panel, msg-router; `mock-oidc` under `local` profile; `signal-api`)
- [x] Verify `docker compose --profile local up` starts all services and postgres healthcheck passes
- [x] Verify Caddy serves HTTPS on `.internal` hostnames with local certs
- [x] Verify inter-service TLS (PostgreSQL `ssl=require` enforced from app side)

#### Secret generation script
- [x] Write `scripts/generate_secrets.py` (reads `.env-example`, replaces `changeme` with `secrets.token_urlsafe(32)`, idempotent — skips existing non-default values, writes `.env` with `chmod 600`)

---

### Phase 1 — App 1: Public Report Form

#### Project setup
- [x] `uv init apps/form`; configure `pyproject.toml` (fastapi, uvicorn[standard], sqlalchemy[asyncio], asyncpg, pydantic, pydantic-settings, slowapi, jinja2, aiosmtplib)
- [x] Add dev deps: pytest, pytest-asyncio, httpx, ruff, bandit, mypy, pre-commit, pip-audit

#### Data model & migrations
- [x] Write `apps/form/src/models.py` (`Case` and `CaseHistory` SQLAlchemy models, schema `forms`)
- [x] Initialise Alembic in `apps/form/`; write initial migration creating `forms.cases` and `forms.case_history`
- [x] Verify migration runs cleanly against a fresh Postgres container

#### Validation schemas
- [x] Write `apps/form/src/schemas.py`:
  - [x] `Location` model (optional text + lat/lon, at-least-one validator, lat/lon range validators)
  - [x] `ReporterDetails` model (name, pronouns, phone with T9/DECT/international allowance and normalisation, email, camping_with)
  - [x] `CaseSubmission` model (all Section 1 + Section 2 fields, urgency validator against config, honeypot field, strip_whitespace validator on all long-text fields)

#### Route handlers
- [x] Write `apps/form/src/routes.py`:
  - [x] `GET /` — render form with phase context, events list, `is_active_routing_window` flag
  - [x] `POST /api/submit` — honeypot check, urgency/event validation, friendly_id generation, Case insert, `pg_notify('new_case', ...)`, idempotency token check
  - [x] `GET /health` — DB ping, return structured JSON
- [x] Write `apps/form/src/main.py` — FastAPI app, slowapi rate limiter middleware, router include

#### Frontend assets
- [x] Download Material Design 3 CSS + fonts locally to `apps/form/static/` (no external CDN references)
- [x] Write `apps/form/templates/footer.html` — minimal shared partial; links to existing emfcamp.org pages for privacy policy, code of conduct, about, and map (no locally-hosted copies)
- [x] Write `apps/form/templates/base.html`:
  - [x] `<HOME>` nav link top-left
  - [x] Include `footer.html` partial via Jinja2 `{% include %}`
  - [x] Responsive meta viewport, accessible font sizes (WCAG AA minimum)
- [x] Write `apps/form/templates/form.html`:
  - [x] Phase-aware DECT alert banner (amber background, not red/green; large accessible text)
  - [x] CSS-hidden honeypot field (`tabindex="-1"`, `autocomplete="off"`, `aria-hidden="true"`)
  - [x] Event selection `<select>` (pre-selects current event during active routing window)
  - [x] Section 1 of 2 — reporter details: name, pronouns (`<input>` + `<datalist>` from config), email (`inputmode="email"`), phone (`type="tel"`) and camping_with (both conditional on `is_active_routing_window`)
  - [x] Section 2 of 2 — incident: `what_happened` (required, `minlength="10"`), date picker, time (`type="time"`, defaults to now via JS), location text + progressive-enhancement map pin
  - [x] Urgency `<select>` (conditional on `is_active_routing_window`)
  - [x] Additional context fieldset (optional, labelled "can be filled later")
  - [x] `<button type="submit">` with accessible loading state
- [x] Write `apps/form/templates/success.html` (friendly_id display, idempotency "already submitted" variant)
- [x] Write `apps/form/static/form.js`:
  - [x] Client-side field validation mirroring backend rules (length, phone charset, email format, required fields)
  - [x] Specific, helpful error messages (over-length message with current/max counts; phone invalid-char message naming the bad character)
  - [x] Datetime defaults: populate time field with current HH:MM on page load; populate date with today
  - [x] Map pin-drop (Leaflet.js — downloaded locally): show map on JS available; click writes lat/lon into hidden fields; hide map if JS unavailable
  - [x] Back-button state persistence: `history.replaceState` on field changes; restore from `history.state` on load; "previously entered" notice when restoring

#### Tests
- [x] Test valid submission → 201, Case row in DB, correct fields
- [x] Test honeypot filled → fake 200, no Case row in DB
- [x] Test rate limit: 6th request in 1 minute → 429
- [x] Test idempotency: same token twice → 200 with existing friendly_id, single DB row
- [x] Test invalid urgency → 422
- [x] Test unknown event_name → 422
- [x] Test `what_happened` below 10 chars → 422
- [x] Test `what_happened` above 10,000 chars → 422
- [x] Test SQL injection string in all text fields → 201 or 422, table still exists
- [x] Test Location with neither text nor coords → 422
- [x] Test DECT extension (`1234`), T9 code (`ADAM`), international (`+44 7700 900000`) all accepted as phone
- [x] Test `@` in phone number → 422
- [x] Test `pg_notify` fired after successful insert (mock listener)
- [x] Test `/health` returns `{"status": "ok"}` when DB up, `{"status": "degraded"}` when DB down

#### Docker
- [x] Write `apps/form/Dockerfile` (multi-stage: build with uv, run as non-root user)
- [x] Add form service to `docker-compose.yml` with correct env, volumes, depends_on

---

### Phase 2 — App 2: Conduct Team Panel

#### Project setup
- [x] `uv init apps/panel`; configure `pyproject.toml` (fastapi, uvicorn, sqlalchemy, asyncpg, pydantic, authlib, itsdangerous, jinja2)

#### Authentication
- [x] Write `apps/panel/src/auth.py`:
  - [x] `configure_oauth()` — register UFFD OIDC provider via `authlib`
  - [x] `require_conduct_team()` — dependency: check session, check `team_conduct` in groups claim, 303 to `/login` if unauthenticated, 403 if unauthorised
- [x] Write login (`GET /login`), callback (`GET /auth/callback`), and logout (`GET /logout`) routes
- [x] Verify mock-oauth2-server works end-to-end locally (login → callback → session → protected route)

#### Case management routes
- [x] Write `apps/panel/src/routes.py`:
  - [x] `GET /` → case list (filter by status, urgency, assignee, tag; sort by created_at / urgency)
  - [x] `GET /cases/{id}` → case detail (full form_data, history timeline)
  - [x] `PATCH /api/cases/{id}/status` → transition with `VALID_TRANSITIONS` enforcement + `CaseHistory` row
  - [x] `PATCH /api/cases/{id}/assignee` → update assignee + history row
  - [x] `PATCH /api/cases/{id}/tags` → update tags (merge / replace) + history row
  - [x] `GET /api/tags` → return list of all distinct existing tags (for autocomplete)
  - [x] `GET /health` → DB ping + structured JSON

#### Templates
- [x] Download Material Design 3 CSS + fonts locally to `apps/panel/static/`
- [x] Copy or symlink `footer.html` partial from `apps/form/templates/` (or move to `shared/templates/` and reference from both apps)
- [x] Write `apps/panel/templates/base.html` (nav, SSO username display, logout link, responsive, include `footer.html` partial)
- [x] Write `apps/panel/templates/cases.html` (sortable/filterable case list; urgency badge colours; status chip)
- [x] Write `apps/panel/templates/case_detail.html` (full case view; history timeline; inline tag editor with autocomplete; assignee picker; status transition buttons showing only valid next states)
- [x] Write `apps/panel/templates/dispatcher_share.html` (generate dispatcher URL, optional "email to" field, active sessions count, revoke button)

#### Tests
- [x] Test unauthenticated `GET /` → 303 to `/login`
- [x] Test user not in `team_conduct` → 403
- [x] Test `team_conduct` member can list cases
- [x] Test valid status transition → 200, DB updated, `CaseHistory` row added with correct `changed_by`
- [x] Test `closed` → any transition rejected → 422
- [x] Test `new` → `in_progress` (skipping `assigned`) rejected → 422
- [x] Test tag autocomplete returns existing tags
- [x] Test case detail does not expose `form_data` to `panel_viewer` DB role

#### Docker
- [x] Write `apps/panel/Dockerfile`
- [x] Add panel service to `docker-compose.yml`

---

### Phase 3 — App 2b: Dispatcher View

#### Token management
- [x] Write `apps/panel/src/dispatcher.py`:
  - [x] `create_dispatcher_token()` — JWT with `jti`, `exp`, `iat`, `scope="dispatcher"`
  - [x] `validate_dispatcher_token()` — decode, check revocation set, check scope, enforce max-2-device limit
  - [x] In-memory revocation set + device map (Redis-backed for restart resilience — add redis to docker-compose)
- [x] `POST /api/dispatcher-session` route (conduct team only): create token, build URL, display URL, optional send-to-email, return `{url, expires_in_hours}`
- [x] `POST /api/dispatcher-session/{jti}/revoke` route (conduct team only): add jti to revocation set

#### Dispatcher routes & view
- [x] `GET /dispatcher` — authenticate via `?token=` query param; set device_id cookie; render stripped view
- [x] `GET /cases` → case list without sensitive data (filter by urgency, status, location; sort by created_at / updated_at / urgency; by default, **only show unassigned cases**)
- [x] `POST /api/dispatcher/ack/{case_id}` — mark notification acked; trigger ACK confirmation on all channels
- [x] `POST /api/dispatcher/trigger/{case_id}` — manually re-trigger routing for a case
- [x] Write `apps/panel/templates/dispatcher.html`: urgency badge, friendly_id, status, ACK button, trigger-call button — no reporter PII visible

#### Tests
- [x] Test valid token → 200, dispatcher view rendered
- [x] Test expired token (`exp` in past) → 401
- [x] Test token with wrong `scope` → 403
- [x] Test revoked token → 401
- [x] Test first device → allowed; second device → allowed; third device → 403
- [x] Test dispatcher `GET /api/cases/{id}` → 403 (no access to full case data)
- [x] Test ACK updates notification state and fires ACK confirmation on channels
- [x] Test `send_to` email sends dispatcher URL to specified address

---

### Phase 4 — App 3: Router / Notification System

#### Project setup
- [x] `uv init apps/router`; configure `pyproject.toml` (fastapi, uvicorn, sqlalchemy, asyncpg, aiosmtplib, httpx, pydantic)

#### Data model & migrations
- [x] Write `apps/router/src/models.py` (`Notification` model, `NotifState` StrEnum)
- [x] Write Alembic migration creating `forms.notifications`

#### Channel adapters
- [x] Write `apps/router/src/channels/base.py` (`CaseAlert` dataclass, `ChannelAdapter` ABC with `is_available()`, `send()`, `send_ack_confirmation()`)
- [x] Write `apps/router/src/channels/email.py` (`EmailAdapter`: SMTP via aiosmtplib, urgency emoji subject prefix, Message-ID capture, In-Reply-To/References on ACK)
- [x] Write `apps/router/src/channels/signal.py` (`SignalAdapter`: signal-cli-rest-api v2/send, group recipient, ACK confirmation message)
- [x] Write `apps/router/src/channels/mattermost.py` (`MattermostAdapter`: incoming webhook POST, same alert format)
- [x] Write `apps/router/src/channels/slack.py` (`SlackAdapter`: incoming webhook POST, same alert format)

#### Routing logic
- [x] Write `apps/router/src/router.py` (`AlertRouter`: phase-aware routing, `signal_mode` logic, `_send_with_retry` with [0, 5, 10, 15] min delays, notification state persistence per attempt)
- [x] Write `apps/router/src/listener.py` (long-lived asyncpg connection, `LISTEN new_case`, dispatch to `AlertRouter.route()` via `asyncio.create_task`)
- [x] Write `apps/router/src/main.py` (FastAPI app, startup event launches listener, `GET /health` with checks for email/signal/telephony availability)

#### ACK handling
- [x] Write Signal webhook handler: receive emoji reactions from signal-cli-rest-api; 🤙 emoji → mark notification acked, fire ACK confirmations
- [x] Write email ACK handler: one-time magic link endpoint (`GET /ack/{token}`) → mark acked, fire ACK confirmations, invalidate token

#### Tests
- [x] Test `EmailAdapter.send()` sends correct headers, returns Message-ID
- [x] Test `EmailAdapter.send_ack_confirmation()` sets `In-Reply-To` and `References`
- [x] Test `EmailAdapter.is_available()` returns False when SMTP unreachable
- [x] Test `SignalAdapter.send()` posts to correct group endpoint
- [x] Test `AlertRouter` event-time: email + phone always sent; Signal per `signal_mode`
- [x] Test `signal_mode="always"` → Signal sent even when phone available
- [x] Test `signal_mode="fallback_only"` → Signal only when phone unavailable
- [x] Test `signal_mode="high_priority_and_fallback"` → Signal for urgent + fallback
- [x] Test `_route_off_event()` → only email sent
- [x] Test retry: `send()` returns None → retried 3× at correct intervals
- [x] Test notification state updated in DB: `pending` → `sent` on success, `failed` after all retries exhausted
- [x] Test LISTEN/NOTIFY: mock `pg_notify`, verify `AlertRouter.route()` called
- [x] Test Signal emoji ACK → notification marked `acked`, ACK confirmation sent
- [x] Test email magic link ACK → notification marked `acked`, token invalidated, second use rejected

#### Docker
- [x] Write `apps/router/Dockerfile`
- [x] Add `msg-router` service to `docker-compose.yml`

---

### Phase 5 — App 4: TTS Service

#### Project setup
- [x] `uv init apps/tts`; configure `pyproject.toml` (fastapi, uvicorn, pydantic)

#### Service implementation
- [x] Write `apps/tts/src/main.py`:
  - [x] `POST /synthesise` — sanitise input, run Piper subprocess, return `StreamingResponse(audio/wav)`
  - [x] `POST /synthesise/file` — sanitise, run Piper to temp file, store token→path map, return `{audio_url}`
  - [x] `GET /audio/{token}` — serve temp file, 404 on unknown/expired token
  - [x] `GET /health` — check Piper model file exists; return `{status: "ok"|"degraded", model: ...}`
- [x] Write `apps/tts/src/builder.py` (`build_tts_message()`: urgency word map, spoken friendly_id with hyphens-to-spaces, location fallback, DTMF prompts)
- [x] Add temp file cleanup: purge `_audio_files` entries older than N minutes (configurable)

#### Tests
- [x] Test `build_tts_message()` output for all urgency levels (correct urgency word)
- [x] Test friendly_id hyphens replaced with spaces in spoken output
- [x] Test `_sanitise()` strips disallowed characters
- [x] Test `_sanitise()` truncates at `MAX_TEXT_LEN`
- [x] Test `POST /synthesise` returns `audio/wav` content-type (mock Piper subprocess)
- [x] Test `POST /synthesise/file` returns JSON with `audio_url`
- [x] Test `GET /audio/{token}` serves file; unknown token → 404
- [x] Test `GET /health` returns `degraded` when model path absent

#### Docker
- [x] Write `apps/tts/Dockerfile` (download Piper binary + `en_GB-alan-medium.onnx` model at build time; run as non-root)
- [x] Add TTS service to `docker-compose.yml`

---

### Phase 6 — App 5: Jambonz Adapter

#### Project setup
- [x] `uv init apps/jambonz`; configure `pyproject.toml` (httpx, fastapi, pydantic)

#### Adapter implementation
- [x] Write `apps/jambonz/src/adapter.py` (`JambonzAdapter`: `is_available()` against Accounts endpoint; `send()` — get TTS file URL, POST to Jambonz Calls API with `tag` payload; `send_ack_confirmation()` — no-op, delegated to Signal)
- [x] Write `apps/jambonz/src/escalation.py` (`ESCALATION_SEQUENCE` with delays, `escalating_call()`, `wait_for_ack()` polling notification state)
- [x] Write Jambonz webhook handler (`POST /webhook/jambonz`): receive DTMF input from Jambonz application; digit `1` → ACK; digit `2` → pass to next in escalation sequence
- [x] Integrate escalation with `AlertRouter`'s `_send_with_retry` (replace generic retry with `escalating_call` for telephony channel)

#### Tests (all against mocked Jambonz API)
- [x] Test `is_available()` → True on 200, False on non-200 / exception
- [x] Test `send()` → calls TTS `/synthesise/file`, then Jambonz Calls API; returns call SID on 201
- [x] Test `send()` → returns None when TTS fails
- [x] Test DTMF digit `1` webhook → marks notification `acked`
- [x] Test DTMF digit `2` webhook → triggers next escalation target
- [x] Test escalation sequence: call_group → (5 min) shift_leader → (10 min) escalation number
- [x] Test no ACK after full sequence → logs error with `🚨`

#### Docker
- [x] Write `apps/jambonz/Dockerfile`
- [x] Add Jambonz service to `docker-compose.yml`

---

### Phase 7 — Observability

#### Prometheus instrumentation (all services)
- [x] Add `prometheus-fastapi-instrumentator` to all service dependencies
- [x] Add `Instrumentator().instrument(app).expose(app, endpoint="/metrics")` to all `main.py` files
- [x] Add `emf_cases_submitted_total` counter (labels: urgency, phase, event_name) to form service
- [x] Add `emf_form_submission_attempts_total` counter (labels: result = success/honeypot/rate_limited/validation_error) to form service
- [x] Add `emf_notification_dispatch_seconds` histogram (label: channel) to router service
- [x] Add `emf_notification_state_total` gauge (label: state) to router service
- [x] Verify no raw IP addresses in any metric labels (hash if needed)

#### Health endpoints (all services)
- [x] Extend `/health` on form service: `{status, checks: {database}, version}`
- [x] Extend `/health` on panel service: `{status, checks: {database, oidc_reachable}, version}`
- [x] Extend `/health` on router service: `{status, checks: {database, email, signal, telephony}, version}`
- [x] Extend `/health` on TTS service: `{status, checks: {piper_model}, version}`
- [x] Extend `/health` on Jambonz adapter: `{status, checks: {jambonz_api}, version}`

#### Grafana dashboards
- [x] Write `infra/grafana/dashboards/form.json` (panels: cases submitted per phase bar, urgency breakdown pie, submission rate anomaly time-series, p50/p99 request latency)
- [x] Write `infra/grafana/dashboards/router.json` (panels: notification state stacked bar, dispatch latency histogram, channel health stat panels, retry/escalation counters)
- [x] Write `infra/grafana/dashboards/panel.json` (panels: case status distribution, SSO login events, active dispatcher sessions)
- [x] Write `infra/grafana/dashboards/tts.json` (panels: synthesis request rate, synthesis latency, health status)
- [x] Add Prometheus + Grafana services to `docker-compose.yml` under `monitoring` profile
- [x] Verify dashboards import cleanly and all panels resolve their metrics

---

### Phase 8 — Security Hardening

#### OWASP Top 10 (2025) test suite
- [x] Write `tests/security/test_owasp.py` covering all 10 categories (reference Section 9.1):
  - [x] A01 Broken Access Control: dispatcher token cannot read `form_data`; `panel_viewer` DB role cannot SELECT `form_data` column; `form_user` cannot UPDATE
  - [x] A02 Cryptographic Failures: Caddy config enforces TLS 1.3 + HTTP/2; no secrets in `config.json`; `.env` permissions check
  - [x] A03 Injection: SQL injection strings in all form fields; XSS payloads in text fields stored as-is, not executed
  - [x] A04 Insecure Design: honeypot returns fake-OK, no DB row; idempotency token prevents duplicate case
  - [x] A05 Security Misconfiguration: no debug mode in prod; server header stripped; no stack traces in API errors
  - [x] A06 Vulnerable Components: `pip-audit` clean run in CI
  - [x] A07 Identification & Auth Failures: non-`team_conduct` user → 403; expired dispatcher token → 401; brute-force on dispatcher token → rate limited
  - [x] A08 Software and Data Integrity: `uv.lock` committed and pinned; gitleaks pre-commit hook fires on test cred
  - [x] A09 Security Logging & Monitoring: status transitions create `CaseHistory` rows; failed auth attempts logged
  - [x] A10 SSRF: URL in `additional_info` stored, not fetched; no outbound HTTP triggered by user input

#### Database permission tests
- [x] Test `panel_viewer` role cannot SELECT `form_data` from `forms.cases`
- [x] Test `form_user` role cannot UPDATE any row
- [x] Test `router_user` can only SELECT from `cases_router` view, not base table
- [x] Test RLS `team_isolation`: team A rows not visible when `app.current_team_id` set to team B UUID
- [x] Test `backup_user` can SELECT all tables, cannot INSERT/UPDATE/DELETE

#### General hardening checks
- [x] Verify Caddy rejects TLS 1.2 connections (`curl --tlsv1.2 --tls-max 1.2 https://...` → connection refused)
- [x] Verify all required security headers present on all Caddy-proxied responses
- [x] Verify CSP does not include `unsafe-eval`
- [x] Verify `bandit -r apps/ shared/` reports zero findings (or all suppressed with justification)
- [x] Verify `mypy --strict` passes on all services and shared lib
- [x] Verify gitleaks hook fires on a test commit containing a fake credential pattern
- [x] Review and document all data minimisation decisions (what is collected, legal basis, retention period placeholder)
- [x] Write abuse detection test: simulate >20 submissions in 1 hour from same hashed source → `emf_form_submission_attempts_total{result="rate_limited"}` counter increments

---

### Phase 9 — Supporting Scripts

#### `scripts/install.py`
- [x] Implement CLI arg parsing (`-q`, `-v`, `-d`, `--dry-run`, `--help`; mutually exclusive verbosity flags; conflicting flags print help and exit)
- [x] Implement component selection prompts (form, panel, router, TTS, Jambonz)
- [x] Implement proxy selection (Caddy default; nginx/Traefik with certbot warning re: 47-day ACME expiry)
- [x] Implement TLS cert method selection (HTTP challenge, DNS challenge, manual)
- [x] Docker Compose template generation from selections (enable/disable service blocks)
- [x] Caddyfile generation (substitute `${PROJECT_NAME}`, select local vs prod template)
- [x] PostgreSQL TLS cert generation (idempotent, using `cryptography` library)
- [x] Signal group registration walkthrough (if Signal component selected): register phone number, list groups, populate `signal_group_id` in config
- [x] Validation pass: `docker compose config` check; `.env` completeness (no remaining `changeme`); required config keys present; TLS certs exist
- [x] Progress bars with `alive-progress` for docker pull / image build stages
- [x] `--dry-run` mode prints all actions without executing

#### `scripts/backup.py`
- [x] `pg_dump --format=custom` via subprocess
- [x] `zstd` compression (piped)
- [x] `age` encryption (recipient = sysadmin public key from config/arg)
- [x] Filename: `emf_forms-<ISO8601>.dump.zst.age`
- [x] Optional rsync to remote path (from config)
- [x] `--systemd` flag: generate `.service` + `.timer` unit files, run `systemctl enable --now`

---

### Phase 10 — CI / CD

- [x] Write `.github/workflows/ci.yml`: checkout, setup-uv, `uv sync --all-extras`, ruff check, ruff format check, mypy, bandit, pytest (with Postgres service container), pip-audit
- [x] Write `.github/workflows/security.yml`: scheduled (weekly) gitleaks scan across full git history
- [x] Verify CI pipeline passes on a clean clone
- [x] Add branch protection rules: `main` and `develop` require CI pass + 1 review before merge

---

### Phase 11 — Documentation

- [x] Write root `README.md`: project overview, prerequisites, quick-start (`docker compose up`), architecture diagram, link to each app README
- [x] Write `apps/form/README.md`: purpose, config options, form field reference
- [x] Write `apps/panel/README.md`: SSO setup, user roles, dispatcher session usage
- [x] Write `apps/router/README.md`: channel adapters, `signal_mode` values, ACK mechanisms
- [x] Write `apps/tts/README.md`: Piper model selection, endpoint reference
- [x] Write `apps/jambonz/README.md`: Jambonz API prerequisites, escalation config
- [x] Write root `CLAUDE.md`: project conventions, key file paths, dev commands, test commands, deploy commands
- [x] Write per-app `CLAUDE.md` files (app-specific conventions and entry points)

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
---

### Phase 15 — End-to-End Tests (Playwright + API + Schemathesis) ✅

#### E2E test project
- [x] Create `tests/e2e/` as a standalone `uv` project with `pyproject.toml` (schemathesis 4.x, pytest-playwright, pytest-asyncio, httpx, hypothesis, ruff, mypy)
- [x] `conftest.py` — session-scoped `form_base_url` / `panel_base_url` fixtures that auto-skip when env vars absent; `form_client` / `panel_client` httpx fixtures
- [x] `helpers.py` — `make_valid_payload(**overrides)` helper (importable without conftest path issues)
- [x] `test_form_schema.py` — schemathesis `openapi.from_url` schema-driven conformance tests (`max_examples=200`, suppress health checks)
- [x] `test_form_api.py` — targeted httpx tests: honeypot silent-drop, idempotency token reuse, rate-limit burst (429)
- [x] `test_form_ui.py` — Playwright/Chromium tests: page loads, valid form submit → success message, XSS in textarea not executed, keyboard navigation smoke test
- [x] `test_panel_api.py` — panel tests: unauthenticated → OIDC redirect (302/307), dispatcher token 401

#### E2E test stack isolation
- [x] `infra/postgres/e2e/00_roles.sql` — trust-auth init SQL (no psql variable syntax; `POSTGRES_HOST_AUTH_METHOD=trust` handles auth)
- [x] `infra/docker-compose.e2e.yml` — Compose override: `POSTGRES_HOST_AUTH_METHOD=trust`, fresh `pg_e2e_data` volume, port bindings (8000, 8001), `mock-oidc` profile cleared
- [x] `scripts/run_e2e.sh` — lifecycle wrapper: bring stack up, wait for postgres healthy, run pytest, tear down with `-v`

---

### Phase 16 — Bad Strings Manual Script ✅

- [x] `scripts/bad_strings_test.py` — PEP 723 inline script (`# /// script` header); runnable via `uv run` with no separate venv
- [x] Uses `blns` PyPI package (`blns.all()`) for ~500 naughty strings; no runtime network call needed
- [x] Stratified sampling: one string per named category first, then random fill; `--sample N` (default 50), `--seed SEED` / `--all` (mutually exclusive via `argparse`)
- [x] asyncio semaphore (`--concurrency 3` default) — N strings in flight; each posts all three target fields in one request
- [x] Rich progress: overall `BarColumn` + `MofNCompleteColumn` + `TimeElapsedColumn`; per-slot `SpinnerColumn` + truncated string preview; `--silent` suppresses all rich output
- [x] JSON output (`--output bad_strings_results.json`): url, sample_size, seed, per-string results, summary counts
- [x] Exit code 0 if no 5xx; exit code 1 otherwise

---

### Phase 17 — Swagger UI Docker Image ✅

- [x] `infra/swagger/pyproject.toml` — deps: fastapi, httpx, uvicorn\[standard\] (no `[build-system]`; app.py is a script not a package)
- [x] `infra/swagger/app.py` — FastAPI aggregator: startup retry-fetch of each service's `/openapi.json`; `GET /` index page; `GET /all` multi-API swagger-ui; `GET /{path}` per-audience pages; `GET /api/specs/{service}` cached spec proxy
- [x] `infra/swagger/Dockerfile` — downloads swagger-ui dist tarball at build time (pinned version, no CDN at runtime); serves JS/CSS via `StaticFiles`
- [x] Add `swagger` service to `infra/docker-compose.yml`: port 8080, `profiles: [local, swagger]`, `http://` internal URLs

---

### Phase 18 — Accented Characters / Diacritical Marks ✅

- [x] Add accented character tests to `apps/form/tests/test_routes.py`: `test_accented_name_accepted` (Héloïse Müller, sie/ihr, São Paulo crew), `test_accented_text_fields_accepted` (naïve résumé, café, crêperie, façade, José, Ångström), `test_accented_location_text_accepted` (Près du château — zone forêt)
- [x] Verify `channels/email.py` creates `MIMEText(..., 'plain', 'utf-8')` — confirmed UTF-8 charset set correctly
- [x] Verify form HTML template has `<meta charset="UTF-8">` in `<head>` — confirmed present
- [x] Add TTS pronunciation note to `apps/tts/README.md`: `en_GB-alan-medium` is English-only; accented chars passed through but pronunciation may vary

---

### Phase 19 — OWASP ZAP Integration ✅

- [x] `infra/zap/form-scan.yaml` — ZAP Automation Framework: spider, ajaxSpider, activeScan with inline `policyDefinition` (buffer overflow / format string / Node.js rules disabled; SQLi, XSS, SSRF, path traversal enabled at Medium strength), dual HTML + JSON report jobs
- [x] `infra/zap/panel-scan.yaml` — same structure for panel; cookie-based auth placeholder (`${SESSION_COOKIE}`)
- [x] `infra/zap/scan-policy-api.policy` — XML policy file for ZAP GUI import (same rules as inline policyDefinition; documents disabled/enabled scanners)
- [x] `reports/zap/.gitkeep` — placeholder keeping the gitignored report output directory tracked
- [x] `scripts/run_zap.py` — PEP 723 script: `docker info` daemon check, `docker compose run --rm zap`, parse `form-report.json` for risk-level counts, rich summary table, interactive prompt to open HTML report (macOS: `open`, Linux: `xdg-open`), exit 1 on HIGH alerts
- [x] Add `zap` service to `infra/docker-compose.yml`: `ghcr.io/zaproxy/zaproxy:stable`, `profiles: [zap]`, volumes `./zap:/zap/wrk:ro` and `../reports/zap:/zap/reports`
- [x] Add `zap` job to `.github/workflows/security.yml`: `workflow_dispatch` only (never scheduled), starts stack, waits for `/health`, runs `uv run scripts/run_zap.py`, uploads report artifact
- [x] Add `reports/zap/*.html` and `reports/zap/*.json` to `.gitignore`

---

### Phase P — Panel & Dispatcher: iteration and UX hardening ✅

Post-launch iteration based on real use. All items below are shipped.

**Status chips**
- [x] Add `STATUS_EMOJI` map (`🆕👤🔄⚠️🤔✅`) to `routes.py`; pass to case list and detail templates
- [x] Add `.chip--<status>` CSS variants with coloured backgrounds (new=dark blue, assigned=orange, in_progress=teal, action_needed=red, decision_needed=purple, closed=grey)
- [x] Apply coloured chips with emoji in case list table and filter bar status checkboxes
- [x] Case detail: status chip is a `<details>`/`<summary>` dropdown — clicking it reveals valid transition buttons inline in the meta bar; removed separate "Transition status" block from Actions section
- [x] Transition buttons styled with the target status colour for quick visual scanning

**Map embeds**
- [x] Add `?readonly=true` param to emf-map embed mode — suppresses click-to-pin handler so pin stays fixed while pan/zoom still works
- [x] Embed read-only map in case detail (`case_detail.html`) below the meta bar; 300px tall with hint "Zoom in/out to find the pin — it won't move."
- [x] Update map `frame-ancestors` CSP in `Caddyfile.local` to allow `panel.emf-forms.internal` in addition to `report.emf-forms.internal`
- [x] Dispatcher: location column shows 📍 link when coordinates available; per-row collapsible "🗺 Show map" `<details>` row with embedded read-only map
- [x] emf-map repo: `?marker=lat,lon` query param pre-sets pin on load; `emf-view` postMessage on moveend/load; `map.on('load', resize)` for WebGL green-box fix

**ACK / notification fixes**
- [x] Fix `InsufficientPrivilegeError` on ACK button: `team_member` was missing `UPDATE` on `forms.notifications`; added to `00_roles.sql` and applied to live DB
- [x] ACK on case detail now works for both admin and dispatcher routes (both use `team_member` DB user)

**Case list improvements**
- [x] Collapsible filter bar using `<details>`/`<summary>` — auto-opens when filters are active; each filter group on its own line; no boxes around fieldsets
- [x] NACK/ACK notification badge column (purple NACK / green ACK) from `notifications` table aggregate
- [x] ACK and Trigger Call buttons per row in case list
- [x] ACK action also assigns the case to the current user (Redis `SADD` for autocomplete)
- [x] Sortable columns with ↑/↓ arrows; sort state preserved across filter changes
- [x] Redis-backed assignee autocomplete: `GET /api/assignees` populates datalist; name added on ACK or manual assign

**Form: pronouns UX**
- [x] Replace datalist with `<select>` + hidden text input; text input only shown when "Other / prefer to self-describe" chosen; hidden again if user switches back

**Infrastructure fixes**
- [x] `SiteMap.zoom: int → float` in shared config (fractional zoom values like `15.63` broke pydantic)
- [x] Router listener: strip `postgresql+asyncpg://` prefix before passing DSN to asyncpg; replace non-existent `wait_closed()` with `is_closed()` poll loop
- [x] `ADMIN_DB_PASSWORD` default `:-localdev` added to silence docker-compose warning
- [x] `REDIS_URL` added to panel service environment in `docker-compose.yml`

**E2E tests**
- [x] Location pin coordinates in `test_form_fields_stored.py` updated to EMF site boundary (~52.041, -2.377) — previous coords (51.90, -2.58) were miles off-site

---

### Phase Q — Admin Panel: urgency/priority editing ✅

Allow team members to change the urgency (priority) of a case after submission, with every change recorded in `case_history` for the audit trail.

- [x] Add `PATCH /api/cases/{case_id}/urgency` endpoint in `routes.py` (session auth via `require_conduct_team`; validate against `config.urgency_levels`; write `case_history` row with `field="urgency"`, `old_value`, `new_value`, `changed_by`)
- [x] Add urgency selector to `case_detail.html` Actions section — a `<select>` pre-filled with the current urgency, styled with the existing badge colours; submit via JS PATCH (same pattern as assignee/tags forms)
- [x] Update `panel.js` `initPatchForm` or add a dedicated `initUrgencyForm` handler that sends `{"urgency": value}` and reloads on success
- [x] Grant `UPDATE (urgency, updated_at)` on `forms.cases` to `team_member` in `infra/postgres/00_roles.sql` (currently only `status` and `assignee` are updatable by `team_member`)
- [x] Apply the live DB grant: `GRANT UPDATE (urgency, updated_at) ON forms.cases TO team_member;`
- [x] Update `cases.html` urgency badge to reflect live changes after reload
- [x] Test: change urgency → `case_history` row exists with correct `old_value`/`new_value`; badge updates in list view

---

### Phase R — Notification system: ACK wiring, cross-channel propagation, & housekeeping ✅

Three inter-related gaps fixed together because they share the same data path:
(a) email ACK magic links never appear in emails; (b) ACK on any one channel does not update the others; (c) the `notification_state_total` Prometheus counter is defined but never incremented; (d) `_signal_phone_available()` is hardcoded `False`.

#### R.1 — Wire email ACK token into `_send_with_retry`

`create_ack_token` exists in `router/ack/tokens.py`; it is never called.  `EmailAdapter.send()` already accepts an `ack_token: str | None` kwarg but `_send_with_retry` never passes one.

- [x] In `alert_router.py` `_send_with_retry`: before the first send attempt, when `channel_name == "email"`, create a notification record first (to get its `id`), call `create_ack_token(notification.id, settings.secret_key)`, then pass the token to `adapter.send(alert, ack_token=token)` for that attempt
- [x] If the adapter is not `EmailAdapter`, `ack_token` is not passed (it is ignored by other adapters anyway; no API change needed)
- [x] Verify the emitted email body contains the `Acknowledge:` line with the correct URL
- [x] Test: `EmailAdapter.send()` with a non-None `ack_token` includes `{ack_base_url}/ack/{token}` in the body; without token the line is absent

#### R.2 — Cross-channel ACK confirmation after any ACK

Current behaviour: when `mark_acked` is called, the code at the call site sends a confirmation only on the channel that just acked.  Other channels never know.

**Design**: `mark_acked` is extended to also return the list of all other `SENT` notifications for the same case.  A new `send_ack_to_all_channels` coroutine then sends an (idempotent) ACK confirmation on every adapter that has a SENT notification.

- [x] Extend `alert_router.mark_acked(notification_id, acked_by, session) -> tuple[CaseAlert | None, list[Notification]]`:
  - After marking the notification ACKED, query `forms.notifications WHERE case_id = ? AND state = 'sent' AND id != notification_id`
  - Return `(alert, other_sent_notifications)` — callers must unpack
- [x] Add `alert_router.send_ack_to_all_channels(alert, acked_by, other_notifications, session)`:
  - Maps each notification's `channel` field to the corresponding adapter
  - Calls `adapter.send_ack_confirmation(alert, acked_by, notification.message_id)` for each
  - Wraps each call in `asyncio.create_task` so a slow channel doesn't block others
  - All current `send_ack_confirmation` signatures already accept `acked_by` via the base ABC; verify and fix any that don't
- [x] Update all ACK call sites to use the new return signature and call `send_ack_to_all_channels`:
  - `POST /webhook/signal` handler in `main.py`
  - `GET /ack/{token}` handler in `main.py`
- [x] Test: mock three adapters (email, signal, mattermost); ACK via signal webhook → email and mattermost adapters each receive `send_ack_confirmation` call with correct `acked_by` and `message_id`

#### R.3 — Panel/dispatcher ACK triggers cross-channel notifications

Panel ACK currently updates `forms.notifications` directly via DB and never calls the router.  Add a router internal endpoint and have the panel call it.

Security: use a pre-shared secret.  Add `ROUTER_INTERNAL_SECRET` to router `Settings` and `.env-example`; the router checks the `X-Internal-Secret` request header on all `/internal/*` routes and returns 403 if it is absent or wrong.  Panel and Jambonz settings each also hold `ROUTER_INTERNAL_SECRET` and include it as `X-Internal-Secret` in every call to the internal endpoint.  This is sufficient given Docker-network isolation — no need for full OAuth on an endpoint that is never exposed via Caddy.

- [x] Add `ROUTER_INTERNAL_SECRET` to `apps/router/src/router/settings.py`, `apps/panel/src/emf_panel/settings.py`, and `apps/jambonz/src/settings.py`; add to `.env-example` with a generation comment
- [x] Add `POST /internal/ack/{case_id}` to `apps/router/src/router/main.py`:
  - Check `X-Internal-Secret` header; return 403 if missing or wrong
  - Accepts JSON body `{"acked_by": "<username>"}`, optional `{"notification_id": "<uuid>"}` (if present, marks that specific notification; otherwise finds the first SENT notification for the case)
  - Marks notification(s) ACKED, calls `send_ack_to_all_channels`
  - Returns `{"ok": True, "acked_count": N}`
  - Do not expose via Caddy
- [x] In `apps/panel/src/emf_panel/routes.py`, after updating notifications in `POST /api/cases/{case_id}/ack`:
  - Call `httpx.AsyncClient().post(settings.router_internal_url + "/internal/ack/" + case_id, json={"acked_by": username}, headers={"X-Internal-Secret": settings.router_internal_secret})` (fire-and-forget with a short timeout; log failure but do not propagate to client)
  - Add `ROUTER_INTERNAL_URL` to `apps/panel/src/emf_panel/settings.py` (default `http://msg-router:8002`)
- [x] Do the same in `POST /api/dispatcher/ack/{case_id}`, passing `"dispatcher"` as `acked_by` (dispatcher sessions have no OIDC username)
- [x] Test: panel ACK → router internal endpoint called → all SENT notifications get `send_ack_confirmation` calls
- [x] Test: missing or wrong `X-Internal-Secret` → 403

#### R.4 — Fix `_signal_phone_available()` to reflect Jambonz state

Currently `_signal_phone_available()` is hardcoded `return False`, making `signal_mode="fallback_only"` behave identically to `"always"`.

- [x] In `alert_router.py`, replace the stub with:
  ```python
  async def _signal_phone_available(self) -> bool:
      if self._phone is None:
          return False
      return await self._phone.is_available()
  ```
- [x] `_phone` is the Jambonz adapter (already passed in via constructor); `JambonzAdapter.is_available()` already hits the Jambonz Accounts endpoint — this just wires the two together
- [x] Test: `signal_mode="fallback_only"` + phone available → Signal not sent; phone unavailable → Signal sent

#### R.5 — Increment `notification_state_total` counter

The Prometheus counter `emf_notification_state_total` is defined in `main.py` but never incremented anywhere.

- [x] In `alert_router._send_with_retry`, after each state transition (`SENT`, `FAILED`, `RETRYING`), call `notification_state_total.labels(channel=channel_name, state=new_state).inc()`
- [x] In `mark_acked`, call `notification_state_total.labels(channel=notification.channel, state="acked").inc()`
- [x] Expose the counter instance via a module-level singleton (or pass into `AlertRouter.__init__`) so it can be referenced from both `main.py` and `alert_router.py` without a circular import
- [x] Test: verify counter increments in unit tests by patching the counter object

#### R.6 — Tests

- [x] `test_mark_acked_returns_other_sent_notifications` — set up two SENT notifications for same case; call `mark_acked`; assert the returned list contains the other notification
- [x] `test_send_ack_to_all_channels_calls_each_adapter` — three adapters, two with SENT notifications; assert both `send_ack_confirmation` mocks called; adapter for non-SENT not called
- [x] `test_email_ack_link_in_body` — `EmailAdapter.send(alert, ack_token="test_token")` → body contains `test_token`
- [x] `test_signal_phone_available_delegates_to_phone_adapter` — phone adapter mock returns True/False; assert `_signal_phone_available` matches
- [x] `test_notification_state_counter_incremented` — `_send_with_retry` success → counter `sent` incremented; all-fail → counter `failed` incremented

---

### Phase S — Mattermost: Posts API upgrade ✅

Replace the incoming webhook (no message ID, no threading, no in-place update) with the Mattermost Posts API.  This is the primary improvement for the Mattermost channel.

#### S.0 — Local Mattermost for development

- [x] Add `mattermost/mattermost-team-edition` service to `docker-compose.yml` under the `local` profile (reference: https://docs.mattermost.com/deployment-guide/server/containers/install-docker.html); expose port 8065; mount a named volume for data persistence
- [x] Add `MATTERMOST_URL`, `MATTERMOST_CHANNEL_ID`, and `MATTERMOST_TOKEN` to `.env-example` with comments explaining how to obtain a bot token and channel ID from the local instance
- [x] Document the Mattermost local setup in `apps/router/README.md` (extend the existing channel-adapter section): how to start the local container, create a bot account, obtain a personal access token with `create_post` permission, find the channel ID, and configure `.env`

#### S.1 — Config changes

- [x] Add to `AppConfig` in `shared/src/emf_shared/config.py`:
  ```python
  mattermost_url: str | None = None          # e.g. "https://mattermost.emfcamp.org"
  mattermost_channel_id: str | None = None   # target channel ID (not display name)
  ```
- [x] Add to router `Settings` in `apps/router/src/router/settings.py`:
  ```
  MATTERMOST_TOKEN=...   # personal access token or bot account token with create_post permission
  ```
- [x] Keep `mattermost_webhook` in `AppConfig` as a fallback: if `mattermost_url` + `mattermost_channel_id` + `MATTERMOST_TOKEN` are all set, use Posts API; otherwise fall back to webhook (for backward compat during transition)
- [x] Update `config.json-example` and `.env-example` with new fields and comments

#### S.2 — MattermostAdapter rewrite

File: `apps/router/src/router/channels/mattermost.py`

- [x] Constructor accepts both: `webhook_url` (legacy) and `api_url`, `channel_id`, `token` (Posts API)
- [x] `send(alert) -> str | None` — when Posts API config present:
  - `POST {mattermost_url}/api/v4/posts` with `Authorization: Bearer {token}`
  - Body (Slack-compatible attachment with coloured border and interactive button):
    ```json
    {
      "channel_id": "CHANNEL_ID",
      "message": "{emoji} New {urgency} case: {friendly_id}",
      "props": {
        "attachments": [{
          "color": "{urgency_colour}",
          "title": "{emoji} New {urgency} case: {friendly_id}",
          "title_link": "{case_url}",
          "fields": [
            {"title": "Event", "value": "{event_name}", "short": true},
            {"title": "Location", "value": "{location_text}", "short": true}
          ],
          "actions": [{
            "name": "Acknowledge",
            "type": "button",
            "integration": {
              "url": "{router_base_url}/webhook/mattermost/action",
              "context": {"action": "ack", "notification_id": "{notification_uuid}"}
            }
          }]
        }]
      },
      "metadata": {"priority": {"priority": "urgent", "requested_ack": true}}
    }
    ```
  - On 201 response: extract `response["id"]` as `message_id` — this is the post ID used for threading and in-place updates
  - Fall back to webhook path if API call fails
- [x] `send_ack_confirmation(alert, acked_by, message_id)` — when Posts API:
  - `PUT {mattermost_url}/api/v4/posts/{message_id}` with updated body:
    - Remove `actions` from the attachment (button disappears)
    - Change `color` to `#2e7d32` (green)
    - Add `{"title": "Acknowledged by", "value": "@{acked_by}", "short": true}` field
    - Returns without error if post not found (already deleted/edited)
- [x] `is_available()` — when Posts API: `GET {mattermost_url}/api/v4/system/ping` (no auth needed); fall back to `bool(webhook_url)`
- [x] Urgency colour map (matches existing CSS variables):
  - `urgent` → `#c62828`, `high` → `#e65100`, `medium` → `#1565c0`, `low` → `#558b2f`

#### S.3 — Mattermost button action endpoint

- [x] Add `POST /webhook/mattermost/action` to `apps/router/src/router/main.py`:
  - Receives Mattermost button-click JSON: `{"user_name": "...", "context": {"action": "ack", "notification_id": "..."}}`
  - Validate `context.action == "ack"` and `notification_id` is a valid UUID
  - Call `mark_acked(notification_id, user_name, session)`
  - Call `send_ack_to_all_channels(alert, user_name, other_notifications, session)`
  - Return `{"update": {"message": "Acknowledged"}}` with HTTP 200 (Mattermost uses this to show ephemeral confirmation)
- [x] Add a simple shared-secret check: include `MATTERMOST_WEBHOOK_SECRET` in `context` when building the action; verify it in the handler.  This prevents arbitrary external callers from triggering ACKs.

#### S.4 — `message_id` format for cross-channel update

For the Posts API path, `notifications.message_id` stores the Mattermost post ID (a 26-char alphanumeric string).  When ACK arrives from another channel and `send_ack_confirmation` is called with this `message_id`, it does the `PUT` update.

- [x] Document this in `notifs-test.md` table (update column meaning for `message_id`)

#### S.5 — Tests

- [x] `test_mattermost_posts_api_send` — mock `httpx`, verify `POST /api/v4/posts` called with correct attachment structure; assert returned value is the post `id` from the response
- [x] `test_mattermost_ack_updates_post` — mock `httpx`, call `send_ack_confirmation(alert, "adam", "post_id")` → verify `PUT /api/v4/posts/post_id` called with green colour and `acked_by` field
- [x] `test_mattermost_webhook_action_endpoint` — POST to `/webhook/mattermost/action` with valid payload → `mark_acked` called, `send_ack_to_all_channels` called; response is `{"update": {"message": "Acknowledged"}}`
- [x] `test_mattermost_falls_back_to_webhook` — Posts API creds absent → falls back to webhook path, returns `"mattermost"` as message_id

---

### Phase T — Jambonz: AlertRouter integration + DTMF ACK fix ✅

Currently Jambonz calls are only initiated by the panel's "Call" button.  The goal: calls fire automatically on new cases (like Signal), controlled by config.  The DTMF ACK webhook exists but doesn't write to the DB.

#### T.1 — Add Jambonz config flags

- [x] Add to per-event `EventConfig` in `shared/src/emf_shared/config.py`:
  ```python
  jambonz_mode: str = "disabled"
  # "disabled"                  — never auto-call (current behaviour)
  # "always"                    — call on every new event-time case
  # "high_priority_only"        — call only for urgency high/urgent
  ```
- [x] Add required Jambonz env vars to `apps/router/src/router/settings.py` (they already exist in `apps/jambonz/settings.py`; add to router settings as well):
  ```
  JAMBONZ_API_URL, JAMBONZ_API_KEY, JAMBONZ_ACCOUNT_SID,
  JAMBONZ_APPLICATION_SID, JAMBONZ_FROM_NUMBER, TTS_SERVICE_URL
  ```
  All optional (default `None`); router creates `JambonzAdapter` only when all are set.
- [x] Update `.env-example` with Jambonz vars and comments
- [x] Document Jambonz configuration in `apps/jambonz/README.md` (extend the existing file): self-hosted vs cloud, required env vars, how the DTMF webhook URL is wired, and a note that cloud Jambonz requires a public-facing URL (ngrok or Caddy) while self-hosted on the same Docker network does not

#### T.2 — Wire Jambonz into AlertRouter

- [x] `AlertRouter.__init__` already has `phone: ChannelAdapter | None`; this is where `JambonzAdapter` plugs in.  The init code in `main.py` creates adapters on startup — add Jambonz adapter creation when env vars are present.
- [x] Add `_jambonz_mode` (per-event string) alongside existing `_signal_mode` in `AlertRouter`.  Per-event lookup via `_event_config(event_name)`.
- [x] In `_route_event_time`, after Signal logic, add Jambonz routing:
  ```python
  if self._phone and await self._phone.is_available():
      if ev.jambonz_mode == "always" or (
          ev.jambonz_mode == "high_priority_only" and alert.urgency in ("high", "urgent")
      ):
          asyncio.create_task(self._send_with_retry(alert, "telephony", self._phone, session))
  ```
- [x] The escalation logic (`apps/jambonz/src/escalation.py`) is not yet integrated with `_send_with_retry`; for now, `JambonzAdapter.send()` places a single call to `call_group`.  Escalation will be wired in a follow-up; note this as a TODO.

#### T.3 — Fix DTMF ACK webhook to write to DB and propagate

File: `apps/jambonz/src/main.py` (the DTMF webhook handler).

- [x] DTMF webhook handler (`POST /webhook/jambonz`): on digit `"1"` (ACK):
  - Look up the `Notification` row where `channel = 'telephony'` and `case_id = body.case_id` and `state = 'sent'`
  - Call the router internal ACK endpoint `POST http://msg-router:8002/internal/ack/{case_id}` with `{"acked_by": "jambonz_dtmf", "notification_id": str(notification.id)}`
  - This causes the router to mark all notifications acked and send cross-channel confirmations
  - Jambonz adapter only needs `ROUTER_INTERNAL_URL` env var (default `http://msg-router:8002`)
- [x] On digit `"2"` (pass/escalate): log and mark as escalated; for now this is a no-op in terms of notification state (escalation sequence remains in `escalation.py`)
- [x] Add `ROUTER_INTERNAL_URL` to `apps/jambonz/src/main.py` settings
- [x] Test: POST to `/webhook/jambonz` with `{"digit": "1", "case_id": "<uuid>"}` → router internal ACK endpoint called; digit `"2"` → endpoint not called

#### T.4 — Tests

- [x] `test_jambonz_auto_call_always_mode` — `jambonz_mode="always"`, Jambonz available → telephony `_send_with_retry` task spawned for any urgency
- [x] `test_jambonz_auto_call_high_priority_only` — `jambonz_mode="high_priority_only"` → spawned for high/urgent; not spawned for medium/low
- [x] `test_jambonz_disabled` — `jambonz_mode="disabled"` → no telephony task spawned regardless of urgency or availability
- [x] `test_dtmf_digit_1_calls_router_ack` — POST to DTMF webhook with digit `"1"` → router internal endpoint called with correct body
- [x] `test_dtmf_digit_2_does_not_ack` — digit `"2"` → router ACK not called

---

### Phase U — Signal: message improvements ✅

Improve the Signal alert message and ACK confirmation to be more useful in the field.

#### U.1 — Map link in Signal alerts

- [x] In `SignalAdapter.send()`, when `alert.location` contains `lat` and `lon`, append a link to the EMF map using the **public** `map.emfcamp.org` URL.  In production, Signal recipients are on their phones and need a public URL.  In local testing with BlueStacks (Android emulator on the dev machine), `map.emf-forms.internal` is also reachable — but always use `map.emfcamp.org` in the message so it works in both contexts without configuration:
  `https://map.emfcamp.org/#15/{lat}/{lon}` (standard map hash format; opens at the right coords)
- [x] If only `location_hint` text (no coords), include the text only — no map link
- [x] Signal markdown: map link as plain URL (Signal renders URLs as tappable links; no special markup needed)
- [x] Updated message format:
  ```
  {emoji} *New {urgency} case: {friendly_id}*
  Location: {location_text}
  Map: https://map.emfcamp.org/#15/{lat}/{lon}
  Case: {case_url}

  React 🤙 to acknowledge
  ```

#### U.2 — Signal `send_ack_confirmation` when ACK from another channel

Currently `send_ack_confirmation` sends a new message quoting the original.  When ACK comes from another channel (e.g. panel), the Signal message should also update.  Because Signal doesn't support in-place message editing via signal-cli-rest-api, the current approach (new quoted message) is correct; just ensure it includes who acked and from where.

- [x] Update `SignalAdapter.send_ack_confirmation(alert, acked_by, message_id)` to include `acked_by` in the confirmation:
  `"✅ Case {friendly_id} acknowledged by {acked_by}"`
- [x] `send_ack_confirmation` already accepts `acked_by` in the base interface; verify the Signal implementation passes it through (currently the method signature exists but the value may not be used in the message body)

#### U.3 — `also_sent_via` field on `CaseAlert`

- [x] Add `also_sent_via: list[str] = field(default_factory=list)` to the `CaseAlert` dataclass in `shared/src/emf_shared/models.py` (or wherever the dataclass lives)
- [x] In `AlertRouter._route_event_time`, after determining which adapters will be used, build the list and assign it: each channel name goes into the list except for the channel being sent to (e.g. when building the Signal message, `also_sent_via = ["email", "mattermost"]`)
- [x] Signal: `SignalAdapter.send()` appends `"Also sent via: {', '.join(alert.also_sent_via)}"` if the list is non-empty (moved to Phase V for all text channels)

#### U.4 — Tests

- [x] `test_signal_message_includes_map_link` — alert with lat/lon → Signal message body contains the public `map.emfcamp.org` URL (Signal messages always use the public map regardless of local dev environment)
- [x] `test_signal_message_no_map_link_when_no_coords` — alert with text-only location → no map URL in body
- [x] `test_signal_ack_confirmation_includes_acked_by` — `send_ack_confirmation(alert, "alice", msg_id)` → confirmation message contains `"alice"`
- [x] `test_signal_also_sent_via` — `alert.also_sent_via = ["email", "mattermost"]` → Signal message contains `"Also sent via: email, mattermost"`

---

### Phase V — "Also sent via" on all text channels ✅

The `also_sent_via` field (Phase U.3) should appear in every text-based notification channel so that any responder — regardless of which channel they read — can immediately see what other channels were notified.  Voice (Jambonz) is excluded: the TTS script is kept short and this information is not useful during a call.

#### V.1 — Signal

Already covered by U.3/U.4: append `"Also sent via: …"` when `also_sent_via` is non-empty.

#### V.2 — Email

- [x] In `EmailAdapter.send()`, when `alert.also_sent_via` is non-empty, append a line to the email body (plain-text and HTML):
  `Also sent via: {', '.join(alert.also_sent_via)}`
- [x] Place it after the case URL and before the ACK link (if present), so it is visible without scrolling
- [x] Test: `EmailAdapter.send(alert_with_also_sent_via)` → email body contains `"Also sent via: signal, mattermost"`

#### V.3 — Mattermost

- [x] In `MattermostAdapter.send()`, when `alert.also_sent_via` is non-empty, add an attachment field:
  `{"title": "Also sent via", "value": "{', '.join(alert.also_sent_via)}", "short": true}`
- [x] Test: `MattermostAdapter.send(alert_with_also_sent_via)` → Posts API body contains the `"Also sent via"` field in the attachment

#### V.4 — AlertRouter wiring

- [x] Confirm `_route_event_time` populates `also_sent_via` correctly for each adapter: each adapter receives the list of the *other* channels, not its own name
- [x] Integration test: alert routed to signal + email + mattermost simultaneously → each channel's message contains the other two names

---

### TODOs / Follow-up

- [ ] Make postgres SSL key/cert files readable by the postgres user — currently `infra/postgres/certs/` files are owned by root so `ssl=on` in postgresql.conf fails; fix ownership in `install.py` or add a `postgres-init` entrypoint script that chowns the certs before postgres starts
- [ ] Swagger: fix `/dispatch` and `/all` pages on `localhost:8080` — filter missing services from URL list so Swagger UI doesn't error when `jambonz` is not running; see implementation notes in (now-deleted) TODO file
- [ ] Swagger: rename `_PATHS["sysadmin"]` → `"text-to-speech"` in `infra/swagger/app.py`
- [ ] Docker install: check port availability before assigning — `find_free_port(preferred, lo=8100, hi=9000)` in `scripts/install.py`; write chosen ports to `.env` as `FORM_PORT`, `PANEL_PORT`, etc.
- [ ] ClamAV: integrate scan in attachment upload pipeline (clamd socket); currently ClamAV container exists but scanning is not wired up
- [ ] Attachment tests: MIME rejection, ClamAV positive → 400, max 3 files, max 10 MB, unauthenticated → 403
- [ ] Phase C: `CURRENT_EVENT_OVERRIDE` missing from `.env-example` — add it
- [x] e2e test coordinates: all three location pin test cases now use EMF site coords (~52.041, -2.377) — done in Phase P

---

## 16. Master Outstanding TODO

Compact checklist of all remaining work, in implementation order.  Phases Q–V and the miscellaneous backlog.  Check items off here and in the detailed phase section above when complete.

---

### Phase Q — Urgency editing ✅

- [x] `PATCH /api/cases/{case_id}/urgency` endpoint (`routes.py`) — auth via `require_conduct_team`; validate against `config.urgency_levels`; write `case_history` row
- [x] Urgency `<select>` in `case_detail.html` Actions section — pre-filled; JS PATCH on change (reuse or extend `initPatchForm`)
- [x] Grant `UPDATE (urgency, updated_at)` on `forms.cases` to `team_member` in `00_roles.sql`
- [x] Apply live DB grant
- [x] Urgency badge in `cases.html` reflects change after reload
- [x] Test: change urgency → `case_history` row with correct `old_value`/`new_value`; badge updates

---

### Phase R — Notification ACK wiring & cross-channel propagation ✅

#### R.1 — Email ACK token
- [x] In `_send_with_retry` (email channel): create notification record first to get `id`; call `create_ack_token(notification.id, settings.secret_key)`; pass token to `adapter.send(alert, ack_token=token)`
- [x] Test: `EmailAdapter.send()` with non-None token → body contains `{ack_base_url}/ack/{token}`; without token → line absent

#### R.2 — Cross-channel ACK confirmation
- [x] `mark_acked` → returns `tuple[CaseAlert | None, list[Notification]]` (other SENT notifications for same case)
- [x] New `send_ack_to_all_channels(alert, acked_by, other_notifications, session)` — maps each notification's `channel` to its adapter; calls `send_ack_confirmation` per adapter; wraps each in `asyncio.create_task`
- [x] Verify all `send_ack_confirmation` method signatures accept `acked_by`; fix any that don't
- [x] Update `POST /webhook/signal` handler to use new `mark_acked` return signature and call `send_ack_to_all_channels`
- [x] Update `GET /ack/{token}` handler similarly
- [x] Test: ACK via signal → email and mattermost adapters each receive `send_ack_confirmation` with correct `acked_by` and `message_id`

#### R.3 — Panel/dispatcher triggers router
- [x] Add `ROUTER_INTERNAL_SECRET` to `apps/router/src/router/settings.py`, `apps/panel/src/emf_panel/settings.py`, `apps/jambonz/src/settings.py`, and `.env-example`
- [x] `POST /internal/ack/{case_id}` in router `main.py`: check `X-Internal-Secret` header (403 if wrong); accept `{"acked_by", "notification_id?"}`; mark ACKED; call `send_ack_to_all_channels`; return `{"ok": true, "acked_count": N}`; do not expose via Caddy
- [x] After DB update in `POST /api/cases/{case_id}/ack` (panel): fire-and-forget POST to `{ROUTER_INTERNAL_URL}/internal/ack/{case_id}` with `acked_by=username` and `X-Internal-Secret` header; add `ROUTER_INTERNAL_URL` to panel settings (default `http://msg-router:8002`)
- [x] Same in `POST /api/dispatcher/ack/{case_id}`, passing `"dispatcher"` as `acked_by`
- [x] Test: panel ACK → router internal endpoint called → all SENT notifications get `send_ack_confirmation`
- [x] Test: missing or wrong `X-Internal-Secret` → 403

#### R.4 — Fix `_signal_phone_available()`
- [x] Replace `return False` stub with `return await self._phone.is_available()` (guard for `self._phone is None`)
- [x] Test: `signal_mode="fallback_only"` + phone available → Signal not sent; phone unavailable → Signal sent

#### R.5 — Prometheus counter
- [x] In `_send_with_retry`: call `notification_state_total.labels(channel=…, state=…).inc()` after each state transition (`sent`, `failed`, `retrying`)
- [x] In `mark_acked`: call `notification_state_total.labels(channel=…, state="acked").inc()`
- [x] Expose counter via module-level singleton to avoid circular import between `main.py` and `alert_router.py`
- [x] Test: `_send_with_retry` success → `sent` counter incremented; all-fail → `failed` counter incremented

#### R.6 — Unit tests
- [x] `test_mark_acked_returns_other_sent_notifications`
- [x] `test_send_ack_to_all_channels_calls_each_adapter`
- [x] `test_email_ack_link_in_body`
- [x] `test_signal_phone_available_delegates_to_phone_adapter`
- [x] `test_notification_state_counter_incremented`

---

### Phase S — Mattermost Posts API ✅

#### S.0 — Local dev
- [x] Add `mattermost/mattermost-team-edition` to `docker-compose.yml` `local` profile; port 8065; named data volume
- [x] Add `MATTERMOST_URL`, `MATTERMOST_CHANNEL_ID`, `MATTERMOST_TOKEN` to `.env-example`
- [x] Extend `apps/router/README.md`: local container setup, bot account, personal access token (`create_post` permission), channel ID lookup

#### S.1 — Config
- [x] Add `mattermost_url: str | None` and `mattermost_channel_id: str | None` to `AppConfig` (`shared/src/emf_shared/config.py`)
- [x] Add `MATTERMOST_TOKEN` to router `Settings`
- [x] Keep `mattermost_webhook` fallback: use Posts API only when all three new fields are set
- [x] Update `config.json-example` and `.env-example`

#### S.2 — MattermostAdapter rewrite (`apps/router/src/router/channels/mattermost.py`)
- [x] Constructor accepts both `webhook_url` (legacy) and `api_url` + `channel_id` + `token`
- [x] `send()` — Posts API path: `POST /api/v4/posts` with coloured attachment (urgency colour map) and interactive Acknowledge button (`integration.url` + `context.notification_id`); extract `response["id"]` as `message_id`; fall back to webhook on failure
- [x] `send_ack_confirmation()` — `PUT /api/v4/posts/{message_id}`: remove `actions`, set green colour, add `"Acknowledged by"` field; no-op if post not found
- [x] `is_available()` — `GET /api/v4/system/ping`; fall back to `bool(webhook_url)`
- [x] Urgency colour map: `urgent=#c62828`, `high=#e65100`, `medium=#1565c0`, `low=#558b2f`

#### S.3 — Button action endpoint
- [x] `POST /webhook/mattermost/action` in router `main.py`: validate `context.action == "ack"` and `notification_id`; verify `MATTERMOST_WEBHOOK_SECRET` in context; call `mark_acked` + `send_ack_to_all_channels`; return `{"update": {"message": "Acknowledged"}}`

#### S.4 — Documentation
- [x] Update `message_id` column description in `notifs-test.md` research table to reflect Mattermost post ID (26-char alphanumeric)

#### S.5 — Tests
- [x] `test_mattermost_posts_api_send` — verify `POST /api/v4/posts` called; returned value is post `id`
- [x] `test_mattermost_ack_updates_post` — verify `PUT /api/v4/posts/{id}` called with green colour and `acked_by` field
- [x] `test_mattermost_webhook_action_endpoint` — valid payload → `mark_acked` called; response is `{"update": …}`
- [x] `test_mattermost_falls_back_to_webhook` — no Posts API creds → webhook path used

---

### Phase T — Jambonz auto-call & DTMF ACK ✅

#### T.1 — Config
- [x] Add `jambonz_mode: str = "disabled"` to per-event `EventConfig` (`"disabled"` / `"always"` / `"high_priority_only"`)
- [x] Add Jambonz env vars (`JAMBONZ_API_URL`, `JAMBONZ_API_KEY`, `JAMBONZ_ACCOUNT_SID`, `JAMBONZ_APPLICATION_SID`, `JAMBONZ_FROM_NUMBER`, `TTS_SERVICE_URL`) to router `Settings` (all optional; default `None`)
- [x] Update `.env-example` with Jambonz vars
- [x] Extend `apps/jambonz/README.md`: self-hosted vs cloud, env vars, DTMF webhook URL, ngrok requirement for cloud Jambonz

#### T.2 — Wire Jambonz into AlertRouter
- [x] In `main.py` startup: create `JambonzAdapter` when all Jambonz env vars are present; pass as `phone=` to `AlertRouter.__init__`
- [x] Add per-event `_jambonz_mode` lookup (alongside existing `_signal_mode`) in `AlertRouter`
- [x] In `_route_event_time`: after Signal logic, if `_phone` available and `jambonz_mode` matches urgency, spawn `_send_with_retry` task for `"telephony"` channel

#### T.3 — DTMF ACK webhook (`apps/jambonz/src/main.py`)
- [x] Digit `"1"`: look up telephony notification by `case_id`; POST to `{ROUTER_INTERNAL_URL}/internal/ack/{case_id}` with `{"acked_by": "jambonz_dtmf", "notification_id": str(notification.id)}` and `X-Internal-Secret` header
- [x] Digit `"2"`: log + mark escalated; no notification state change
- [x] Add `ROUTER_INTERNAL_URL` to jambonz settings (default `http://msg-router:8002`)

#### T.4 — Tests
- [x] `test_jambonz_auto_call_always_mode`
- [x] `test_jambonz_auto_call_high_priority_only`
- [x] `test_jambonz_disabled`
- [x] `test_dtmf_digit_1_calls_router_ack`
- [x] `test_dtmf_digit_2_does_not_ack`

---

### Phase U — Signal message improvements ✅

#### U.1 — Map link
- [x] `SignalAdapter.send()`: when `alert.location` has `lat`+`lon`, append `Map: https://map.emfcamp.org/#15/{lat}/{lon}` (plain URL; always use public `map.emfcamp.org`)
- [x] No map link when only `location_hint` text (no coords)

#### U.2 — `acked_by` in Signal ACK confirmation
- [x] `SignalAdapter.send_ack_confirmation(alert, acked_by, message_id)` → message body includes `"acknowledged by {acked_by}"`
- [x] Verify `acked_by` is used in the message, not just accepted in the signature

#### U.3 — `also_sent_via` field
- [x] Add `also_sent_via: list[str] = field(default_factory=list)` to `CaseAlert` dataclass
- [x] `_route_event_time`: build per-adapter `also_sent_via` list (all other channels, not self)
- [x] `SignalAdapter.send()`: append `"Also sent via: {…}"` when non-empty

#### U.4 — Tests
- [x] `test_signal_message_includes_map_link`
- [x] `test_signal_message_no_map_link_when_no_coords`
- [x] `test_signal_ack_confirmation_includes_acked_by`
- [x] `test_signal_also_sent_via`

---

### Phase V — "Also sent via" on all text channels ✅

#### V.2 — Email
- [x] `EmailAdapter.send()`: when `also_sent_via` non-empty, append `"Also sent via: …"` to both plain-text and HTML body (after case URL, before ACK link)
- [x] Test: email body contains `"Also sent via: signal, mattermost"`

#### V.3 — Mattermost
- [x] `MattermostAdapter.send()`: when `also_sent_via` non-empty, add `{"title": "Also sent via", "value": "…", "short": true}` attachment field
- [x] Test: Posts API body contains the `"Also sent via"` field

#### V.4 — Integration
- [x] Confirm `_route_event_time` gives each adapter the list of the *other* channels
- [x] Integration test: alert routed to signal + email + mattermost → each channel's message contains the other two names

---

### Phase W — Email: Resend integration, HTML template, ACK threading ✅

Replaced broken SMTP (Outlook.com blocks SMTP auth; host.docker.internal unreachable) with Resend as primary sender and retained aiosmtplib SMTP as fallback.

- [x] Add `resend==2.23.0` to `apps/router/pyproject.toml`
- [x] Add `resend_api_key: str = ""` to `apps/router/src/router/settings.py`
- [x] `EmailAdapter.__init__`: accept `resend_api_key`; call `resend.api_key = resend_api_key` when set
- [x] Fix STARTTLS vs SSL-on-connect: `_start_tls = use_tls and port != 465`; `_ssl = use_tls and port == 465`
- [x] Add `URGENCY_COLOUR` dict matching Mattermost/panel colours
- [x] Add `_location_str()`: returns `location_hint` if set, else `"{lat:.5f}, {lon:.5f} (map pin)"` when coordinates present, else `"not specified"`
- [x] Add `_build_body()`: returns `(plain, html)` tuple with urgency-coloured header banner and full case details
- [x] `send()` Resend path: pass both `text` and `html`; store generated `Message-ID` in headers
- [x] ACK confirmation email: same subject as notification (`{emoji} [{URGENCY}] New case: {id}`) for Gmail subject-based threading; green `#2e7d32` "✅ Acknowledged" banner to distinguish from initial alert
- [x] ACK confirmation Resend path: no custom headers — SES overrides our generated `Message-ID`, causing a broken `In-Reply-To` chain that prevents Gmail threading; subject-match alone is sufficient and reliable

---

### Miscellaneous backlog

- [ ] Postgres SSL cert ownership — chown `infra/postgres/certs/` in `install.py` or a postgres-init entrypoint so `ssl=on` works at startup
- [ ] Swagger: filter missing services from URL list so `/dispatch` and `/all` pages don't error when Jambonz is not running
- [ ] Swagger: rename `_PATHS["sysadmin"]` → `"text-to-speech"` in `infra/swagger/app.py`
- [ ] Docker install: `find_free_port(preferred, lo=8100, hi=9000)` in `scripts/install.py`; write chosen ports to `.env`
- [x] ClamAV: wire clamd socket into attachment upload pipeline
- [x] Attachment tests: MIME rejection, ClamAV positive → 400, max 3 files, max 10 MB, unauthenticated → 403
- [ ] Add `CURRENT_EVENT_OVERRIDE` to `.env-example`


---

### Phase X — Distributed Trace IDs & Structured Logging ✅

**Design decision:** Full OpenTelemetry was evaluated and rejected for always-on use. Rationale: ~15 MB of deps per service, requires a running OTLP collector, Grafana Tempo needs 200–400 MB RAM — too expensive for a system that may be dormant 50 weeks/year. The `form → router` path uses `pg_notify` (not HTTP), so OTel can't automatically link those spans anyway. The actual requirement — being able to `grep` across container logs by trace ID — is fully met by a lightweight ContextVar + JSON logging approach with zero new infrastructure.

#### X.1 — Shared library additions (`shared/src/emf_shared/`)
- [x] `tracing.py`: `ContextVar[str]` for trace ID, `get_trace_id()`, `set_trace_id()`, `new_trace_id()`, `outbound_headers()`
- [x] `middleware.py`: `TraceIDMiddleware(BaseHTTPMiddleware)` — reads `X-Trace-ID` from inbound request (or mints a new one), sets ContextVar, echoes header on response
- [x] `logging.py`: `configure_logging(service_name, level)` — installs `_TraceFilter` (injects `trace_id` + `service` into every log record) and JSON formatter (`python-json-logger`)
- [x] `shared/pyproject.toml`: add `python-json-logger>=3.0`, `starlette>=0.40`; mypy override for `pythonjsonlogger.*`

#### X.2 — App wiring (5 services)
- [x] `apps/form/src/emf_form/main.py`: `configure_logging("form")`, `TraceIDMiddleware` outermost
- [x] `apps/panel/src/emf_panel/main.py`: `configure_logging("panel")`, `TraceIDMiddleware` outermost
- [x] `apps/router/src/router/main.py`: `configure_logging("router")`, `TraceIDMiddleware` outermost
- [x] `apps/tts/src/tts/main.py`: `configure_logging("tts")`, `TraceIDMiddleware` outermost
- [x] `apps/jambonz/src/jambonz/main.py`: `configure_logging("jambonz")`, `TraceIDMiddleware` (CORSMiddleware stays outermost — LIFO)

#### X.3 — Background task trace injection
- [x] `apps/router/src/router/listener.py` `_handle_new_case()`: `set_trace_id(new_trace_id())` at entry (synthetic trace for each pg_notify dispatch)
- [x] `apps/router/src/router/main.py` `_poll_signal_reactions()`: `set_trace_id(new_trace_id())` per reaction processed

#### X.4 — Outbound header propagation (httpx call sites)
- [x] `apps/panel/src/emf_panel/routes.py` `_notify_router_ack()`
- [x] `apps/jambonz/src/jambonz/main.py` `_call_router_ack()`
- [x] `apps/router/src/router/channels/telephony.py` `_headers()` + jambonz-adapter register call
- [x] `apps/router/src/router/channels/mattermost.py` `_auth_headers()` + webhook calls
- [x] `apps/router/src/router/channels/slack.py` `send()` + `send_ack_confirmation()`
- [x] `apps/router/src/router/channels/signal.py` `send()` + `send_ack_confirmation()`

#### X.5 — Tests (`shared/tests/`)
- [x] `test_tracing.py`: trace ID format, uniqueness, set/get, `outbound_headers`, task isolation (ContextVar is per-task), child task inherits parent trace, `_TraceFilter` injects fields
- [x] `test_middleware.py`: generates ID when none provided, echoes incoming ID, ID available in route handler, unique per request, no bleed between requests
