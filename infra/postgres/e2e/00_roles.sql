-- infra/postgres/e2e/00_roles.sql
-- E2E test variant — no passwords (POSTGRES_HOST_AUTH_METHOD=trust in docker-compose.e2e.yml).
-- Schema is identical to the production init; only role creation differs.

CREATE ROLE form_user        LOGIN;
CREATE ROLE router_user      LOGIN;
CREATE ROLE service_user     LOGIN;
CREATE ROLE panel_viewer     LOGIN;
CREATE ROLE team_member      LOGIN;
CREATE ROLE backup_user      LOGIN;

CREATE SCHEMA IF NOT EXISTS forms;

GRANT USAGE ON SCHEMA forms TO
    form_user, router_user, service_user, panel_viewer, team_member;

GRANT CONNECT ON DATABASE emf_forms TO backup_user;
GRANT USAGE ON SCHEMA forms TO backup_user;
GRANT SELECT ON ALL TABLES IN SCHEMA forms TO backup_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA forms
    GRANT SELECT ON TABLES TO backup_user;

CREATE TABLE IF NOT EXISTS forms.cases (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    friendly_id  VARCHAR(64) NOT NULL UNIQUE,
    event_name   VARCHAR(64) NOT NULL,
    urgency      VARCHAR(16) NOT NULL DEFAULT 'medium',
    phase        VARCHAR(16) NOT NULL,
    form_data    JSONB       NOT NULL DEFAULT '{}',
    location_hint TEXT,
    status       VARCHAR(32) NOT NULL DEFAULT 'new',
    assignee     VARCHAR(128),
    tags         JSONB       NOT NULL DEFAULT '[]',
    team_id      UUID,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS cases_status_idx  ON forms.cases (status);
CREATE INDEX IF NOT EXISTS cases_urgency_idx ON forms.cases (urgency);
CREATE INDEX IF NOT EXISTS cases_team_idx    ON forms.cases (team_id);

CREATE TABLE IF NOT EXISTS forms.case_history (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id     UUID        NOT NULL REFERENCES forms.cases(id),
    changed_by  VARCHAR(128) NOT NULL,
    field       VARCHAR(64) NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS case_history_case_idx ON forms.case_history (case_id);

CREATE TABLE IF NOT EXISTS forms.notifications (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id         UUID        NOT NULL REFERENCES forms.cases(id),
    channel         VARCHAR(32) NOT NULL,
    state           VARCHAR(16) NOT NULL DEFAULT 'pending',
    attempt_count   INTEGER     NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    message_id      VARCHAR(256),
    acked_by        VARCHAR(128),
    acked_at        TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS notifications_case_idx  ON forms.notifications (case_id);
CREATE INDEX IF NOT EXISTS notifications_state_idx ON forms.notifications (state);

CREATE TABLE IF NOT EXISTS forms.idempotency_tokens (
    token      VARCHAR(64) PRIMARY KEY,
    case_id    UUID        NOT NULL REFERENCES forms.cases(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

GRANT INSERT ON forms.cases TO form_user;
GRANT INSERT, SELECT ON forms.idempotency_tokens TO form_user;

CREATE VIEW forms.cases_router WITH (security_barrier = true) AS
    SELECT id, friendly_id, event_name, urgency, status, location_hint, created_at, updated_at
    FROM forms.cases;

GRANT SELECT ON forms.cases_router TO router_user;
GRANT INSERT, UPDATE ON forms.notifications TO router_user;
GRANT SELECT ON forms.notifications TO router_user;

GRANT SELECT (id, friendly_id, urgency, status, assignee, updated_at) ON forms.cases TO service_user;
GRANT UPDATE (status, assignee, updated_at) ON forms.cases TO service_user;
GRANT INSERT ON forms.case_history TO service_user;

CREATE VIEW forms.cases_dispatcher WITH (security_barrier = true) AS
    SELECT id, friendly_id, urgency, status, location_hint, created_at, updated_at, assignee
    FROM forms.cases;

GRANT SELECT ON forms.cases_dispatcher TO panel_viewer;
GRANT SELECT ON forms.notifications TO panel_viewer;

GRANT SELECT, UPDATE ON forms.cases TO team_member;
GRANT SELECT, INSERT ON forms.case_history TO team_member;
GRANT SELECT ON forms.notifications TO team_member;
GRANT SELECT ON forms.idempotency_tokens TO team_member;

ALTER TABLE forms.cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE forms.cases FORCE ROW LEVEL SECURITY;

CREATE POLICY team_isolation ON forms.cases
    USING (
        team_id IS NULL
        OR team_id = current_setting('app.current_team_id', true)::uuid
    );
