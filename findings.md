# EMF Conduct System — Security & Quality Findings

**Date:** 2026-07-11 → 2026-07-12
**Status:** ✅ ALL ACTIONABLE FINDINGS RESOLVED (branch `fix/security-and-reliability-findings`)
**Scope:** Static code review + architecture analysis + live stack testing
**Skipped:** RLS `team_id` — null correct for single-team, kept as-is

## Resolution summary

### Pass 1–3 fixes

| Finding | Status |
|---|---|
| Router XSS (html.escape friendly_id) | ✅ fixed |
| JWT verify_aud enforcement | ✅ fixed |
| Signal webhook zero-auth | ✅ fixed |
| Mattermost empty-secret warning | ✅ fixed |
| resend.Emails.send blocks event loop | ✅ fixed |
| asyncio.create_task GC (task tracking set) | ✅ fixed |
| asyncpg listener connection leak | ✅ fixed |
| asyncpg TCP keepalive | ✅ fixed |
| Panel unauthed API 401 vs 303 redirect | ✅ fixed |
| admin_ack missing CaseHistory audit row | ✅ fixed |
| upload_attachment no case-existence check | ✅ fixed |
| Null bytes in form text fields | ✅ fixed |
| Dispatcher _revoked/_active_sessions → Redis | ✅ fixed |
| PENDING notifications orphaned on restart | ✅ fixed |
| Redis graceful degradation in panel routes | ✅ fixed |
| URGENCY_EMOJI/COLOUR dedup → emf_shared | ✅ fixed |
| Docker healthchecks (form/panel/router/tts) | ✅ fixed |
| postgres restart: unless-stopped | ✅ fixed |
| TTS concurrency cap + 429 backpressure | ✅ fixed |
| Piper hung subprocess timeout (30s / 504) | ✅ fixed |
| OIDC metadata fetch timeout (10s) | ✅ fixed |
| Prometheus alert rules | ✅ fixed |
| settings lru_cache | ✅ fixed |
| app_config instance-level cache | ✅ fixed |
| SECRET_KEY startup validation | ✅ fixed |
| https_only=False in SessionMiddleware | ✅ fixed |
| Redis pool per-request leak | ✅ fixed |
| attachments volume in compose | ✅ fixed |
| Dispatcher happy-path test | ✅ fixed |
| _handle_new_case dedup guard tests | ✅ fixed |

### Pass 4 fixes

| Finding | ID | Status |
|---|---|---|
| UNIQUE(case_id, channel) on notifications | AR2-06 | ✅ fixed |
| mark_acked atomic WHERE state != ACKED | AR2-02 | ✅ fixed |
| Safe Browsing disclosure in media_links hint | G-19 | ✅ fixed |
| _send_with_retry: 2 sessions → 1 per attempt | P-09 | ✅ fixed |
| validate_dispatcher_token Redis pipeline | P-11 | ✅ fixed |
| --workers 2 on form + panel Dockerfiles | SRE-16 | ✅ fixed |
| SlowAPI wired to Redis for multi-worker | SRE-19 | ✅ fixed |
| Log rotation (50m×5) + Loki/promtail | SRE-22 | ✅ fixed |

### Deferred (operational — pre-event tasks, not code fixes)

| Finding | ID | Reason deferred |
|---|---|---|
| Full downtime on every deploy | SRE-06 | Requires blue-green or rolling deploy setup; ops task |
| No on-call runbook | SRE-07 | Docs task; out of scope for code review |

---

## Reviews

- [Code Reviewer](#code-reviewer)
- [QA Expert](#qa-expert)
- [Debugger](#debugger)
- [Penetration Tester](#penetration-tester)
- [Chaos Engineer](#chaos-engineer)

---

## Code Reviewer

### Summary

Overall a well-structured FastAPI codebase with good type discipline and thoughtful security design. Main issues: in-memory dispatcher session state that breaks in multi-process deployments, a full-table scan on every form submission for friendly-ID collision detection, `URGENCY_EMOJI` duplicated across four channel adapters, `https_only=False` on the session cookie, a missing case-existence check on attachment upload, and the RLS `app.current_team_id` session variable never being set from application code (making RLS a no-op in practice).

---

### Findings

#### CRITICAL

**CR-1 — Dispatcher session state is in-process memory; breaks in multi-worker or multi-instance deployments**

`apps/panel/src/emf_panel/dispatcher.py` stores `_revoked: set[str]` and `_active_sessions: dict[str, list[str]]` as module-level globals. When Uvicorn runs with `--workers N`, each worker process has its own independent copy. A token revoked in worker 1 is still valid in worker 2. A device counted against `max_devices` in worker 1 is invisible to worker 2. This is a security hole if `max_devices` is a real limit and a correctness hole for revocation. Fix: move both stores to Redis (already a dependency of the panel service).

- `/work/apps/panel/src/emf_panel/dispatcher.py` lines 9–11

**CR-2 — `SessionMiddleware` sets `https_only=False`**

`apps/panel/src/emf_panel/main.py:38` sets `https_only=False` on the Starlette session middleware. The session cookie therefore has no `Secure` flag and will be sent over plain HTTP. Even behind a TLS-terminating Caddy proxy, this is wrong: if Caddy misconfigures a route or internal Docker traffic is intercepted, session cookies travel in cleartext. Fix: `https_only=True`; Starlette handles `X-Forwarded-Proto` correctly behind a proxy.

- `/work/apps/panel/src/emf_panel/main.py` line 38

**CR-3 — RLS `app.current_team_id` is never set; Row-Level Security is a no-op**

`infra/postgres/00_roles.sql:128–132` creates a `team_isolation` RLS policy gating access by `current_setting('app.current_team_id', true)::uuid`. The `team_id` column on all cases is `NULL` (never set anywhere in the codebase). The policy allows `team_id IS NULL` unconditionally, so every `team_member` user sees every case. The RLS machinery exists but does nothing — dead security infrastructure that creates false confidence.

- `/work/infra/postgres/00_roles.sql` lines 125–132
- `/work/apps/form/src/emf_form/models.py` line 29 (`team_id` never populated)
- `/work/apps/panel/src/emf_panel/models.py` line 29

---

#### HIGH

**CR-4 — Full-table scan on `forms.cases.friendly_id` on every form submission**

`apps/form/src/emf_form/routes.py:210–211` loads the entire `friendly_id` column into Python memory on every submission to check for collision. At 10,000 cases this is ~640 KB per request plus DB round-trip. Fix: query `SELECT 1 FROM forms.cases WHERE friendly_id = :candidate LIMIT 1` per candidate, or catch `IntegrityError` from the existing `UNIQUE` constraint.

- `/work/apps/form/src/emf_form/routes.py` lines 210–214

**CR-5 — `upload_attachment` does not verify the `case_id` exists before writing to disk**

`/attachments` creates `{attachment_dir}/{case_id}/` and writes the file without confirming `case_id` references a real row in `forms.cases`. An attacker can upload files under arbitrary UUID directories, wasting storage and potentially confusing the panel attachment display.

- `/work/apps/form/src/emf_form/routes.py` lines 299–342

**CR-6 — `admin_ack` mutates `Case.assignee` without writing a `CaseHistory` row**

`apps/panel/src/emf_panel/routes.py:664–666` updates the assignee on acknowledgement but writes no audit history row. Every other assignee change (via `update_assignee`) produces a `CaseHistory` entry. The audit log has no record of who acknowledged and claimed a case via the ACK path.

- `/work/apps/panel/src/emf_panel/routes.py` lines 649–670

**CR-7 — `URGENCY_EMOJI` and `URGENCY_COLOUR` duplicated across four channel adapters**

The same dict literal appears in `email.py`, `signal.py`, `mattermost.py`, and `slack.py`. Values differ subtly between adapters (e.g. `"low": "📋"` in email/signal/mattermost vs `"low": "🟢"` in slack). Extract to a shared constants module in `router/channels/`.

- `/work/apps/router/src/router/channels/email.py` lines 15–27
- `/work/apps/router/src/router/channels/signal.py` lines 13–19
- `/work/apps/router/src/router/channels/mattermost.py` lines 13–25
- `/work/apps/router/src/router/channels/slack.py` lines 13–18

**CR-8 — `CaseHistory.changed_by` is `nullable=True` in ORM but `NOT NULL` in SQL schema**

`apps/panel/src/emf_panel/models.py:49` and `apps/form/src/emf_form/models.py:49` map `changed_by` as `Mapped[str | None]` with `nullable=True`. The SQL schema at `infra/postgres/00_roles.sql:47` defines it `VARCHAR(128) NOT NULL`. Any code path that inserts a `CaseHistory` with `changed_by=None` will produce a DB constraint violation at runtime.

- `/work/apps/panel/src/emf_panel/models.py` line 49
- `/work/apps/form/src/emf_form/models.py` line 49
- `/work/infra/postgres/00_roles.sql` line 47

**CR-9 — `app_config` re-reads and re-parses JSON from disk on every property access**

`shared/src/emf_shared/config.py:104–105` is a plain `@property` that calls `self.config_path.read_text()` + `json.loads()` + Pydantic validation on every access. `get_settings()` in both form and panel creates a new `Settings()` instance per request (no caching). Combined: every request does multiple filesystem reads + JSON parses. Fix: use `@lru_cache` on `get_settings()` or a `functools.cached_property` on `app_config`.

- `/work/shared/src/emf_shared/config.py` lines 103–105
- `/work/apps/panel/src/emf_panel/settings.py` line 27
- `/work/apps/form/src/emf_form/settings.py` line 14

**CR-10 — `get_redis` creates a new connection pool on every request**

`apps/panel/src/emf_panel/routes.py:57–58` calls `aioredis.from_url(...)` inside a FastAPI dependency, initialising a new connection pool per request. Fix: create the Redis client once at startup in `lifespan` and store it on `app.state`.

- `/work/apps/panel/src/emf_panel/routes.py` lines 57–58

---

#### MEDIUM

**CR-11 — Four dispatcher routes use `= None` default + `type: ignore[assignment]` to bypass FastAPI DI**

`apps/panel/src/emf_panel/routes.py:755–756, 821–822, 859–860, 883–884` — `settings` and `session` are typed `Annotated[Settings, Depends(get_settings)] = None` with `type: ignore[assignment]`. This is a design hack that suppresses type checking on safety-critical parameters. Refactor: extract a `_validate_dispatcher_token` helper callable without DI, or use proper dependency overrides in tests.

- `/work/apps/panel/src/emf_panel/routes.py` lines 747–756, 817–822

**CR-12 — `_verify_bearer` disables JWT audience verification**

`apps/panel/src/emf_panel/auth.py:56`: `options={"verify_aud": False}` — disabling audience verification allows tokens issued for other clients on the same IdP to authenticate to the panel if they share the same issuer and signing key. Fix: `"verify_aud": True` with `audience=settings.oidc_client_id`.

- `/work/apps/panel/src/emf_panel/auth.py` line 56

**CR-13 — `submit_form` is 84 lines with 7+ distinct concerns**

`apps/form/src/emf_form/routes.py:139–250` handles honeypot, phase, event validation, urgency validation, contact-method validation, idempotency lookup, URL safety scanning, friendly-ID generation, case creation, token creation, pg_notify, and response — all inline. Refactor: extract `_validate_submission_fields`, `_handle_idempotency`, `_create_case_and_notify`.

- `/work/apps/form/src/emf_form/routes.py` lines 139–250

**CR-14 — `case_list` is 70 lines mixing query-building, business logic, and template rendering**

`apps/panel/src/emf_panel/routes.py:197–283` — filter building, map URL resolution, notification state resolution, and template rendering all in one function. Extract `_build_case_query`, `_resolve_map_urls`, `_resolve_notif_states`.

- `/work/apps/panel/src/emf_panel/routes.py` lines 197–283

**CR-15 — `_send_with_retry` (84 lines) opens the session factory three times with repeated boilerplate**

`apps/router/src/router/alert_router.py:128–217` — session factory opened three times in sequence. Additionally, `asyncio.sleep(delay_minutes * 60)` inside a fire-and-forget task with no cancellation support means orderly shutdown hangs or silently drops in-flight retries.

- `/work/apps/router/src/router/alert_router.py` lines 128–217

**CR-16 — `lifespan` in `router/main.py` is 80+ lines of inline adapter construction**

`apps/router/src/router/main.py:129–235` — builds every adapter, wires them, and starts background tasks inline in the lifespan function. This makes the construction logic untestable without invoking the ASGI lifespan. Extract adapter factory functions.

- `/work/apps/router/src/router/main.py` lines 129–235

**CR-17 — `except jwt.PyJWTError, ValueError:` uses ambiguous Python 2-style syntax**

`apps/router/src/router/main.py:309` — in Python 3, `except A, B:` is parsed as `except (A, B):` (tuple catch) not `except A as B:`. It works correctly but is visually misleading. Use `except (jwt.PyJWTError, ValueError):` explicitly.

- `/work/apps/router/src/router/main.py` line 309

**CR-18 — TTS `_audio_files` dict grows unbounded under sustained load**

`apps/tts/src/tts/main.py:32` — `_audio_files` is only purged when requests arrive at `/synthesise/file` or `/audio/{token}`. Under high Jambonz call volume, if synthesis requests arrive faster than retrieval, the dict and on-disk cache files grow without bound. Add a periodic background cleanup task or a maximum cache size.

- `/work/apps/tts/src/tts/main.py` lines 32, 131–145

---

#### LOW

**CR-19 — `team_id` column is dead infrastructure**

Defined in model and schema but never set or read. Either implement team isolation (fix CR-3) or remove the column.

- `/work/apps/form/src/emf_form/models.py` line 29
- `/work/apps/panel/src/emf_panel/models.py` line 29

**CR-20 — `aiosmtplib` is an unused dependency of `apps/form`**

`apps/form/pyproject.toml` lists `aiosmtplib>=5.1.0` but the form app never imports it. Email is the router's responsibility. Remove the unused dependency.

- `/work/apps/form/pyproject.toml`

**CR-21 — `require_conduct_team` calls `get_settings()` directly for bearer path, bypassing FastAPI DI**

`apps/panel/src/emf_panel/auth.py:68` — `settings = get_settings()` called directly (not via `Depends`), bypassing `dependency_overrides` in tests and creating a new `Settings` instance that reads `.env` directly. Makes the bearer auth path untestable in isolation.

- `/work/apps/panel/src/emf_panel/auth.py` line 68

**CR-22 — `LOCAL_DEV=true` is the compose default, bypassing routing-window check**

`infra/docker-compose.yml:34`: `LOCAL_DEV: ${LOCAL_DEV:-true}` — if `LOCAL_DEV` is unset in a prod `.env`, the form always accepts submissions regardless of event dates. Default should be `false`.

- `/work/infra/docker-compose.yml` line 34
- `/work/apps/form/src/emf_form/routes.py` line 108

---

#### INFO

**CR-23 — `idempotency_tokens.token` column size mismatch: `VARCHAR(64)` in SQL vs `String(256)` in ORM**

ORM accepts values up to 256 chars; DB rejects them at 64. Any idempotency key longer than 64 characters fails at the DB layer.

- `/work/infra/postgres/00_roles.sql` line 74
- `/work/apps/form/src/emf_form/models.py` line 64

**CR-24 — `_send_via_resend` is a dead method in `EmailAdapter`**

`apps/router/src/router/channels/email.py:79–99` — never called from `send()`. The `send()` method has its own inline Resend call (lines 182–196) that duplicates the logic. Delete the dead method or route `send()` through it.

- `/work/apps/router/src/router/channels/email.py` lines 79–99, 182–196

**CR-25 — `case_history.id` is `UUID` in SQL but `Integer` with `autoincrement` in both ORM models**

`infra/postgres/00_roles.sql:44` uses `UUID PRIMARY KEY DEFAULT gen_random_uuid()`. Both ORM models map it as `Integer` with `autoincrement=True`. Schema/ORM mismatch would cause failures if the ORM ever generates DDL or queries for this column directly.

- `/work/infra/postgres/00_roles.sql` line 44
- `/work/apps/panel/src/emf_panel/models.py` line 47
- `/work/apps/form/src/emf_form/models.py` line 47

**CR-26 — `case_list` passes a local closure (`make_sort_url`) to the Jinja2 template context**

`apps/panel/src/emf_panel/routes.py:232–234` — non-standard pattern. Consider computing sort URLs in the template via a macro or a simple URL helper filter instead.

- `/work/apps/panel/src/emf_panel/routes.py` lines 232–234

---

## QA Expert

### Summary

Test suite is in decent shape for a project of this size. Unit tests cover the happy path well. Key weaknesses: the pg_notify listener has zero unit tests; Slack adapter has zero tests; OIDC auth flow is never tested end-to-end; the panel has multiple untested endpoints; and no coverage tooling enforces minimums in CI.

---

### Findings

#### CRITICAL

**C1 — pg_notify listener (`apps/router/src/router/listener.py`) has zero unit tests**

`listen_for_cases` and `_handle_new_case` are the core of the notification pipeline. Neither function has any test. The reconnection loop, the duplicate-notification guard (`if result.scalar_one() > 0: return`), and the `force=True` retrigger path are all untested. A bug here silently drops alerts with no coverage signal.

Relevant files:
- `/work/apps/router/src/router/listener.py` — entire file untested
- `/work/apps/router/tests/test_router.py` — no import of `listener` module anywhere

**C2 — Slack adapter (`apps/router/src/router/channels/slack.py`) has zero tests**

`SlackAdapter.send` and `SlackAdapter.send_ack_confirmation` are never exercised. Email, Signal, Mattermost, and EMFPhone all have dedicated tests; Slack is entirely missing. If Slack is configured in production and its webhook URL changes format, the failure would be invisible until a real alert fires.

Relevant files:
- `/work/apps/router/src/router/channels/slack.py`
- `/work/apps/router/tests/test_router.py` — no `SlackAdapter` import

---

#### HIGH

**H1 — No test for `_handle_new_case` deduplication guard**

`listener.py:54–59` skips routing if notifications already exist for a case. The only e2e coverage requires a live Postgres + pg_notify stack. The guard logic (checking `func.count()`) is non-trivial SQLAlchemy — a regression here could cause duplicate alerts for every case.

**H2 — Panel: `update_assignee`, `update_tags`, `serve_attachment`, `admin_ack`, `dispatcher_cases` endpoints have no unit tests**

`/work/apps/panel/tests/test_routes.py` covers status transitions, urgency, history, and dispatcher session CRUD — but not:
- `PATCH /api/v1/cases/{id}/assignee` (line 487 of `routes.py`) — includes Redis `sadd` side-effect and auto-transition from `new` → `assigned`
- `PATCH /api/v1/cases/{id}/tags` (line 533)
- `GET /cases/{case_id}/attachments/{filename}` (line 911) — path traversal guard at line 918–919 is untested
- `POST /api/v1/cases/{id}/ack` (admin ack, line 649) — different from dispatcher ack; also fires `_notify_router_ack`
- `GET /api/v1/dispatcher/cases` (line 816)

The path traversal guard (`if "/" in filename or ".." in filename`) is security-critical and has no unit test.

**H3 — OIDC auth flow (`apps/panel/src/emf_panel/auth.py`) is never tested end-to-end**

`/login`, `/auth/callback`, `/logout` routes exist but no test exercises the actual OIDC redirect → callback → session flow. `auth_callback` has a `MismatchingStateError` fallback path (line 135–137) with no test. `_verify_bearer` (line 47) has no test at all. Tests only mock out `require_conduct_team` entirely, so the actual JWT verification logic is untested outside the OWASP suite's integration-style test.

**H4 — No test for `_notify_router_ack` in panel routes**

`admin_ack` (line 649) and `dispatcher_ack` (line 853) both call `_notify_router_ack` which fires an outbound HTTP request. The function silently swallows all exceptions (line 645–646). No test verifies the outbound call is attempted, or that the silent-failure path doesn't mask a configuration error.

**H5 — No negative tests for attachment upload edge cases**

`/work/apps/form/tests/test_routes.py` tests clean/infected/clamd-unreachable — but not:
- File exactly at the size limit vs 1 byte over
- Non-image content with a spoofed MIME type (e.g. a PDF with `.jpg` extension)
- Zero-byte upload
- No `case_id` query param (FastAPI would 422 but this is never verified)
- File with valid magic bytes but invalid image body

The `_detect_image_ext` function at `routes.py:280` is complex enough to warrant direct unit tests separate from the HTTP layer — it has none.

**H6 — Status transition tests do not verify all legal transitions**

`/work/apps/panel/tests/test_routes.py:191–225` tests `new → assigned` (valid), `new → in_progress` (invalid), and `closed → new` (invalid). The transition map has 6 states and ~12 valid edges. Untested valid transitions include:
- `assigned → in_progress`, `assigned → new`, `assigned → closed`
- `in_progress → action_needed`, `in_progress → decision_needed`, `in_progress → closed`
- `action_needed → in_progress`, `action_needed → decision_needed`, `action_needed → closed`
- `decision_needed → closed`, `decision_needed → in_progress`

---

#### MEDIUM

**M1 — No coverage enforcement in CI**

`/work/.github/workflows/ci.yml` runs pytest with `-q` but no `--cov` flag. There is no `pytest.ini` or `pyproject.toml` with `[tool.coverage]` settings, no `fail_under` threshold, and no coverage report uploaded as an artifact. A developer can delete tests and CI stays green.

**M2 — `test_form_user_has_no_update_grant` (OWASP A01, line 68) has a logical flaw**

The assertion at `/work/tests/security/test_owasp.py:74–77` is:
```python
assert (
    "UPDATE" not in sql.split("form_user")[1].split("team_member")[0].upper()
    or "UPDATE ON forms.cases TO form_user" not in sql
)
```
This is an `or` — it passes if *either* condition is true, meaning it always passes if the second clause is true regardless of the first. The intent is clearly `and`. The test gives false confidence about the DB role grant.

**M3 — `test_safe_browsing_api_failure_allows_submission` is misleading (line 520)**

The test patches `_check_urls_safe_browsing` to return `[]` directly, which is what the function itself returns on API failure. But the test comment says "API failure → returns []". This doesn't actually test that the real function swallows HTTP exceptions — it only tests the caller's behaviour when the function returns `[]`. The actual error-handling path in `_check_urls_safe_browsing` (lines 96–98 of `routes.py`) has no test.

**M4 — Router `_send_with_retry` retry count hardcoded assumption**

`/work/apps/router/tests/test_router.py:424` asserts `email.send.call_count == 4` with comment `# 4 retry delays [0, 5, 10, 15]`. The retry count is derived from a hardcoded comment rather than from a constant or the actual retry configuration. If the retry policy changes, the test assertion breaks without catching the regression.

**M5 — No test for `_current_active_event` in panel routes**

`routes.py:61–69` determines which event is active for dispatcher configuration. The boundary conditions (event start/end + padding) are tested in `shared/tests/test_phase.py` but the panel-specific `_current_active_event` is not — it has different semantics (returns event name vs Phase enum) and its own padding logic.

**M6 — `test_panel_viewer_grants_no_form_data` (OWASP A01, line 56) is fragile**

The test at `/work/tests/security/test_owasp.py:56–65` uses string splitting on `panel_viewer` and `team_member` to check SQL role grants. This is sensitive to whitespace, ordering, and comments in `00_roles.sql`. Any reformat of the SQL file could cause a false pass or false fail.

**M7 — No test for `dispatcher_view` HTML endpoint with valid token**

The `GET /dispatcher` HTML route (line 746 of `routes.py`) is never tested via the unit test suite. Only `test_expired_dispatcher_token_rejected` and `test_revoked_dispatcher_token_rejected` hit this endpoint — both with invalid tokens. The happy path (valid token renders `dispatcher.html`) has no test.

**M8 — TTS: no test for `_run_piper` failure path**

`/work/apps/tts/tests/test_tts.py` patches `_run_piper` with a fake but never tests what happens when the real Piper process fails (non-zero exit, subprocess exception). The `synthesise` endpoint's error handling on Piper failure is untested.

**M9 — e2e tests require manual `MOCK_WEBHOOK_URL` env var**

`/work/tests/e2e/test_notification_flow.py:86` skips `test_notification_sent_to_webhook` if `MOCK_WEBHOOK_URL` is not set. CI (`scripts/run_e2e.sh`) presumably does not set this, so the notification delivery assertion is never run in automated tests. The most critical behaviour (alert actually delivered) has conditional coverage.

---

#### LOW

**L1 — No performance / load tests**

No wrk, locust, or k6 configuration exists. The rate limiter is configured at `15/10 seconds` but its behaviour under concurrent load (e.g. multiple IPs, Redis backend) is not tested. No baseline latency or throughput measurements exist.

**L2 — No accessibility (a11y) tests**

`test_form_ui.py` uses Playwright but no `axe-core` integration or WCAG checks. The form is public-facing and likely subject to accessibility obligations; no automated a11y validation runs.

**L3 — Conftest fixture creates config fixture file on disk if missing**

`/work/apps/router/tests/conftest.py:14–40` writes `tests/fixtures/config.json` at import time if it does not exist. This is side-effectful module-level I/O that mutates the working directory on test collection. It can cause unexpected behaviour when tests run from a read-only filesystem or when running tests in parallel.

**L4 — `test_context_var_is_task_local` uses `asyncio.run()` inside a sync test**

`/work/shared/tests/test_tracing.py:42–65` calls `asyncio.run()` inside a test function rather than marking it `@pytest.mark.asyncio`. This works but bypasses the event loop configured by `pytest-asyncio`, meaning any fixtures that depend on the asyncio mode would not be available. This is a testing anti-pattern that could cause subtle issues when the test suite's asyncio mode changes.

**L5 — `_revoked` set in `dispatcher.py` is process-global state with no cleanup between tests**

`/work/apps/panel/tests/test_routes.py:312` calls `_revoked.discard(jti)` as cleanup, but only in `test_revoke_dispatcher_session`. `test_revoked_dispatcher_token_rejected` (line 322) also calls `revoke_token(jti)` and manually discards afterward. If a test fails before the discard, the revoked JTI persists and can affect subsequent tests. There is no fixture-based teardown.

**L6 — No test for `emf_shared.db` module**

`/work/shared/` has tests for config, friendly_id, middleware, phase, and tracing — but not for `db.py` (which contains `get_session` and engine setup). The session factory and connection URL parsing are untested.

**L7 — `test_form_api_conformance` (schemathesis) excludes `/api/submit` from positive-data-acceptance**

`/work/tests/e2e/test_form_schema.py:43–48` suppresses the `positive_data_acceptance` check for `/api/submit` because `event_name` is runtime-constrained. This means schemathesis never verifies that a validly-shaped payload actually returns 201. The exclusion is documented but it represents a gap in the schema conformance coverage.

---

#### INFO

**I1 — CI uses `actions/checkout@v7`, `astral-sh/setup-uv@v7`, `actions/setup-node@v6` — non-existent versions**

At time of review (2026-07-11), the latest stable versions are `@v4`, `@v5`, and `@v4` respectively. `@v7` and `@v6` may not exist. If these pinned versions don't exist, CI fails immediately on setup. Verify these action versions are correct.

**I2 — ZAP scan only runs on `workflow_dispatch`**

`/work/.github/workflows/security.yml:22` — ZAP is gated to manual dispatch only. It does not run on PRs or on a schedule alongside gitleaks. Dynamic OWASP scanning is therefore never automated.

**I3 — `test_valid_status_transition` does not verify DB state**

`/work/apps/panel/tests/test_routes.py:191–198` asserts the response body has `status == "assigned"` but does not verify `mock_session.execute` was called with an `UPDATE` statement. The session mock accepts all calls silently, so a broken DB write would still pass this test.

**I4 — No test for `emf_form.main` startup event (SMTP config, attachment dir creation)**

`main.py` likely has a lifespan handler or startup logic. If `attachment_dir` creation or config validation fails on startup, no test catches it.

**I5 — `test_also_sent_via_each_channel_sees_others` (router, line 1614) uses `asyncio.sleep(0)` to drain tasks**

This is brittle — it works for immediately-completing coroutines but would fail if `_send_with_retry` involved real delays. The test currently patches `asyncio.create_task` with `asyncio.ensure_future` which is not identical behaviour; this may mask race conditions in the real `create_task` path.

---

## Debugger

### Summary

12 bugs found across async, resource, state, and reliability categories. One is a blocking I/O call in an async context that will freeze the event loop during every email send via Resend. One is a connection leak in the pg_notify listener that accumulates dead asyncpg connections on each reconnect. Several are correctness issues that would surface under production load or restart.

---

### Findings

#### CRITICAL

**D-C1 — Blocking I/O in async context: `resend.Emails.send()` called without `asyncio.to_thread`**

File: `/work/apps/router/src/router/channels/email.py`, lines 95, 192, 240

`resend.Emails.send()` is a synchronous function that uses the `requests` library internally (confirmed: `/work/apps/router/.venv/lib/python3.14/site-packages/resend/http_client_requests.py` uses `requests.request`). It is called directly inside `async def _send_via_resend` and `async def send_ack_confirmation` without wrapping in `asyncio.to_thread`. This blocks the entire event loop for the duration of the HTTP request (typically 100–2000ms depending on Resend's API latency).

**Effect:** During every email send via Resend, all other requests to the router service are frozen. Under load, this means any simultaneous pg_notify callbacks, webhook requests, or health checks stall. At extreme latency (Resend timeout ~10s), the event loop is blocked for 10 seconds.

**Fix:** `await asyncio.to_thread(resend.Emails.send, params)` — or switch to `resend.Emails.send_async()` which is available in the installed version.

---

**D-C2 — asyncpg connection leak in listener reconnect loop**

File: `/work/apps/router/src/router/listener.py`, lines 22–44

The `listen_for_cases` reconnect loop calls `asyncpg.connect()` to establish a connection, but never closes it before reconnecting. When the connection drops (or the inner `while not conn.is_closed()` loop exits due to an exception), the `except Exception` block logs and sleeps, then the outer `while True` loop immediately calls `asyncpg.connect()` again — leaving the previous connection unclosed.

```python
conn: asyncpg.Connection = await asyncpg.connect(dsn)
# ... no finally: await conn.close()
```

Over time (every DB hiccup, network blip, or Postgres restart), connections accumulate in `TIME_WAIT` or remain open on the Postgres side, eventually exhausting `max_connections`.

**Fix:** Wrap the body in `try/finally: await conn.close()`.

---

#### HIGH

**D-H1 — Race condition in `friendly_id` generation: TOCTOU between read and insert**

File: `/work/apps/form/src/emf_form/routes.py`, lines 210–214

```python
existing_ids_result = await session.execute(select(Case.friendly_id))
existing_ids: set[str] = set(existing_ids_result.scalars().all())
friendly_id = generate_unique(existing_ids, str(case_id))
```

The code fetches all existing friendly IDs, generates a candidate not in that set, then inserts. Between the read and the insert, a concurrent request can insert the same friendly ID. `friendly_id` has a `UNIQUE` constraint so the insert will raise an `IntegrityError` — which propagates as an unhandled 500 to the caller. Under concurrent load (event rush at submission open), this will produce 500s and dropped reports.

**Effect:** Unhandled `IntegrityError` → HTTP 500, case not saved, no retry.

**Fix:** Catch `IntegrityError` on commit and retry with a new `friendly_id`, or generate the friendly ID using a DB sequence/trigger.

Also note: fetching all friendly IDs is an N-row table scan that will degrade as cases accumulate.

---

**D-H2 — `_send_with_retry` tasks are fire-and-forget with no reference kept**

File: `/work/apps/router/src/router/alert_router.py`, lines 117, 122; `/work/apps/router/src/router/listener.py`, line 35

Every `asyncio.create_task(...)` call discards the returned `Task` object. Python's asyncio documentation explicitly warns that tasks not referenced can be garbage-collected mid-execution. While CPython's reference implementation is lenient about this, the official asyncio docs state:

> "Save a reference to the result of `create_task()` to avoid the task being garbage collected."

More critically: when the router service shuts down (lifespan `yield` returns), `task.cancel()` is called for the listener task, but any in-flight `_send_with_retry` tasks spawned by that listener are not cancelled. They continue running after the lifespan context exits, potentially during a rolling restart — accessing a session factory that is being torn down.

**Effect:** Potential mid-send corruption on shutdown; GC-collected tasks under memory pressure; undelivered alerts.

**Fix:** Keep a `set[asyncio.Task]` and add a `task.add_done_callback(tasks.discard, task)` pattern. Cancel all pending tasks on shutdown.

---

**D-H3 — `get_redis()` dependency creates a new Redis connection object per request with no cleanup**

File: `/work/apps/panel/src/emf_panel/routes.py`, lines 57–58

```python
async def get_redis(settings: ...) -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)
```

`aioredis.from_url` creates a connection pool. This function is a regular `async def`, not an `async generator` — FastAPI treats it as a direct dependency that returns a value. The returned pool is never closed. Each request creates and leaks a new pool object. Over time this exhausts file descriptors and Redis server connections.

**Fix:** Convert to an `AsyncGenerator` pattern:
```python
async def get_redis(settings: ...) -> AsyncGenerator[aioredis.Redis, None]:
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()
```

Or, create the pool once at startup in the lifespan and expose it via `app.state`.

---

**D-H4 — Module-level mutable dispatcher state lost on process restart**

File: `/work/apps/panel/src/emf_panel/dispatcher.py`, lines 9–10

```python
_revoked: set[str] = set()
_active_sessions: dict[str, list[str]] = {}
```

Both `_revoked` (revoked JTIs) and `_active_sessions` (device tracking) are in-process memory. If the panel service restarts (crash, rolling deploy, container restart), all revoked tokens become valid again and device counts reset. A revoked dispatcher token can be reused immediately after a restart.

**Effect:** Security regression on restart — revoked tokens un-revoke. Device cap enforcement resets.

**Fix:** Back both structures with Redis (already a panel dependency). Use `SADD`/`SISMEMBER` for revoked tokens with TTL matching the JWT expiry; use a Redis hash for device tracking.

---

**D-H5 — `app_config` property reads `config.json` from disk on every access**

File: `/work/shared/src/emf_shared/config.py`, lines 103–105

```python
@property
def app_config(self) -> AppConfig:
    return AppConfig.model_validate(json.loads(self.config_path.read_text()))
```

`settings.app_config` is called multiple times per request (e.g. panel `routes.py` calls it in nearly every handler). Each call does a synchronous disk read + JSON parse + Pydantic validation. In the panel alone there are 22+ call sites. Under load, every request does multiple blocking filesystem reads in async handlers.

**Effect:** Disk I/O on every request; can cause latency spikes if config.json is on a slow volume (NFS, Docker bind mount under Mac). If `config.json` is deleted or malformed mid-request, `OSError` or `ValidationError` propagates uncaught to callers that don't protect it.

**Fix:** Cache the result with `functools.lru_cache` or read once at startup and store on `app.state`.

---

#### MEDIUM

**D-M1 — `upload_attachment` does not verify the `case_id` exists in the database**

File: `/work/apps/form/src/emf_form/routes.py`, lines 299–342

`POST /attachments?case_id=<uuid>` accepts any UUID, creates a directory on disk at `attachment_dir/<uuid>/`, and stores the file — without checking whether a case with that ID actually exists. An attacker or buggy client can pre-populate arbitrary directories with files, or exhaust disk space by uploading to random UUIDs.

**Fix:** Add `case = await session.get(Case, case_id); if not case: raise HTTPException(404)` before accepting the upload. The `session` dependency is not currently in scope for this endpoint — it needs to be added.

---

**D-M2 — `_handle_new_case` uses `async for session in get_session()` antipattern**

File: `/work/apps/router/src/router/listener.py`, lines 52–66

`get_session()` is an `AsyncGenerator` intended to be used as a FastAPI `Depends`. Using it with `async for` works (it yields exactly once), but an early `return` inside the loop body (lines 61, 65) abandons the generator mid-iteration without sending a `GeneratorExit`. In CPython this is cleaned up by the GC, but it means the session's `__aexit__` is not called synchronously at the `return` point — the session remains open until the next GC cycle. Under the retry delays in `_send_with_retry` (up to 30 minutes total), this means sessions can pile up.

**Fix:** Use `async with get_session_factory()() as session:` directly, as `_send_with_retry` does.

---

**D-M3 — `_run_piper` subprocess failure is silently ignored**

File: `/work/apps/tts/src/tts/main.py`, lines 100–112

```python
proc = await asyncio.create_subprocess_exec(...)
stdout, _ = await proc.communicate(input=text.encode())
return stdout
```

`proc.returncode` is never checked. If Piper exits with a non-zero code (model not found, OOM, bad input), `stdout` will be empty bytes and the function returns `b""`. The `/synthesise` endpoint then streams zero bytes as `audio/wav`. The `/synthesise/file` endpoint writes a zero-byte WAV file and caches it permanently (`persistent=True`). The Jambonz caller gets an empty audio file and plays silence, with no error surfaced.

**Fix:** Check `proc.returncode` after `communicate()` and raise `HTTPException(500)` if non-zero.

---

**D-M4 — OIDC `verify_aud: False` disables audience validation on bearer tokens**

File: `/work/apps/panel/src/emf_panel/auth.py`, line 57

```python
options={"verify_aud": False},
```

Audience validation is disabled for bearer token JWTs. This means a token issued for a different service (e.g. an internal API, a different client) will be accepted by the panel as long as it has the right issuer and `team_conduct` group claim. If the same IdP issues tokens for multiple services, a compromised token for another service can be used to access the panel.

**Fix:** Configure the expected audience (`oidc_client_id`) and set `audience=settings.oidc_client_id` in `jwt.decode`. Remove `verify_aud: False`.

---

**D-M5 — Session cookie `https_only=False` in production code**

File: `/work/apps/panel/src/emf_panel/main.py`, line 38

```python
app.add_middleware(SessionMiddleware, secret_key=_session_secret, https_only=False, same_site="lax")
```

`https_only=False` means the session cookie is sent over HTTP as well as HTTPS. Even though TLS terminates at Caddy, if internal routing were ever misconfigured or a direct HTTP connection reached the panel, the session cookie would be transmitted in clear text.

**Fix:** Set `https_only=True`. This is safe because TLS terminates at Caddy; internal traffic is within the Docker network.

---

**D-M6 — `pg_notify` sent before transaction commits — possible notification before row is visible**

File: `/work/apps/form/src/emf_form/routes.py`, lines 240–245

```python
await session.flush()
await session.execute(text("SELECT pg_notify('new_case', :payload)"), {"payload": str(case_id)})
await session.commit()
```

`pg_notify` inside a transaction fires the notification when the transaction commits, not when the `SELECT pg_notify()` executes. This is correct PostgreSQL behaviour. However, the notification is sent within the same session as the case insert, so if the `commit()` call fails (network blip, Postgres constraint violation), the notification is never sent and the case exists unnotified. There is no retry mechanism for a missed notification in this path.

The router's listener has no dead-letter queue or polling fallback. Cases that miss their initial notification (due to a commit failure, listener downtime, or missed NOTIFY) are never routed until a manual `retrigger_case` is issued.

**Effect:** Silent case drop — case exists in DB, team never notified.

**Fix:** Add a background polling job that scans for cases with no `notifications` row older than N minutes and re-triggers them. This is the standard pg_notify reliability pattern.

---

#### LOW

**D-L1 — Signal adapter uses first event's `signal_group_id` only — breaks with multiple events**

File: `/work/apps/router/src/router/main.py`, line 135

```python
ev = cfg.events[0] if cfg.events else None
```

The Signal adapter is initialised with the first event's `signal_group_id`. If `config.json` contains multiple events (e.g. a future event added before the previous one's data is archived), Signal notifications go to the wrong group for the non-first event.

**Fix:** Derive `signal_group_id` from `current_phase`/active event at routing time, not at startup.

---

**D-L2 — `_audio_files` dict and `_purge_expired` are not concurrency-safe**

File: `/work/apps/tts/src/tts/main.py`, lines 32–54

`_audio_files` is a module-level `dict` mutated by multiple async handlers (`synthesise_file`, `serve_audio`, `_purge_expired`). Python's GIL makes individual dict operations atomic, but the read-then-modify pattern in `_purge_expired` (iterate, collect expired keys, pop) is not atomic relative to concurrent writes from `synthesise_file`. In CPython this is safe in practice due to the GIL, but it is fragile — especially under asyncio where `await` points allow interleaving.

**Effect:** Low-severity: unlikely to corrupt state but `asyncio.to_thread` calls (if added for blocking I/O) would make this truly unsafe.

---

**D-L3 — `SECRET_KEY` fallback to hardcoded dev string if env var missing**

File: `/work/apps/panel/src/emf_panel/main.py`, line 33

```python
_session_secret = os.environ.get("SECRET_KEY", "dev-session-key-replace-in-prod")
```

This diverges from the pydantic-settings `Settings` model (which requires `secret_key` with no default, raising on missing). If `SECRET_KEY` is absent from the environment, `Settings()` will raise, but `_session_secret` will silently use the hardcoded fallback. The session middleware is initialised before the lifespan (where `Settings()` is called), so in a misconfigured prod container the sessions use a known-weak key while the app appears to start normally.

**Fix:** Derive `_session_secret` from `get_settings().secret_key` inside the lifespan, or move middleware setup into the lifespan after settings validation.

---

**D-L4 — `mark_acked` has a missing `session.commit()` in the early-return path**

File: `/work/apps/router/src/router/alert_router.py`, lines 258–261

```python
case_row = await session.get(CaseRouterView, uuid.UUID(str(case_id)))
if case_row is None:
    await session.commit()
    return None, []
```

This path commits the ACK state updates that were executed before this line (lines 230–255). This is intentional. However the session is passed in from the caller (e.g. `signal_webhook`, `email_ack`, `internal_ack`), and those callers do not call `session.commit()` after `mark_acked` returns — they rely on `mark_acked` to commit. If `case_row is not None`, the function reaches line 274: `await session.commit()`. So the commit only happens in both branches. This is correct, but subtle — the session management is split across caller and callee, making it easy to break if `mark_acked` is refactored.

**Effect:** Not a current bug, but a fragile pattern. A future refactor that moves the commit could cause silently uncommitted ACK state updates.

---

#### INFO

**D-I1 — `Settings.get_settings()` instantiates a new `Settings` object on every call**

Files: `/work/apps/panel/src/emf_panel/settings.py:27`, `/work/apps/form/src/emf_form/settings.py:14`, `/work/apps/router/src/router/main.py:115`

None of the `get_settings()` functions use `@lru_cache`. Every FastAPI request that injects `settings` via `Depends(get_settings)` creates a new `Settings()` instance, which reads environment variables fresh. This is cheap but means `.app_config` (a property that reads `config.json`) is re-read on every `Depends(get_settings)` call. FastAPI caches `Depends` results per-request, not globally, so in a single request with multiple `Depends(get_settings)` the cost is paid multiple times.

**D-I2 — `resend.api_key` is set as a module-level global side effect in `__init__`**

File: `/work/apps/router/src/router/channels/email.py`, lines 58–59

```python
if resend_api_key:
    resend.api_key = resend_api_key
```

This mutates a module-level global in the `resend` library. If multiple `EmailAdapter` instances were created with different API keys (unlikely in current code but possible in tests), the last one wins. Tests that create an `EmailAdapter` with a dummy key will pollute the module global for subsequent tests in the same process.

---

## Penetration Tester

### Summary

Static analysis of all five services plus infra config. No live stack available — all findings are code and config derived. Severity follows CVSS v3.1 qualitative scale. OWASP references map to OWASP Top 10 2021.

---

### CRITICAL

#### PT-C1 — Session Cookie `https_only=False` — Hijacking Over HTTP (OWASP A05)

**File:** `apps/panel/src/emf_panel/main.py:38`

```python
app.add_middleware(SessionMiddleware, secret_key=_session_secret, https_only=False, same_site="lax")
```

`https_only=False` means the `session` cookie is **never marked `Secure`**. Any HTTP request (redirect loop, mixed-content load, pre-TLS navigation) transmits the session cookie in cleartext. At a festival with open Wi-Fi, passive sniffing trivially steals staff sessions containing OIDC claims including PII (`name`, `email`, `groups`).

**PoC:** `tcpdump -i wlan0 -A 'tcp port 80'` → capture `Cookie: session=<base64>` → decode with `itsdangerous` + known `SECRET_KEY`.

**Fix:** `https_only=True`. Caddy already enforces HTTPS in prod; this flag must match.

---

#### PT-C2 — Hardcoded Weak Default `SECRET_KEY` (OWASP A02)

**File:** `apps/panel/src/emf_panel/main.py:33`

```python
_session_secret = os.environ.get("SECRET_KEY", "dev-session-key-replace-in-prod")
```

If `SECRET_KEY` is unset or `.env` fails to load, the panel starts with a known, public default. Any attacker who reads the source can forge session cookies and dispatcher HS256 JWTs. `.env-example` has `SECRET_KEY=changeme` — if an operator skips `generate_secrets.py`, all auth is forgeable.

**PoC:**
```python
from itsdangerous import URLSafeTimedSerializer
s = URLSafeTimedSerializer("dev-session-key-replace-in-prod")
cookie = s.dumps({"user": {"sub": "attacker", "groups": ["team_conduct"]}})
# Cookie: session=<cookie> → full panel access
```

**Fix:** Fail hard at startup if `SECRET_KEY` equals the default or is shorter than 32 bytes.

---

### HIGH

#### PT-H1 — Unverified `userinfo` Claims Used for Group Membership (OWASP A07)

**File:** `apps/panel/src/emf_panel/routes.py:143–149`

```python
if token.get("id_token"):
    try:
        parsed = await oauth.emf.parse_id_token(request, token)
        id_claims = dict(parsed) if parsed else {}
    except Exception:
        log.warning("id_token parse failed; falling back to userinfo only")
user = {**id_claims, **(token.get("userinfo") or {})}
```

`parse_id_token` verifies signature + nonce. If it raises for **any reason** (clock skew, transient JWKS failure), the bare `except Exception` swallows the error and group membership falls back to unverified `userinfo` data. Additionally, `userinfo` entries overwrite `id_claims` values due to dict merge order — a rogue IdP or Docker-internal MITM could inject `groups: ["team_conduct"]`.

**Fix:** Catch only authlib-specific JWT errors, not bare `Exception`. Derive `groups` exclusively from verified `id_claims`.

---

#### PT-H2 — `verify_aud=False` on Bearer Token Validation (OWASP A07)

**File:** `apps/panel/src/emf_panel/auth.py:51–57`

```python
claims = jwt.decode(token, signing_key.key, algorithms=["RS256", "ES256"], issuer=issuer,
                    options={"verify_aud": False})
```

Audience validation is disabled. A JWT issued for **any other application** sharing the same UFFD/auth.emfcamp.org IdP passes validation if its `groups` claim includes `team_conduct`.

**Fix:** Set `audience=settings.oidc_client_id` and remove `verify_aud: False`.

---

#### PT-H3 — Dispatcher Token Revocation Lost on Process Restart (OWASP A07)

**File:** `apps/panel/src/emf_panel/dispatcher.py:9–10`

```python
_revoked: set[str] = set()
_active_sessions: dict[str, list[str]] = {}
```

Revocation state and device-session binding are in-process memory only. Any container restart/redeploy resets them: revoked tokens become valid again and device limits clear. A leaked dispatcher token that was explicitly revoked can be replayed after a restart to suppress incident visibility.

**Fix:** Persist revocation in Redis (already deployed). Add `dispatcher:revoked:{jti}` keys with TTL matching token `exp`.

---

#### PT-H4 — Attachment Upload: No Auth, No Case Existence Check, No Rate Limit (OWASP A01, A04)

**File:** `apps/form/src/emf_form/routes.py:290–342`

- No authentication — any anonymous user can upload files.
- No check that `case_id` exists in the DB before writing to disk. Arbitrary UUIDs fill `attachment_dir/` without bound.
- No rate limit on `/attachments` (only `/api/submit` is rate-limited at 15/10 s).

**PoC — disk exhaustion:**
```bash
for i in $(seq 1 10000); do
  curl -F "case_id=$(uuidgen)" -F "file=@image.png" https://report.emfcamp.org/attachments
done
```

**Fix:** Verify `case_id` exists in DB before accepting file. Add `@limiter.limit("5/minute")`. Gate behind a signed token issued at form submission.

---

#### PT-H5 — Signal Webhook Has No Authentication (OWASP A01)

**File:** `apps/router/src/router/main.py:257–292`

`POST /webhook/signal` accepts arbitrary Signal reaction bodies with no HMAC or shared secret. Any entity with Docker-network access can ACK any case by guessing/brute-forcing `message_id` (a timestamp string), silencing alert notifications.

**Fix:** Validate a shared webhook secret header or verify the sender against a known Signal group.

---

#### PT-H6 — Mattermost and Internal ACK Webhook Secrets Optional — Bypass When Empty (OWASP A01)

**Files:** `apps/router/src/router/main.py:355–363`, `apps/panel/src/emf_panel/settings.py:24`

```python
if settings.mattermost_webhook_secret and body.context.secret != settings.mattermost_webhook_secret:
    raise HTTPException(...)
```

When `MATTERMOST_WEBHOOK_SECRET` or `ROUTER_INTERNAL_SECRET` is empty (both default to `""` in settings), the auth check is skipped entirely. Anyone on the internal network can POST to `/webhook/mattermost/action` or `/internal/ack/{case_id}` to silently ACK any case.

**Fix:** Require non-empty secrets at startup; raise a configuration error if either is blank.

---

### MEDIUM

#### PT-M1 — Python 3 `except A, B:` Bug — `ValueError` Not Caught in Email ACK (OWASP A04)

**File:** `apps/router/src/router/main.py:309`

```python
except jwt.PyJWTError, ValueError:
```

In Python 3, `except A, B:` means `except A as B:` — catches **only** `jwt.PyJWTError` and assigns the exception to the name `ValueError`, shadowing the built-in. A `ValueError` from `uuid.UUID(str(payload["sub"]))` in `decode_ack_token` is **not caught** and propagates as HTTP 500. An attacker crafting a valid-signature JWT with a non-UUID `sub` receives a 500 response.

**Fix:** `except (jwt.PyJWTError, ValueError):`

---

#### PT-M2 — Form Rate Limit Too Permissive + Full Table Scan DoS (OWASP A04)

**File:** `apps/form/src/emf_form/routes.py:138, 210`

15 submissions/10 seconds per IP = 90 req/min. `get_remote_address` trusts `X-Forwarded-For`; the form service runs **without** `--proxy-headers`, so the effective key may be the reverse-proxy IP, removing per-user rate limiting entirely.

Every submission executes a full-table scan:
```python
existing_ids_result = await session.execute(select(Case.friendly_id))
```
This is O(n) and degrades linearly as cases grow. At 90 req/min an attacker rapidly accumulates cases, making each subsequent submission more expensive.

**Fix:** Reduce limit to 3–5/minute. Replace full scan with a retry-based single-row lookup. Apply edge rate limiting at Caddy.

---

#### PT-M3 — `ssl=prefer` for PostgreSQL Allows Cleartext Fallback (OWASP A02)

**File:** `shared/src/emf_shared/db.py:23`

```python
connect_args={"ssl": "prefer"},
```

`prefer` falls back to plaintext if the server declines SSL. A misconfigured container or Docker-network MITM receives a cleartext connection, exposing credentials and reporter PII silently.

**Fix:** Change to `ssl=require`.

---

#### PT-M4 — `/metrics` Unauthenticated on All Services, Swagger Unauthenticated in wolfcraig (OWASP A05)

All four services expose Prometheus metrics at `/metrics` without authentication. The form's `/metrics` is reachable externally via Caddy. Metrics reveal endpoint names, request rates, error rates, and latency histograms; panel metrics disclose when incidents are being actively managed.

The wolfcraig Caddyfile also exposes the Swagger UI unauthenticated (`swagger.emf.thisparish.org` with no auth middleware), revealing full API schemas for all internal services.

**Fix:** Block `/metrics` in the external Caddyfile or require bearer auth. Gate Swagger behind auth in prod deployments.

---

#### PT-M5 — Prod Caddyfile Missing TLS Minimum Version Snippet (OWASP A02)

**File:** `infra/caddy/Caddyfile.prod`

The prod Caddyfile imports `snippets/headers.caddy` but does **not** import `snippets/tls.caddy` (which enforces `min_version tls1.3`). TLS 1.0/1.1 connections may be accepted depending on Caddy build defaults.

**Fix:** Add `import snippets/tls.caddy` to the global block of `Caddyfile.prod`.

---

#### PT-M6 — Dispatcher Token Exposed in URL Query Parameter (OWASP A07)

**File:** `apps/panel/src/emf_panel/routes.py:749`

The dispatcher access JWT is passed as `?token=<jwt>` in the URL, appearing in server access logs, browser history, and `Referer` headers. Every sort/filter navigation preserves the token via `request.url.include_query_params`.

**Fix:** Exchange the token for a short-lived signed cookie on first authenticated load; redirect to a token-free URL.

---

#### PT-M7 — Unescaped Values in `HTMLResponse` Strings — Latent XSS (OWASP A03)

**File:** `apps/router/src/router/main.py:327–330`

```python
html = (
    f"<h1>✅ Acknowledged</h1>"
    f"<p>Case <strong>{alert.friendly_id}</strong> has been marked as acknowledged.</p>"
)
```

`alert.friendly_id` and similar fields (`acked_by`, `location_hint`) are interpolated directly into HTML response strings throughout the router and email adapter without `html.escape()`. Current wordlist-based IDs are safe, but this is a stored XSS sink if IDs ever incorporate user input.

**Fix:** Apply `html.escape()` to all user-derived values interpolated into HTML strings.

---

### LOW

#### PT-L1 — OIDC State Mismatch Silently Succeeds for Authenticated Users (OWASP A07)

**File:** `apps/panel/src/emf_panel/routes.py:134–137`

On OAuth state mismatch (CSRF indicator), already-authenticated users are silently redirected to `/` with no log entry. Mismatch should be logged as a security warning with the requesting IP.

**Fix:** Log the event at WARN level. Consider session invalidation on mismatch.

---

#### PT-L2 — AV Scanner Silent Bypass When ClamAV Unavailable (OWASP A04)

**File:** `apps/form/src/emf_form/routes.py:41–52`

ClamAV is an optional compose profile (`clamav`) — not running by default. When unreachable, the scan is silently skipped and uploads proceed unchecked.

**Fix:** Add `REQUIRE_AV=true` env flag for prod; reject uploads with HTTP 503 if AV is required but unreachable.

---

#### PT-L3 — Reporter PII Accessible to `backup_user` Role (OWASP A02)

`form_data JSONB` stores full reporter PII (`email`, `phone`, `name`, narrative). The `backup_user` DB role has `SELECT ON ALL TABLES IN SCHEMA forms`, giving any backup process full PII access. An unencrypted backup exfiltrates all reporter data.

**Fix:** Verify `scripts/backup.py` with `age` encryption is used for all backups and the recipient key is properly secured.

---

#### PT-L4 — Unbounded Optional Text Fields (OWASP A04)

**File:** `apps/form/src/emf_form/schemas.py:85–93`

Fields `additional_info`, `support_needed`, `outcome_hoped`, `others_involved`, `why_it_happened`, `anything_else`, `reporter.name`, and `reporter.camping_with` have no `max_length` constraint. Submissions can store megabytes per field up to uvicorn's 64 MB body default.

**Fix:** Add `max_length=5000` (or similar) to all optional free-text fields.

---

### Existing Security Test Coverage

**Covered by `tests/security/test_owasp.py`:**
- Unauthenticated panel redirects to `/login` (A01) ✓
- Non-conduct-group user gets 403 (A07) ✓
- Expired dispatcher token returns 401 (A07) ✓
- SQL injection in `what_happened` stored safely via ORM (A03) ✓
- Honeypot field silently drops without DB write (A04) ✓
- `uv.lock` files committed (A08) ✓
- Status transitions produce `CaseHistory` rows (A09) ✓
- SSRF: URL in `additional_info` stored, not fetched (A10) ✓
- Caddy CSP lacks `unsafe-eval` (A05) ✓
- `.env-example` uses placeholder values (A02) ✓

**Not covered by existing tests (gaps):**
- Session cookie `Secure` flag (`https_only=False`)
- `verify_aud=False` on bearer token validation
- Unverified `userinfo` fallback for group membership
- `except jwt.PyJWTError, ValueError:` bug in router
- Attachment upload without auth or case-existence check
- Dispatcher revocation reset on restart
- `/metrics` accessible externally without auth
- Missing rate limit on `/attachments`
- `ssl=prefer` DB connection downgrade
- Optional Mattermost/internal webhook secret bypass
- Dispatcher token in URL query parameter

---

### Live Testing

**Date:** 2026-07-11  
**Environment:** Docker network — form `172.18.0.7:8000`, panel `172.18.0.10:8001`, router `172.18.0.5:8002`, TTS `172.18.0.8:8003`  
**Note:** Database was unavailable during testing (`health` reported `"database": "error"`). All form POST submissions that reach DB-dependent code return HTTP 500 due to this, not application bugs. Findings below distinguish DB-caused failures from real vulnerabilities.

---

#### LT-1 — OpenAPI Docs / Swagger UI Exposed on All Services (OWASP A05) — CONFIRMED

All four services return HTTP 200 on `/docs`, `/openapi.json`, and `/redoc` with no authentication.

```
GET /docs        -> 200 (form, panel, router, tts)
GET /openapi.json -> 200 (all)
GET /redoc       -> 200 (all)
```

Full API surface disclosed including internal route schemas for `/internal/ack/{case_id}`, `/webhook/signal`, dispatcher session management, and TTS synthesis. Static analysis finding PT-M4 confirmed live.

---

#### LT-2 — `/metrics` Unauthenticated on All Services (OWASP A05) — CONFIRMED

All four services return Prometheus metrics at `/metrics` with no authentication or IP restriction.

```
GET /metrics -> 200 (form, panel, router, tts)
```

Form metrics expose request counts, error rates, and handler paths including submission failure rates. Panel metrics would expose case management activity patterns. No `Authorization` header required.

---

#### LT-3 — Panel Auth Bypass: All Vectors Correctly Rejected (OWASP A07) — PASS

Tested four attack vectors against `/api/v1/cases`:

| Vector | Expected | Result |
|---|---|---|
| No auth header | 303 redirect to /login | 303 ✓ |
| Garbage Bearer token | 401 | 401 ✓ |
| `alg:none` JWT with `groups:["team_conduct"]` | 401 | 401 ✓ |
| Bad signature RS256 JWT | 401 | 401 ✓ |

The `none` algorithm JWT (`eyJhbGciOiJub25lIn0.eyJzdWIiOiJhdHRhY2tlciIsImdyb3VwcyI6WyJ0ZWFtX2NvbmR1Y3QiXSwiaWF0Ijo5OTk5OTk5OTk5fQ.`) was correctly rejected. PyJWT's JWKS client refuses to validate alg:none tokens.

**Note:** The `/dispatcher` endpoint returns HTTP 422 with no `?token=` param, and 401 with a garbage token — correct.

---

#### LT-4 — Rate Limiting Active on Form Submit (OWASP A04) — PASS (with caveat)

Sent 50 rapid POST requests to `/api/submit`. Rate limiting (`15/10 seconds`) triggered correctly:

```
HTTP 429: 37 requests
HTTP 500: 13 requests  (DB down, not rate-limit bypass)
```

Rate limiting is active. However, `slowapi` uses `get_remote_address` which reads `X-Forwarded-For` — if the form service isn't behind a trusted proxy stripping/overwriting this header, an attacker can spoof the header to bypass per-IP rate limiting. Static analysis finding PT-M2 applies.

---

#### LT-5 — Security Headers Absent on All Services (OWASP A05) — CONFIRMED CRITICAL

No security headers present on any service response:

```
Content-Security-Policy:    MISSING (all services)
X-Frame-Options:            MISSING (all services)
X-Content-Type-Options:     MISSING (all services)
Strict-Transport-Security:  MISSING (all services)
Referrer-Policy:            MISSING (all services)
Permissions-Policy:         MISSING (all services)
```

The `server: uvicorn` header is present and discloses the application server. Without a CSP, any stored XSS in templates (e.g. PT-M7 router `friendly_id` interpolation) has no secondary mitigations. Without `X-Frame-Options` or `frame-ancestors` CSP, clickjacking is possible on the form.

**Note:** These headers should be set at the Caddy layer for external-facing endpoints. The prod Caddyfile imports `snippets/headers.caddy` which may set some of these — but they are absent on direct service hits, meaning any internal network actor or misconfigured proxy bypass leaves responses unprotected.

---

#### LT-6 — Session Cookie Missing `Secure` Flag (OWASP A05) — CONFIRMED

The panel session cookie observed live:

```
set-cookie: session=eyJ...; path=/; Max-Age=1209600; httponly; samesite=lax
```

`Secure` flag is absent. `httponly` and `samesite=lax` are present, which is partial protection. Confirms static finding PT-C1 / CR-2. The cookie contains base64-encoded OIDC state including `redirect_uri` with internal IP (`http://172.18.0.10:8001/auth/callback`).

---

#### LT-7 — OIDC `redirect_uri` Exposes Internal Docker IP (OWASP A05) — CONFIRMED

The `/login` redirect URL exposes the internal Docker IP:

```
Location: https://oidc.emf-forms.internal/default/authorize?...
  &redirect_uri=http%3A%2F%2F172.18.0.10%3A8001%2Fauth%2Fcallback
```

In production this would expose the internal container network address. This should be the public-facing URL, not the container IP.

---

#### LT-8 — CORS: No CORS Headers Set (No Wildcard, But No Restriction Either) — INFO

Sending `Origin: https://evil.com` with both GET and OPTIONS requests:

```
OPTIONS /api/submit (evil origin) -> 405 Method Not Allowed (no ACAO header)
GET / (evil origin) -> 200 OK (no ACAO header)
```

No `Access-Control-Allow-Origin` header is returned. This means browsers enforce same-origin policy by default — cross-origin JS cannot read responses. This is correct behaviour for a form submission endpoint. No wildcard CORS misconfiguration.

---

#### LT-9 — Path Traversal: Server Correctly Normalises or Rejects (OWASP A01) — PASS

Tested URL path traversal on panel attachment endpoint:

| Path | Result |
|---|---|
| `../../../etc/passwd` (client normalised) | 404 (curl resolved client-side to `/etc/passwd`, no route) |
| `%2e%2e%2f` encoded (server-side) | 404 (framework normalises) |
| `..%2F..%2F` mixed (server-side) | 404 |
| `%252e%252e%252f` double-encoded | 303 → /login (auth applied, route not found) |
| Direct `test.jpg` (legit path) | 303 → /login (auth correctly applied) |

The panel `serve_attachment` handler has an explicit guard (`if "/" in filename or ".." in filename: raise HTTPException(400)`). Starlette also normalises path traversal sequences at the router level. No traversal bypass found.

---

#### LT-10 — HTTP Verb Tampering: Non-Allowed Verbs Correctly Rejected — PASS

All tested non-standard verbs return HTTP 405 Method Not Allowed:

```
PUT/DELETE/PATCH/HEAD/OPTIONS/TRACE on /api/submit -> 405
PUT/DELETE/POST/PATCH/HEAD/OPTIONS/TRACE on /health -> 405
TRACE on form / and panel /health -> 405
```

No HTTP verb tunnelling or method override vulnerability found.

---

#### LT-11 — Error Responses: No Stack Trace Leakage — PASS (with server header caveat)

Error responses tested:
- Malformed JSON → 422 with structured Pydantic error (no traceback)
- Wrong `Content-Type` → 422 with validation detail
- Empty body → 422 with field-level error
- Invalid UUID → structured 404 `{"detail": "Not Found"}`
- 500 errors → plain `Internal Server Error` (no traceback)

No file paths, Python module names, or stack traces observed in error responses. The `server: uvicorn` response header discloses the ASGI server — minor info disclosure.

**Exception:** Pydantic 422 validation errors echo input values back in some cases:
```json
{"type":"string_too_long","msg":"String should have at most 10000 characters",
 "input":"AAAAAA...AAAA"}
```
A 100,000 character input is reflected (truncated in display but present in response). For large payloads this means the full input may be returned in the 422 body.

---

#### LT-12 — Input Boundary: Validation Correct, but Unicode/Null Bytes Return 500 — FINDING

Form submission boundary tests:

| Input | Result | Notes |
|---|---|---|
| Missing `what_happened` | 422 ✓ | Correct |
| Empty `event_name` | 422 ✓ | Correct |
| Whitespace-only `what_happened` | 422 ✓ | Stripped to empty, too short |
| 100,000 char `what_happened` | 422 ✓ | `max_length=10000` enforced |
| Emoji in `what_happened` (💀🔥) | **500** | Reaches DB layer, fails due to DB down |
| RTL override (U+202E) | **500** | Same as above |
| Null byte (`\x00`) | **500** | Same — but Pydantic/Python accepts null bytes in strings |
| Future date | 422 ✓ | Validator rejects correctly |

The 500s for emoji/RTL/null bytes are due to the database being down, not application crashes — the payloads pass Pydantic validation and reach the DB commit. However, **null bytes in text fields** deserves attention: Pydantic does not strip null bytes, and PostgreSQL will reject strings with null bytes in TEXT columns, which would cause a 500 in production. Applications should sanitise or reject `\x00` at the schema layer.

---

#### LT-13 — Signal Webhook Unauthenticated — CONFIRMED (OWASP A01)

`POST /webhook/signal` on the router returns HTTP 200 with no authentication:

```
POST http://172.18.0.5:8002/webhook/signal
Body: {"type": "message", "payload": {}}
Response: {"ok": true}  HTTP 200
```

No HMAC, shared secret, or IP allowlist. The endpoint processes Signal reaction webhooks (used to ACK cases). Confirms static finding PT-H5. In the test environment the router is directly reachable on the Docker network. In production it should only be reachable from the Signal daemon container via internal Docker networking, but there is no application-layer enforcement.

---

#### LT-14 — Router `/internal/ack` Secret Enforced in This Deployment — PASS (config-dependent)

```
POST /internal/ack/00000000-0000-0000-0000-000000000001 (no X-Internal-Secret)
Response: {"detail":"Forbidden"}  HTTP 403
```

`ROUTER_INTERNAL_SECRET` is configured in this deployment. However the default is `""` (empty string), and when empty the check is skipped — auth is only active when the secret is explicitly configured. The risk described in PT-H6 applies to any deployment that omits this variable.

---

#### LT-15 — XSS: Jinja2 Auto-Escaping Prevents Reflected XSS on Success Page — PASS

The `/success?friendly_id=<script>alert(1)</script>` URL reflects the payload HTML-escaped:

```html
<p class="reference-id">&lt;script&gt;alert(1)&lt;/script&gt;</p>
```

Jinja2 auto-escaping is active. No reflected XSS on the success page.

---

#### LT-16 — Dispatcher Token Exposed in URL Query Parameter — CONFIRMED (OWASP A07)

Confirmed live: `/dispatcher`, `/api/v1/dispatcher/cases`, `/api/v1/dispatcher/cases/{id}/ack`, and `/api/v1/dispatcher/cases/{id}/calls` all require `?token=<jwt>` in the URL. The token appears in all server access log lines and any `Referer` header sent on outbound navigation. Confirms static finding PT-M6.

---

#### LT-17 — TTS Service Unauthenticated Synthesis — INFO

`POST /synthesise` with arbitrary text returns audio with no authentication:

```
POST http://172.18.0.8:8003/synthesise
Body: {"text": "hello world", "urgency": "low"}
Response: [binary WAV/PCM audio]  HTTP 200
```

No auth required. The TTS service is an internal tool but is directly reachable on the Docker network. Abuse potential is limited (no PII, no state modification), but unauthenticated compute consumption is possible if the service is reachable from less-trusted network segments.

---

#### Summary: Live Test Results

| ID | Finding | Severity | Status |
|---|---|---|---|
| LT-1 | OpenAPI/Swagger exposed unauthenticated | Medium | CONFIRMED |
| LT-2 | `/metrics` unauthenticated all services | Medium | CONFIRMED |
| LT-3 | Panel auth bypass (none-alg JWT, garbage token) | Critical | PASS (not vulnerable) |
| LT-4 | Rate limiting on form submit | Medium | PASS (active, X-Forwarded-For caveat) |
| LT-5 | Security headers absent | High | CONFIRMED |
| LT-6 | Session cookie missing `Secure` flag | Critical | CONFIRMED |
| LT-7 | OIDC redirect_uri leaks internal IP | Low | CONFIRMED |
| LT-8 | CORS misconfiguration | — | NOT PRESENT |
| LT-9 | Path traversal on attachments | High | PASS (not vulnerable) |
| LT-10 | HTTP verb tampering | Medium | PASS (not vulnerable) |
| LT-11 | Error response info disclosure | Low | PASS (minor: server header, large 422 echoes) |
| LT-12 | Null bytes in text fields reach DB uncleaned | Low | NEW FINDING |
| LT-13 | Signal webhook unauthenticated | High | CONFIRMED |
| LT-14 | Router internal ACK secret | High | PASS in this env (config-dependent) |
| LT-15 | Reflected XSS on success page | High | PASS (Jinja2 escaping works) |
| LT-16 | Dispatcher token in URL | Medium | CONFIRMED |
| LT-17 | TTS unauthenticated | Low | INFO |

---

## Chaos Engineer

### Summary

Static analysis of resilience across all five services. No live stack available — all findings from code and compose config.

---

### Failure Mode Analysis

#### PostgreSQL — connection loss mid-request

**Severity: CRITICAL**

`emf_shared/db.py` creates a SQLAlchemy async engine with `pool_pre_ping=True`. Pre-ping detects stale connections before handing them to a session, so a mid-idle-period outage is recovered transparently on next request. However: if Postgres goes down *during* an active transaction (e.g. mid `session.flush()` in `submit_form`), the request raises an unhandled `asyncpg` exception which propagates as HTTP 500. The form returns an error to the user; the case is not persisted; the `pg_notify` is not fired. **No data loss guard** — reporter gets a 500 and may not retry. The panel and router behave identically: 500s on all DB-dependent endpoints.

- Retry logic: none on individual requests
- Circuit breaker: none
- Graceful degradation: none — all endpoints hard-fail
- Health check: `/health` reports `"database": "error"` but Docker Compose health check only covers Postgres itself, not app-layer DB reachability

**Gap:** no queue or write-ahead buffer to absorb submissions while DB is temporarily unavailable.

#### PostgreSQL — startup (dependency ordering)

**Severity: HIGH**

`form`, `panel`, and `msg-router` all `depends_on: postgres: condition: service_healthy`. Postgres health check (`pg_isready`) passes before `init.sql` role creation completes in some edge cases (pg_isready responds as soon as the postmaster accepts connections, before `docker-entrypoint-initdb.d` scripts finish). If a service starts and attempts a query before roles exist, it fails with an auth error and won't retry — `uvicorn` exits and Docker restarts it (since `restart: unless-stopped`). The restart loop recovers but adds latency.

- `tts` has **no** `depends_on` at all — starts in parallel with everything; isolated so not dangerous but inconsistent.
- Signal-api and redis also have no health-check-gated `depends_on` in compose.

#### pg_notify listener — connection loss

**Severity: CRITICAL**

`listener.py` runs `listen_for_cases` as an infinite loop. On connection drop it catches all exceptions and sleeps 5 s before reconnecting. This is correct but has a critical gap: **`conn.is_closed()` polling every 5 s is the only liveness check.** asyncpg does not guarantee `is_closed()` returns `True` immediately after a network partition; on a TCP half-open connection (e.g. firewall silently drops packets), the socket may appear open indefinitely. The listener would sit silent — not dead, not reconnecting — missing all `new_case` notifies until the TCP timeout fires (default: minutes to hours depending on OS `tcp_keepalive` settings). Cases submitted during this window get no notifications on any channel.

- Retry: yes, 5 s, but only after exception surfaces
- Backoff: none (fixed 5 s)
- Circuit breaker: none
- **No alerting** on listener silence
- **No watchdog**: `asyncio.create_task(_handle_new_case(...))` is fire-and-forget; exceptions in the task are caught and logged but the outer listener loop continues regardless
- **TCP keepalive not configured** on the asyncpg connection

#### pg_notify listener — missed notifies during reconnect

**Severity: HIGH**

Between listener disconnect and reconnect there is a race: if a case is submitted and `pg_notify('new_case', ...)` fires while the listener is in the 5 s sleep or re-connecting, the notify is **permanently lost** — PostgreSQL does not queue LISTEN notifications for disconnected clients. The retry mechanism in `_send_with_retry` (4 attempts at 0/5/10/15 min) only helps if the notification is received in the first place.

The `retrigger_case` channel via the panel's `/api/v1/cases/{case_id}/calls` endpoint provides a manual recovery path, but requires human intervention.

#### OIDC provider unreachable — panel login

**Severity: HIGH**

`auth.py` calls `oauth.emf.authorize_redirect()` on `/login`, which triggers authlib's lazy metadata fetch (fetches `/.well-known/openid-configuration`). If the OIDC provider is unreachable at redirect time, authlib raises an unhandled connection error that surfaces as HTTP 500.

However: **existing sessions are unaffected** — `require_conduct_team` reads from `request.session["user"]` (server-side cookie-backed) and from cached JWKS. `PyJWKClient` is wrapped with `@lru_cache` and `cache_keys=True`, so bearer token validation continues working with cached keys.

- New logins: hard fail with 500
- Existing sessions: continue working
- Bearer token (API) auth: continues working while JWKS cache is warm; fails on cache miss if OIDC unreachable
- **No timeout configured on authlib metadata fetch** — a slow OIDC provider can hang the login handler indefinitely, consuming a worker

#### Mattermost webhook fails

**Severity: MEDIUM**

`MattermostAdapter._send_posts_api` falls back to `_send_webhook` if the Posts API fails. If the webhook also fails, `send()` returns `None`. `_send_with_retry` treats `None` as failure, retries 3 more times (at +5, +10, +15 min), then marks notification `FAILED` and logs an error. No alerting beyond log line.

**Fixed retry delays are wall-clock `asyncio.sleep`** — if the router process restarts mid-retry sequence, pending retries are lost. PENDING notifications in the DB are never retried on restart; only a new `pg_notify` (or manual `retrigger_case`) would re-trigger routing.

#### TTS service crashes

**Severity: LOW**

TTS is only used by the Jambonz telephony adapter. If TTS is down, telephony calls fail. Telephony is optional (`phone_mode: disabled` by default). `EMFPhoneAdapter.is_available()` checks the Jambonz API directly, not TTS, so TTS failure surfaces only when a call is attempted and the audio URL returns 404.

TTS has no `depends_on` in compose. `_audio_files` is an in-memory dict — restart loses all generated audio tokens. Jambonz callers holding a token URL get 404 after TTS restart.

`_run_piper` spawns a subprocess with **no timeout** — a hung Piper process blocks the async handler indefinitely.

#### Jambonz telephony fails

**Severity: MEDIUM**

`EMFPhoneAdapter.send()` iterates targets sequentially with configurable `delay_seconds` between them. The outer timeout is 90 s per target (httpx). If Jambonz is unreachable, all targets fail; the method returns `None`. Because `send_with_retry` still runs, 3 more retry attempts happen at 5/10/15 min intervals, each waiting up to 90 s per target × N targets. This blocks the coroutine for a long time per retry cycle.

No circuit breaker — every notification attempt unconditionally contacts Jambonz.

#### Service restart mid-request

**Severity: MEDIUM**

`restart: unless-stopped` recovers services but:

1. **In-flight requests are dropped** — no graceful shutdown timeout configured in Docker Compose; tasks created with `asyncio.create_task` (fire-and-forget in `_route_event_time`) are cancelled on shutdown
2. **Pending retry sequences** in `_send_with_retry` (sleeping `asyncio.sleep`) are cancelled on shutdown — notifications stuck in `PENDING` state in the DB, never retried
3. **Listener reconnects** cleanly after 5 s; notifies fired during gap are lost

#### Disk full — Postgres volume

**Severity: HIGH**

Postgres will refuse writes. All form submissions fail (500). Panel status updates fail. Router notification state updates fail (notifications stuck PENDING). No capacity monitoring in compose (Prometheus/Grafana is opt-in `--profile monitoring`). No alerting on disk usage.

Attachment uploads will also fail with 500 if the attachment volume fills; no disk-space pre-check before `dest.write_bytes(header + rest)`.

#### Memory exhaustion

**Severity: MEDIUM**

No memory limits set on any container in `docker-compose.yml`. Notable risks:

- TTS: `_run_piper` reads entire audio into memory (`stdout`), no subprocess streaming. Multiple concurrent requests multiply this.
- Form: attachment upload reads entire file body into memory (`header + rest`) before writing to disk — 10 MB limit per file but no container memory limit.

A memory spike triggers OOM-kill with no grace period.

---

### Single Points of Failure

| Component | SPOF? | Notes |
|---|---|---|
| PostgreSQL | **YES** | Single instance, named volume. No replica, no standby. Loss = total outage. |
| pg_notify listener | **YES** | Single goroutine per router instance; silent failure on TCP half-open. |
| Email (SMTP/Resend) | YES | Single SMTP relay or Resend API; no fallback MTA. |
| Redis | **YES** | Single instance; assignee list and dispatcher session state lost on restart. Panel assignment endpoint throws unhandled 500 when Redis is down. |
| Caddy/TLS | YES | Single reverse proxy; TLS terminates here only. |
| OIDC provider | Partial | New logins fail; existing sessions survive cache. |
| Mattermost | No | Fails gracefully with retry; fallback webhook. |
| Signal API | No | Optional; falls back to email. |
| TTS | No | Optional path; telephony degrades if TTS down. |

**Redis** deserves special attention: `panel/routes.py` calls `await redis.sadd(...)` and `await redis.smembers(...)` with no try/except. If Redis is unavailable, the first await raises and propagates as HTTP 500 on `PATCH /api/v1/cases/{case_id}/assignee` and `GET /api/v1/assignees` — **panel case assignment breaks silently when Redis is down**.

---

### Docker Compose Health Checks and Restart Policies

| Service | `restart` | Health check |
|---|---|---|
| postgres | **not set** | `pg_isready` every 10 s, 5 retries |
| form | `unless-stopped` | **none** |
| panel | `unless-stopped` | **none** |
| msg-router | `unless-stopped` | **none** |
| tts | `unless-stopped` | **none** |
| redis | `unless-stopped` | `redis-cli ping` every 10 s |
| clamav | not set (profile) | `clamdcheck` every 30 s |

No health checks on `form`, `panel`, `msg-router`, or `tts` — Docker reports them as `Up` even if the FastAPI process has crashed internally. The `/health` endpoints exist in code but are not wired to Docker healthchecks.

`postgres` has **no `restart` policy** — if the Postgres container crashes it will not restart automatically. This is the single biggest availability gap in the compose file.

---

### Chaos Experiment Designs

#### Experiment 1 — PostgreSQL hard kill during form submission

**Hypothesis:** Submitting a report while Postgres is killed mid-transaction results in HTTP 500 to the user but no partial/corrupt data. Service recovers within 60 s.

**Inject:**
```bash
# Tab 1: hammer submissions
while true; do
  curl -s -X POST https://report.emf-forms.internal/api/submit \
    -H 'Content-Type: application/json' \
    -d '{"event_name":"EMF2026","urgency":"medium",...}' &
done

# Tab 2: kill Postgres after ~2 s
sleep 2 && docker compose -f infra/docker-compose.yml kill postgres
```

**Observe:**
- HTTP response codes on in-flight requests
- `emf_form` container logs for stack traces
- DB state after restart: partial rows in `forms.cases`
- Time from postgres restart to form accepting submissions again

**Success:** No partial/corrupt rows; form recovers within 60 s; all errors are 500, not silent data loss.

**Failure:** Partial rows inserted; form stays erroring >60 s; notifications fire without corresponding DB row.

---

#### Experiment 2 — TCP partition on pg_notify listener (half-open)

**Hypothesis:** A firewall-silently-dropped connection to Postgres causes the listener to hang indefinitely without reconnecting, losing all notifies during the partition. **Expected outcome: FAILURE** — listener will hang silent for minutes to hours.

**Inject:**
```bash
# Drop packets from router to postgres with no RST (simulates silent firewall)
docker compose -f infra/docker-compose.yml exec msg-router \
  sh -c 'iptables -A OUTPUT -d $(getent hosts postgres | awk "{print \$1}") -j DROP'

# Submit several cases
for i in $(seq 1 5); do
  curl -s -X POST https://report.emf-forms.internal/api/submit ...
done

# Wait 30 s, observe whether notifications fire
sleep 30

# Restore
docker compose -f infra/docker-compose.yml exec msg-router \
  sh -c 'iptables -D OUTPUT -d $(getent hosts postgres | awk "{print \$1}") -j DROP'
```

**Observe:**
- `msg-router` logs: does listener log "Listener error; reconnecting"?
- `notifications` table: do PENDING rows appear and get sent after restore?
- Time from restore to listener reconnect

**Success:** Listener detects partition within 60 s (requires TCP keepalive), reconnects, processes cases.

**Failure:** Listener hangs silent >5 min; cases are permanently lost requiring manual `retrigger_case`.

**Fix:** Pass `server_settings={"tcp_keepalives_idle": "60", "tcp_keepalives_interval": "10", "tcp_keepalives_count": "3"}` to `asyncpg.connect()` in `listener.py`, or add a heartbeat `SELECT 1` inside the `while not conn.is_closed()` poll loop.

---

#### Experiment 3 — OIDC provider blackholed

**Hypothesis:** New panel logins fail with 500 but existing sessions continue working; bearer-token API callers continue working while JWKS cache is warm.

**Inject:**
```bash
docker compose -f infra/docker-compose.yml exec panel \
  sh -c 'iptables -A OUTPUT -d $(getent hosts mock-oidc | awk "{print \$1}") -j DROP'
```

**Observe:**
- `/login` — expect 500 (unhandled connection error in authlib)
- Panel UI for already-logged-in user — expect 200 (session cookie valid)
- `GET /api/v1/cases` with Bearer token — expect 200 while JWKS cache warm; then 500 on cache miss

**Success:** Existing sessions survive; bearer auth survives cache TTL; clear 500 on new login.

**Failure:** Existing sessions broken; panel unusable for logged-in team.

---

#### Experiment 4 — Redis down, panel assignment

**Hypothesis:** Panel case assignment fails with unhandled 500 when Redis is unavailable; other panel functions continue.

**Inject:**
```bash
docker compose -f infra/docker-compose.yml stop redis
```

**Observe:**
- `PATCH /api/v1/cases/{id}/assignee` — expect unhandled 500 (`redis.exceptions.ConnectionError`)
- `GET /api/v1/assignees` — expect 500
- All other panel endpoints (`GET /`, case detail, status transitions) — expect 200 (no Redis dependency)

**Success:** Assignment endpoints fail clearly (500 or 503) with logged error; other panel functions unaffected.

**Failure:** 500 propagates silently without log; or other endpoints also break.

**Expected steady state:** FAILURE — Redis exception propagates as unhandled 500. Fix: wrap Redis calls in try/except with graceful degradation (assignee list returns `[]`; `sadd` silently skipped).

---

#### Experiment 5 — Notification retry sequence lost on router restart

**Hypothesis:** A notification in mid-retry (sleeping between attempts) is permanently lost when the router is restarted.

**Inject:**
```bash
# Block Mattermost so first send fails and retry sleeps begin
docker compose -f infra/docker-compose.yml exec msg-router \
  sh -c 'iptables -A OUTPUT -d $(getent hosts mattermost | awk "{print \$1}") -j DROP'

# Submit a case, wait for first attempt to fail and retry sleep to start
curl -X POST https://report.emf-forms.internal/api/submit ...
sleep 10

# Restart router while retry delay is in progress
docker compose -f infra/docker-compose.yml restart msg-router
```

**Observe:**
- `notifications` table: PENDING rows after restart
- Whether PENDING notifications are ever retried without manual intervention
- Router logs after restart for any PENDING sweep

**Success:** Router restart recovers PENDING notifications (startup scan of PENDING rows, re-enqueues retries).

**Failure:** PENDING notifications remain stuck forever until manual `retrigger_case`.

**Expected steady state:** FAILURE — `_send_with_retry` is in-memory only; PENDING rows are orphaned on restart. Fix: on router startup, query `SELECT * FROM notifications WHERE state = 'pending'` and re-enqueue retry sequences.

---

#### Experiment 6 — Disk pressure on Postgres volume

**Hypothesis:** Disk-full on the Postgres volume causes all writes to fail with clear 500s and no silent data loss.

**Inject:**
```bash
docker compose -f infra/docker-compose.yml exec postgres \
  dd if=/dev/zero of=/var/lib/postgresql/data/spacefill bs=1M count=9999
```

**Observe:**
- Form submissions: expect 500 with DB error log
- Panel case updates: expect 500
- Postgres logs: `No space left on device`
- Docker health check: Postgres goes unhealthy

**Success:** Clear logged errors; no silent data loss; health check marks service unhealthy.

**Failure:** Silent swallowing of write errors; partial commits.

---

#### Experiment 7 — Memory exhaustion via concurrent TTS requests

**Hypothesis:** Many concurrent TTS synthesis requests exhaust container memory, causing OOM-kill.

**Inject:**
```bash
for i in $(seq 1 50); do
  curl -s -X POST http://tts:8003/synthesise \
    -H 'Content-Type: application/json' \
    -d "{\"text\": \"$(python3 -c 'print(\"conduct case alert \" * 50)')\"}" \
    > /dev/null &
done
wait
```

**Observe:**
- TTS container memory usage via `docker stats`
- OOM-kill in kernel log (`dmesg | grep -i oom`)
- Recovery time after container restart
- In-flight audio tokens lost on restart

**Success:** Container survives; memory bounded by container memory limit.

**Failure:** OOM-kill; container restarts; Jambonz calls fail.

**Expected steady state:** OOM-kill likely — no container memory limit, entire WAV read into memory per request, no concurrency cap.

---

### Key Recommendations (Priority Order)

1. **[CRITICAL]** Add TCP keepalive to the asyncpg listener connection (`server_settings` in `asyncpg.connect()` in `listener.py`), or add a heartbeat `SELECT 1` inside the `while not conn.is_closed()` poll loop. Without this, a TCP half-open partition silently kills the notification pipeline for hours.

2. **[CRITICAL]** On router startup, sweep for PENDING notifications and re-enqueue retry sequences. PENDING rows are currently orphaned on restart — a mid-event router restart silently drops all in-progress notifications.

3. **[HIGH]** Add `restart: unless-stopped` to the `postgres` service in `infra/docker-compose.yml` (currently missing — Postgres crash is permanent without manual intervention).

4. **[HIGH]** Wire `/health` endpoints to Docker `healthcheck` for `form`, `panel`, `msg-router`, and `tts`. Without this, Docker Compose cannot detect a hung or crashed FastAPI process.

5. **[HIGH]** Wrap Redis calls in `panel/routes.py` with try/except; degrade gracefully (assignee list returns `[]`; `sadd` silently skipped). Redis outage currently breaks panel case assignment entirely.

6. **[MEDIUM]** Add exponential backoff with jitter to `RETRY_DELAYS_MINUTES`. Fixed delays cause thundering herd on router restart when many notifications begin retrying simultaneously.

7. **[MEDIUM]** Add a timeout to the authlib OIDC metadata fetch to prevent hung login handlers when the OIDC provider is slow or unresponsive.

8. **[MEDIUM]** Set container memory limits in `docker-compose.yml` for `tts` (and all services) to prevent OOM cascades from taking down the host.

9. **[MEDIUM]** Add `asyncio.wait_for` timeout to `_run_piper` in `tts/main.py` to bound hung Piper subprocesses.

10. **[LOW]** Add Prometheus alerting rules (not just dashboards) for: listener silence >60 s, notification FAILED count >0, Postgres unhealthy, disk usage >80%.

11. **[INFO]** Consider a short-lived idempotent submission queue (Redis list or WAL) in the form service to absorb submissions during brief Postgres outages — directly relevant for a festival where a DB hiccup during peak hours loses conduct reports.

---

## Chaos Engineer

### Live Chaos Experiments

Conducted 2026-07-11 against live containers on the internal Docker network.

Services targeted:
- Form: `http://172.18.0.7:8000`
- Panel: `http://172.18.0.10:8001`
- Router: `http://172.18.0.5:8002`
- TTS: `http://172.18.0.8:8003`

**Pre-condition note:** At test time, the PostgreSQL database was unreachable from the form and router services (`/health` reported `"database": "error"`). This is a pre-existing infrastructure condition, not a result of the chaos tests. Form `POST /api/submit` therefore returned 500 throughout. All tests assess HTTP-layer resilience, rate limiting, and process stability — not DB correctness.

---

#### Experiment 1 — Steady State Baseline

**Hypothesis:** All four services are reachable and respond to GET /.

**What was done:**
```
curl -w "%{time_total}" http://<svc>:<port>/
POST /api/submit with a valid minimal payload
GET /health on form, router, tts
```

**Results:**

| Service | Path | Status | Time |
|---------|------|--------|------|
| form | GET / | 200 | 0.023s |
| panel | GET / | 303 | 0.002s |
| router | GET / | 404 | 0.002s |
| tts | GET / | 404 | 0.001s |
| form | POST /api/submit | 500 | 0.014s |
| form | GET /health | 200 | 0.012s |

Form health: `{"status":"degraded","checks":{"database":"error","clamav":"unavailable","safe_browsing":"not_configured"}}`

Router health: `{"status":"degraded","checks":{"database":"error","email":"ok","signal":"error"}}`

TTS health: `{"status":"ok","checks":{"piper_model":"ok","piper_bin":"ok"}}`

Panel returns 303 (redirect to OIDC login) — correct with no session. Router and TTS return 404 on GET / (no index route) but `/health` confirms both are alive.

**Verdict: PASS** — all four services reachable; 500 on submit is pre-existing DB condition.

---

#### Experiment 2 — Resource Exhaustion (200 Concurrent POSTs)

**Hypothesis:** Rate limiter engages before service saturates; service remains healthy after the burst.

**What was done:**
200 concurrent `POST /api/submit` with valid JSON, fired simultaneously via `asyncio.gather` + `httpx.AsyncClient(limits=httpx.Limits(max_connections=250))`.

**Results:**
- All 200 completed in **0.85s total**
- Status distribution: `{429: 186, 500: 14}`
- Response times: min=0.445s mean=0.641s max=0.819s
- Post-burst `/health` → 200
- Post-burst submit → 429 (rate limiter still active)

The 186 × 429 confirm `slowapi` rate limiting fired correctly (`15/10 seconds` per IP). The 14 × 500 are DB-error fallthrough for requests that passed the limiter gate before it tripped. No connection errors, no timeouts, no crashes.

**Verdict: PASS** — rate limiter absorbed 93% of the burst; service never crashed or hung.

---

#### Experiment 3 — Slow Client / Slowloris-Lite

**Hypothesis:** 20 connections stalling after partial HTTP headers do not prevent legitimate requests from completing.

**What was done:**
20 TCP sockets opened to port 8000. Each sent `POST /api/submit HTTP/1.1\r\nHost: 172.18.0.7\r\n` then held (no further bytes). After 2 seconds, a legitimate GET / and POST were issued.

**Results:**
- All 20 slow sockets opened
- Legitimate GET / → **200 in 0.008s** (no degradation)
- Legitimate POST → 429 (rate limiter, expected)

Uvicorn holds partial-header connections in the I/O layer without occupying async workers.

**Verdict: PASS** — slow clients did not starve legitimate traffic.

---

#### Experiment 4 — Large Payload Stress

**Hypothesis:** 10MB and 100MB request bodies are rejected quickly without crashing the process.

**What was done:**
- `POST /api/submit` with `Content-Type: application/json` and 10MB body of repeated bytes
- Same with 100MB body
- `/health` and valid submit checked after

**Results:**

| Payload | Status | Time |
|---------|--------|------|
| 10MB | 422 | 0.020s |
| 100MB | 422 | 0.162s |
| Post-recovery /health | 200 | — |

Both rejected with 422 `json_invalid` — FastAPI/Pydantic read the full body into memory then rejected it. No 413 returned because no `max_body_size` is configured at the ASGI/uvicorn layer.

**Finding:** Server reads entire body before rejecting. 100MB processed in 0.162s — fast individually, but concurrent large-body requests will spike memory. No explicit body size cap at the HTTP layer (the attachment limit only applies to `/attachments`).

**Verdict: PASS** — service survived; finding logged above.

---

#### Experiment 5 — Malformed Request Flood

**Hypothesis:** Malformed and truncated requests produce consistent 4xx; no 500s; service stays up.

**What was done:**
- 50 concurrent requests with varied malformed bodies (empty, `null`, `[]`, truncated `{{{`, HTML, string-not-object, invalid field types)
- 50 sequential requests with `Content-Length: 5000` but only ~18 bytes sent, connection held 3s

**Results — malformed JSON (50 requests):**
```
{422: 50}
```
Every malformed body returned 422. Zero 500s.

**Results — truncated with lying Content-Length (50 requests):**
```
50x TIMEOUT_server_waiting_for_rest_of_body
```
Server held each connection open waiting for the bytes promised by `Content-Length: 5000`. This is correct HTTP/1.1 behaviour but means an attacker can cheaply tie up server connections at the body layer — a body-layer variant of Slowloris. Uvicorn has no `client_body_timeout` equivalent.

**Verdict: PASS** — no 500s, no crashes. Truncated-body connection holding is a concern (logged as finding).

---

#### Experiment 6 — Connection Exhaustion (500 Idle TCP Connections)

**Hypothesis:** 500 idle TCP connections (no bytes sent) do not prevent new legitimate requests.

**What was done:**
`socket.create_connection(('172.18.0.7', 8000))` × 500, held open, nothing sent. Legitimate GET / then issued.

**Results:**
- All 500 sockets opened successfully
- Legitimate GET / → **200 in 0.009s**
- Service completely unaffected

Idle connections sit in the OS socket buffer; they do not consume async workers until a byte arrives.

**Verdict: PASS** — 500 idle connections caused zero observable impact.

---

#### Experiment 7 — TTS Hammering (20 Concurrent 1000-char Requests)

**Hypothesis:** TTS service either queues gracefully or shows heavy latency under 20 concurrent long requests, but recovers.

**What was done:**
20 concurrent `POST /synthesise` with 1000-character text payload (`asyncio.gather`).

**Results:**
- All 20 returned **200**
- Response times: min=34.4s mean=38.0s max=39.3s
- Post-hammer `/health` → 200

All synthesis jobs completed but each request waited 34–39 seconds. Piper runs effectively serially — each HTTP request blocks waiting for all prior syntheses. Under festival load with multiple simultaneous dispatcher TTS requests, this will produce client timeouts.

**Finding:** 38s mean latency under 20 concurrent requests. A jambonz telephony adapter with a 10–15s call timeout will time out while the server is still synthesising. Need explicit concurrency limit with 429 backpressure, or async task/polling pattern.

**Verdict: PASS** (all requests eventually succeeded, health OK) — but severe latency finding logged.

---

#### Experiment 8 — Recovery Check

**Hypothesis:** After all chaos experiments, all services return to baseline response times and health.

**What was done:**
2-second cooldown, then GET / on all four services, POST /api/submit, GET /health on form.

**Results:**

| Service | Status | Time | vs Baseline |
|---------|--------|------|-------------|
| form | 200 | 0.008s | 0.33× (faster — warm JIT) |
| panel | 303 | 0.003s | ~1.5× |
| router | 404 | 0.003s | ~2× |
| tts | 404 | 0.003s | ~2× |
| form submit | 500 | 0.021s | pre-existing DB |

All services responsive. Form response time improved post-chaos (warm bytecode cache from burst traffic).

**Verdict: PASS** — full recovery confirmed.

---

### Live Chaos Summary

| Test | Hypothesis | Verdict | Key Finding |
|------|-----------|---------|-------------|
| 1. Steady state | All 4 services reachable | PASS | DB pre-down; TTS/router have no GET / route |
| 2. 200 concurrent POSTs | Rate limiter engages | PASS | `slowapi` fired 186×429; 14×500 from DB |
| 3. Slowloris-lite (20 conns) | Normal traffic unaffected | PASS | Uvicorn handles partial-header stalls well |
| 4. 10MB / 100MB payloads | 4xx, no crash | PASS | No body-size cap at ASGI layer; full body read into memory |
| 5. Malformed + truncated | Consistent 4xx | PASS | Truncated Content-Length holds conns indefinitely |
| 6. 500 idle TCP conns | Legit reqs get through | PASS | No observable impact |
| 7. 20x TTS 1000-char | Recovers after load | PASS | 34-39s/request latency under concurrency — telephony risk |
| 8. Recovery | Baseline response times | PASS | All services healthy post-chaos |

**Overall: 8/8 PASS** — HTTP layer is robust. No crashes, hangs, or data corruption observed under any test. All failure modes were graceful (4xx) or pre-existing (DB down).

### Actionable Findings from Live Tests

1. **[HIGH]** No `max_body_size` limit at the uvicorn/ASGI layer — server reads entire request body into memory before rejecting invalid JSON. Add `client_max_body_size` at Caddy (e.g. `64KiB` for `/api/submit`) or pass `--limit-concurrency` to uvicorn.

2. **[HIGH]** TTS synthesis is effectively serialised: 20 concurrent 1000-char requests each waited 34-39s. A jambonz telephony adapter with a 10-15s call timeout will silently time out. Add explicit concurrency control with 429 backpressure, or an async task/polling pattern so callers are not blocked.

3. **[MEDIUM]** Truncated `Content-Length` bodies hold server connections open indefinitely (body-layer Slowloris variant). Mitigate with a Caddy `read_timeout` directive and uvicorn `--timeout-keep-alive` tuning.

4. **[INFO]** Rate limiter (`slowapi`) is correctly configured and fires. Post-burst, legitimate same-IP submissions get 429 until the 10-second window clears. The `15/10 seconds` limit is generous for a form that should be a rare deliberate act — consider `5/60 seconds` per IP for the festival context.

---

### Live Testing

Live tests run against:
- Form: `http://172.18.0.7:8000`
- Panel: `http://172.18.0.10:8001`
- Router: `http://172.18.0.5:8002`
- TTS: `http://172.18.0.8:8003`

**Stack status at test time:** All services up. Database `error` on all services (form, panel, router health checks all report `"database":"error"`). ClamAV `unavailable`. Safe Browsing `not_configured`. TTS fully operational (piper_model and piper_bin both `ok`).

---

#### Test 1: Form Happy Path

**1a — GET /**

```
STATUS: 200  Content-Type: text/html; charset=utf-8  Size: 14,161 bytes
```
Page renders. Form element present. Static asset `/static/form.js?v=10` loads (200). No JS errors or stack traces in HTML. Event name dropdown populated (`EMF 2026` selected). Urgency dropdown populated. Active-event banner: "EMF is happening now."

**1b — GET /api/v1/config**

```
STATUS: 404  {"detail":"Not Found"}
```
No `/api/v1/config` endpoint exists. Config is embedded in rendered HTML only; not exposed as an API.

**1c — POST /api/submit (valid payload)**

```json
{
  "event_name": "EMF 2026",
  "reporter": {"name": "Test Reporter", "email": "test@example.com"},
  "what_happened": "A test incident occurred during the event for QA testing purposes.",
  "incident_date": "2026-07-10", "incident_time": "14:30:00",
  "urgency": "medium", "can_contact": true
}
```

```
STATUS: 500  Content-Type: text/plain; charset=utf-8  Body: Internal Server Error
```

Root cause: Database unreachable. Submission passes all validation but fails at `session.execute(select(Case.friendly_id))`. The 500 returns plain text with no JSON envelope — see Test 6 finding LT-04.

---

#### Test 2: Form Field Validation

**2a — Empty body**

```
STATUS: 422
{"detail":[
  {"type":"missing","loc":["body","event_name"],"msg":"Field required"},
  {"type":"missing","loc":["body","reporter"],"msg":"Field required"},
  {"type":"missing","loc":["body","what_happened"],"msg":"Field required"},
  {"type":"missing","loc":["body","incident_date"],"msg":"Field required"},
  {"type":"missing","loc":["body","incident_time"],"msg":"Field required"},
  {"type":"missing","loc":["body","can_contact"],"msg":"Field required"}
]}
```
All six required fields reported in a single response. Field-level `loc` arrays are accurate.

**2b — Invalid urgency (`"critical"`)**

```
STATUS: 422
{"detail":[{"type":"value_error","loc":["body","urgency"],
  "msg":"Value error, urgency must be one of ['high', 'low', 'medium', 'urgent']",
  "input":"critical"}]}
```
Clear error with allowed values listed.

**2c — `what_happened` at exactly 10,000 chars**

```
STATUS: 500  (DB error — Pydantic validation passed, hit database)
```
Exact max-length payload accepted. Boundary is inclusive.

**2d — `what_happened` at 10,001 chars**

```
STATUS: 422  {"detail":[{"type":"string_too_long","msg":"String should have at most 10000 characters"}]}
```
One char over limit correctly rejected.

**2e — Whitespace-only `what_happened` (`"          "`)**

```
STATUS: 422  {"detail":[{"type":"string_too_short","msg":"String should have at least 10 characters","input":""}]}
```
Whitespace stripped before length check — correct behaviour.

---

#### Test 3: Edge Cases

**3a — Unicode in text fields**

Payload with `"中文 emoji 👋 Arabic مرحبا accented café naïve"` in `what_happened`, `"Café Naïve Reporter"` as name:

```
STATUS: 500  (DB error — validation passed)
```
Unicode accepted without error by validation layer.

**3b — 500-char reporter name**

```
STATUS: 500  (DB error — validation passed)
```
`reporter.name` has no max-length constraint. 500-char names pass. **FINDING LT-09.**

**3c — Future incident date**

```
STATUS: 422  {"detail":[{"msg":"Value error, incident date cannot be in the future","input":"2027-01-01"}]}
```

**3d — Honeypot field (`website` populated)**

```
STATUS: 200  {"case_id":"de8230da-6a0c-4c0f-b83e-b480e9805396","friendly_id":"silent-drop"}
```
Honeypot path returns 200 (not 201) with fake UUID and `"friendly_id":"silent-drop"`. Silently discards bot submission — correct behaviour.

**3e — `media_links` with `ftp://` URL**

```
STATUS: 422  {"detail":[{"msg":"Value error, Each link must start with http:// or https://: 'ftp://malicious.com/evil'"}]}
```
Non-HTTP schemes rejected with clear message.

**3f — `can_contact: true` without email or phone (event-time)**

```
STATUS: 422  {"detail":"An email address or phone number is required when you have agreed to be contacted."}
```
Event-time branch detected correctly. Error is a plain string, not a list — see LT-11.

**3g — Unknown `event_name`**

```
STATUS: 422  {"detail":"Unknown event: EMF 9999"}
```
Domain-level check fires before DB access.

**3h — `can_contact: true` with phone only (event-time)**

```
STATUS: 500  (DB error — event-time contact check passed with phone alone)
```
Event-time logic correctly allows phone without email.

**3i — Invalid phone number (contains `@`)**

```
STATUS: 422  {"detail":[{"msg":"Value error, Phone number contains invalid characters. Allowed: digits, spaces, +, -, ., (, ), A-Z"}]}
```

**3j — Empty `location` object**

```
STATUS: 422  {"detail":[{"msg":"Value error, At least one of text, lat, or lon must be provided"}]}
```

**3k — `location.lat` out of range (91.0)**

```
STATUS: 422  {"detail":[{"msg":"Value error, lat must be between -90 and 90"}]}
```

---

#### Test 4: Attachment Upload

**4a — Valid JPEG (magic bytes `\xff\xd8\xff`, 112 bytes)**

```
STATUS: 500  Internal Server Error
```
Magic-byte check passes (JPEG signature detected). **FINDING LT-02:** Valid JPEG upload fails with 500. The upload endpoint has no DB dependency — likely a filesystem permissions issue on `/app/attachments` inside the container.

**4b — Zero-byte file with `.jpg` extension**

```
STATUS: 415  {"detail":"Only JPEG, PNG, GIF, and WebP images are accepted"}
```
Empty file has no magic bytes — correctly rejected.

**4c — Plain text file with `.jpg` extension (MIME mismatch)**

```
STATUS: 415  {"detail":"Only JPEG, PNG, GIF, and WebP images are accepted"}
```
Magic-byte detection ignores declared MIME type. Content sniffing works correctly.

**4d — File > 10 MB (JPEG header + 11 MB padding)**

```
STATUS: 413  {"detail":"File too large (max 10 MB)"}
```
Size limit check fires after magic-byte check, before filesystem write.

---

#### Test 5: Panel Unauthenticated Access

| Endpoint | Method | Status | Notes |
|---|---|---|---|
| `GET /api/v1/cases` | GET | 303 → `/login` | Redirects, not 401 |
| `GET /api/v1/cases/{uuid}` | GET | 303 → `/login` | Same |
| `PATCH /api/v1/cases/{uuid}/status` | PATCH | 303 → `/login` | Same |
| `GET /api/v1/dispatcher/cases` (no token) | GET | 422 `Field required` | Token is required query param |
| `GET /api/v1/dispatcher/cases?token=badtoken` | GET | 401 `Invalid or expired session token` | Correct |

**FINDING LT-03:** Panel auth returns HTTP 303 (See Other) with body `{"detail":"See Other"}` for API endpoints. API clients expect 401 Unauthorized, not a redirect. JSON body leaks HTTP status as a string. Programmatic consumers following the redirect will land at the OIDC login page.

---

#### Test 6: API Response Quality

**Content-Type consistency:**

| Scenario | Status | Content-Type |
|---|---|---|
| Form 422 (Pydantic) | 422 | `application/json` |
| Form 422 (HTTPException domain) | 422 | `application/json` |
| Form 500 (DB error) | 500 | `text/plain; charset=utf-8` — INCONSISTENT |
| Form health | 200 | `application/json` |
| Panel 303 redirect | 303 | `application/json` |
| TTS 422 | 422 | `application/json` |

**FINDING LT-04:** 500 responses from the form service return `text/plain` with body `Internal Server Error`. All other non-500 responses are `application/json`. FastAPI's default unhandled exception handler produces plain text — a custom 500 handler returning `{"detail":"Internal server error"}` with `application/json` would be consistent.

**Error structure inconsistency (FINDING LT-11):**
- Pydantic 422: `{"detail": [...]}` — `detail` is a **list** of objects with `loc`, `msg`, `type`
- HTTPException 422: `{"detail": "string"}` — `detail` is a **string**

Top-level key is consistent but the value type differs. Clients must handle both variants.

**Trace ID header:** All four services return `x-trace-id` on every response. Consistent.

---

#### Test 7: TTS Service

**Health:**
```json
{"status":"ok","checks":{"piper_model":"ok","piper_bin":"ok"},"version":"0.1.0"}
```
TTS is the only fully healthy service.

**POST /synthesise (short text "Hello world"):**
```
STATUS: 200  Content-Type: audio/wav  Size: 55,412 bytes
```

**POST /synthesise (empty `text: ""`):**
```
STATUS: 422  {"detail":"Provide either 'text' or 'friendly_id' + 'urgency'"}
```
Empty string treated as falsy — falls through to `friendly_id` check, then 422.

**POST /synthesise (no body fields):**
```
STATUS: 422  {"detail":"Provide either 'text' or 'friendly_id' + 'urgency'"}
```

**POST /synthesise (10,000-char text):**
```
STATUS: 200  Size: 239,220 bytes  Time: 3.55s
```
Text silently truncated to 500 chars (`MAX_TEXT_LEN = 500`). No error raised. **FINDING LT-06:** Over-limit text is silently truncated. Caller receives audio for shorter text with no indication. A 422 or `truncated: true` field would be more honest.

**POST /synthesise/file:**
```
STATUS: 200  {"audio_url":"/audio/<token>"}
```

**GET /audio/{valid_token}:**
```
STATUS: 200  Content-Type: audio/wav  Size: 454,652 bytes
```

---

#### Test 8: Router Service

**Health:**
```json
{"status":"degraded","checks":{"database":"error","email":"ok","signal":"error"},"version":"0.1.0"}
```
Email adapter healthy. Signal unreachable (expected).

**Exposed endpoints (from OpenAPI):**
- `POST /webhook/signal` — unauthenticated
- `GET /ack/{token}` — JWT-protected
- `POST /webhook/mattermost/action` — optionally secret-protected
- `POST /internal/ack/{case_id}` — `X-Internal-Secret` header required
- `GET /health`, `GET /metrics`

**POST /webhook/signal (no auth):**
```
STATUS: 200  {"ok":true}
```
Accepts unauthenticated POST from any host. Gracefully ignores non-reaction messages. **FINDING LT-07:** No authentication on signal webhook — fake ACKs possible from Docker network.

**GET /ack/badtoken:**
```
STATUS: 400  {"detail":"Invalid token"}
```

**POST /internal/ack/{uuid} (no X-Internal-Secret):**
```
STATUS: 403  {"detail":"Forbidden"}
```

**POST /webhook/mattermost/action (DB down):**
```
STATUS: 500  Internal Server Error
```
No graceful DB degradation. **FINDING LT-08.**

**FINDING LT-05 — Python 2 exception syntax bug (`router/main.py` line 309):**
```python
except jwt.PyJWTError, ValueError:
```
In Python 3 this is parsed as `except jwt.PyJWTError as ValueError:` — catches only `jwt.PyJWTError` and **shadows the builtin `ValueError`** inside the except block. `ValueError` exceptions from the try block (e.g., malformed UUIDs) are **not caught** and propagate as unhandled exceptions (500). Correct syntax: `except (jwt.PyJWTError, ValueError):`.

---

#### Test 9: OpenAPI Spec vs Actual Behaviour

| Check | Result |
|---|---|
| `/api/v1/cases/{id}/status` declared as `PATCH` | Matches ✓ |
| Form `/api/submit` returns 201 on success | Cannot verify (DB down) |
| Form `/attachments` returns `{"id":..,"case_id":..}` | Cannot verify (upload 500) |
| Panel API documents 401/403 responses | Not documented — gap |
| TTS `/synthesise` returns `audio/wav` | Matches ✓ |
| TTS text truncation behaviour | Not in spec |

**FINDING LT-10:** Panel OpenAPI spec has no `401`/`403` responses on any API endpoint. A consumer reading the spec would not know authentication is required.

---

#### Test 10: Concurrency

**5 simultaneous form submissions (valid payloads, DB down):**
All 5 returned 500 in 30–50 ms each. Uniform failure. No race conditions, timeouts, or server crashes.

**5 simultaneous validation-error requests (no DB touch):**
All 5 returned 422 in 11–12 ms each. Consistent, no race conditions. Validation layer is stateless.

---

#### Summary of Live Testing Findings

| ID | Severity | Finding |
|---|---|---|
| LT-01 | CRITICAL | All services: DB error — form submissions, panel reads, router ACKs all fail with 500 |
| LT-02 | HIGH | Valid JPEG upload returns 500 (unrelated to DB — likely `/app/attachments` filesystem permissions) |
| LT-03 | HIGH | Panel API returns 303 redirect (not 401) for unauthenticated JSON API requests |
| LT-04 | HIGH | Form 500 errors return `text/plain`, not `application/json` — breaks API clients |
| LT-05 | HIGH | Router bug: `except jwt.PyJWTError, ValueError:` line 309 — Python 3 only catches PyJWTError, not ValueError |
| LT-06 | MEDIUM | TTS silently truncates text >500 chars with no warning to caller |
| LT-07 | MEDIUM | Router `/webhook/signal` unauthenticated — fake ACKs injectable from Docker network |
| LT-08 | MEDIUM | Mattermost webhook 500s when DB is down — no graceful degradation |
| LT-09 | LOW | `reporter.name` has no max-length constraint — 500+ char names accepted |
| LT-10 | INFO | Panel OpenAPI spec omits 401/403 responses — auth requirement undocumented |
| LT-11 | INFO | 422 `detail` field is list (Pydantic) or string (HTTPException) — mixed types, clients must handle both |

---

## Live Retest (DB Up)

Retest date: 2026-07-11. Full stack up including PostgreSQL 17. Form at `http://172.18.0.7:8000`, Panel at `http://172.18.0.11:8001` (actual IP — note: provided IP `172.18.0.10` was wrong/stale), Router at `http://172.18.0.5:8002`, TTS at `http://172.18.0.8:8003`.

---

### Test 1 — Form Submission Happy Path

#### 1a — GET /health
- **Request:** `GET http://172.18.0.7:8000/health`
- **Response:** `200` `{"status":"ok","checks":{"database":"ok","clamav":"unavailable","safe_browsing":"not_configured"},"version":"0.1.0"}`
- **Result:** PASS — DB now up; ClamAV unavailable (not deployed); Safe Browsing not configured

#### 1b — First valid report submission
- **Request:** `POST /api/submit` with `event_name: "EMF 2026"`, `reporter.name: "Alice Smith"`, `reporter.email: "alice@example.com"`, `what_happened: "Someone was making loud threatening comments..."`, `incident_date: "2026-07-10"`, `incident_time: "22:30:00"`, `location.text: "Bar area near main stage"`, `urgency: "medium"`, `can_contact: true`
- **Response:** `201` `{"case_id":"81a000c9-085a-45bd-b1bd-effe3d7ce56d","friendly_id":"caucus-removes-viewer-cough"}`
- **Result:** PASS — 201 with case_id (UUID) and friendly_id (4-word slug)

#### 1c — Second valid report
- **Request:** `POST /api/submit` minimal fields, `can_contact: false`, no email
- **Response:** `201` `{"case_id":"2b457973-905c-4475-84bc-103e52c748b9","friendly_id":"fought-naomi-certain-davidson"}`
- **Result:** PASS

#### 1d — Third valid report with lat/lon location
- **Request:** `POST /api/submit` with `location: {"lat": 52.0397, "lon": -2.3783}`, phone number, `urgency: "high"`
- **Response:** `201` `{"case_id":"0f8e3c66-9d0c-46f5-b694-202a24be89d7","friendly_id":"either-rupert-march-virtual"}`
- **Result:** PASS — all friendly_ids unique across submissions

---

### Test 2 — Attachment Upload

#### 2a — Valid small JPEG
- **Request:** `POST /attachments?case_id=f1681b40-a41c-4863-9bd2-b3e1afba8cdc` multipart with 22-byte minimal JPEG (valid magic bytes `\xFF\xD8\xFF`)
- **Response:** `500 Internal Server Error` (text/plain body)
- **Result:** CONFIRMED-BUG (LT-02) — `docker-compose.yml` mounts no volume for `/app/attachments` in the `form` service. The container cannot create `case_dir.mkdir(parents=True, exist_ok=True)` on the ephemeral container filesystem (or the path doesn't survive). PNG also returns 500 identically. Root cause: **missing volume mount** for attachment storage, not filesystem permissions per se.

#### 2b — Zero-byte file
- **Request:** `POST /attachments?case_id=...` with 0-byte file named `empty.jpg`
- **Response:** `415` `{"detail":"Only JPEG, PNG, GIF, and WebP images are accepted"}`
- **Result:** PASS — correctly rejected before any filesystem write

#### 2c — Text file with .jpg extension
- **Request:** `POST /attachments?case_id=...` with text content, `.jpg` extension, `Content-Type: image/jpeg`
- **Response:** `415` `{"detail":"Only JPEG, PNG, GIF, and WebP images are accepted"}`
- **Result:** PASS — magic-byte detection correctly rejects MIME-spoofed file

---

### Test 3 — SQL Injection Confirmation

#### 3a — DROP TABLE payload
- **Request:** `POST /api/submit` with `reporter.name: "'; DROP TABLE cases; --"` and `what_happened: "'; DROP TABLE cases; -- this is an injection..."` (all text fields carrying SQLi)
- **Response:** `201` `{"case_id":"c7ff11d0-9c2d-44da-bed0-cd73c074d841","friendly_id":"dallas-humidity-flyer-believer"}`
- **Result:** PASS — stored safely via ORM parameterisation; DB still functional after submission

#### 3b — OR 1=1 tautology
- **Request:** `POST /api/submit` with `reporter.name: "' OR '1'='1"` in fields
- **Response:** `201` `{"case_id":"b46eba46-f02f-47de-ac3a-cfef9355a67a","friendly_id":"joshua-passes-girl-shame"}`
- **Result:** PASS — treated as literal string; SQLAlchemy ORM parameterisation confirmed working

#### 3c — information_schema probe
- **Request:** `POST /api/submit` with `"1; SELECT * FROM information_schema.tables"` in name and description
- **Response:** `201` `{"case_id":"ce27dbab-baf4-42b1-921f-4ade46966529","friendly_id":"murders-notions-snapchat-alma"}`
- **Result:** PASS — no SQL injection possible; subsequent form submissions confirm DB intact

---

### Test 4 — Null Byte Injection

#### 4a — Null byte in both name and description
- **Request:** `POST /api/submit` with `reporter.name: "Test\x00User"` and `what_happened: "Description with null byte\x00 here..."`
- **Response:** `500 Internal Server Error`
- **Result:** CONFIRMED-BUG — PostgreSQL rejects null bytes in TEXT columns; Pydantic does not sanitise them; unhandled exception produces 500

#### 4b — Null byte in reporter.name only
- **Request:** `POST /api/submit` with `reporter.name: "Test\x00User"`, clean `what_happened`
- **Response:** `500 Internal Server Error`
- **Result:** CONFIRMED-BUG — any field containing `\x00` causes 500; name is stored in `form_data` JSONB column, PostgreSQL TEXT still rejects null bytes in JSON strings

#### 4c — Null byte in what_happened only
- **Request:** `POST /api/submit` with normal name, `what_happened: "Description with null byte\x00 in what_happened..."`
- **Response:** `500 Internal Server Error`
- **Result:** CONFIRMED-BUG — confirms null byte bug is per-field, not combination. Any null byte in any text field causes unhandled 500. **This is the confirmed key bug from static analysis.**

---

### Test 5 — Panel API Auth Behaviour

> Note: Panel was not at `172.18.0.10` (connection refused). Found at `172.18.0.11:8001`.

#### 5a — GET /api/v1/cases unauthenticated, no Accept header
- **Request:** `GET http://172.18.0.11:8001/api/v1/cases`
- **Response:** `303 See Other` with `Location: /login` header, body `{"detail":"See Other"}`
- **Result:** CONFIRMED-BUG (LT-03) — API endpoint returns 303 redirect, not 401/403. `Accept: application/json` does not change this behaviour.

#### 5b — GET /api/v1/cases with Accept: application/json
- **Request:** Same as 5a with `Accept: application/json`
- **Response:** `303` `{"detail":"See Other"}` — identical to 5a
- **Result:** CONFIRMED-BUG — content negotiation ignored for auth redirect; JSON API clients will unexpectedly follow redirect

#### 5c — GET /api/v1/cases with garbage Bearer token
- **Request:** `GET /api/v1/cases` with `Authorization: Bearer garbage-token-xyz-12345`
- **Response:** `401` `{"detail":"Invalid or insufficient bearer token"}`
- **Result:** PASS — Bearer token path correctly returns 401 (not 303); shows two separate auth code paths: session cookie → 303, Bearer → 401

#### 5d — Follow 303 redirect to /login
- **Request:** `GET /api/v1/cases` with `-L` (follow redirect) → hits `/login`
- **Response:** `500 Internal Server Error` on `/login`
- **Result:** NEW-BUG — `/login` itself returns 500 (OIDC misconfiguration or missing `OIDC_SERVER_METADATA_URL` in this environment). Panel login flow is broken in current deployment.

---

### Test 6 — Mattermost Webhook

#### 6a — Valid ack for non-existent case
- **Request:** `POST http://172.18.0.5:8002/webhook/mattermost/action` `{"user_name":"testuser","context":{"action":"ack","case_id":"00000000-0000-0000-0000-000000000000","secret":""}}`
- **Response:** `200` `{"update":{"message":"Case not found"}}`
- **Result:** PASS — gracefully handles missing case without 500; DB up resolves LT-08

#### 6b — Unknown action
- **Request:** Same with `"action": "unknown"`
- **Response:** `200` `{"update":{"message":"Unknown action"}}`
- **Result:** PASS

#### 6c — Invalid case_id format
- **Request:** Same with `"case_id": "not-a-uuid"`
- **Response:** `400` `{"detail":"Invalid case_id"}`
- **Result:** PASS — correct input validation

---

### Test 7 — XSS Stored Test

#### 7a — XSS in reporter.name and what_happened
- **Request:** `POST /api/submit` with `reporter.name: "<script>alert(1)</script>"` and `what_happened: "<script>alert(document.cookie)</script> XSS test..."`
- **Response:** `201` `{"case_id":"c89cd030-dfed-42f2-a407-d8f9f60c3456","friendly_id":"findings-samsung-founding-existent"}`
- **Result:** INFO — stored as literal string; friendly_id contains no XSS payload (slug generator doesn't use user input). Panel UI rendering would need separate verification (panel /login is 500 in this env).

#### 7b — XSS via friendly_id query param on success page
- **Request:** `GET /success?friendly_id=<script>alert(1)</script>` (URL-encoded)
- **Response:** `200` HTML with `<p class="reference-id" aria-live="polite">&lt;script&gt;alert(1)&lt;/script&gt;</p>`
- **Result:** PASS — Jinja2 auto-escaping active; `{{ friendly_id }}` in `success.html` line 20 is properly HTML-escaped. Previous static analysis concern about `alert.friendly_id` in HTMLResponse is mitigated here. The template does NOT use `| safe` filter.

---

### Test 8 — Concurrent Submissions (Race Condition)

- **Request:** 10 simultaneous `POST /api/submit` via Python asyncio + httpx
- **Response:** All 10 returned `201`; 10 unique `friendly_id` values; 10 unique `case_id` values
- **Result:** PASS — no duplicates detected in this run. Note: the TOCTOU window in `generate_unique()` (fetch all IDs → generate → insert) still exists theoretically under very high concurrency; 10 concurrent requests did not trigger it. The `SELECT ... existing_ids` + UUID-seeded generation provides enough entropy to avoid practical collisions at this load.

---

### Test 9 — Rate Limiter

#### 9a — Rate limit fires
- **Request:** Sequential `POST /api/submit` from same IP
- **Response:** Requests 1–15 return `201`; request 16 returns `429` `{"error":"Rate limit exceeded: 15 per 10 second"}`
- **Result:** PASS — limit is `15/10 seconds` as configured in `@limiter.limit("15/10 seconds")`

#### 9b — Rate limit resets
- **After waiting 12 seconds:** `POST /api/submit` returns `201`
- **Result:** PASS — sliding window resets correctly

#### 9c — Rate limit headers
- **Request:** `POST /api/submit` (within limit)
- **Response:** No `X-RateLimit-*` headers present in response
- **Result:** INFO — no rate limit headers returned to clients; callers cannot proactively throttle

---

### Test 10 — Field Edge Cases

#### 10a — Unicode: accented, CJK, emoji, Arabic
- **Request:** `POST /api/submit` with `reporter.name: "café résumé 中文"`, `what_happened` containing `café, 中文字符, emoji 🔥, Arabic مرحبا`
- **Response:** `201` `{"case_id":"1cee7d6c-0ee8-481c-b4aa-1023e64f2f98","friendly_id":"ferguson-return-skate-blink"}`
- **Result:** PASS — full Unicode stored and accepted correctly

#### 10b — Max-length what_happened (10000 chars)
- **Request:** `POST /api/submit` with `what_happened` exactly 10000 chars
- **Response:** `201`
- **Result:** PASS — boundary value accepted

#### 10c — Over max-length what_happened (10001 chars)
- **Request:** `POST /api/submit` with `what_happened` of 10001 chars
- **Response:** `422` `{"detail":[{"type":"string_too_long","loc":["body","what_happened"],"msg":"String should have at most 10000 characters",...}]}`
- **Result:** PASS — Pydantic enforces max_length correctly

#### 10d — Whitespace-only in what_happened
- **Request:** `POST /api/submit` with `what_happened: "   \t\n   "`
- **Response:** `422` `{"detail":[{"type":"string_too_short","loc":["body","what_happened"],"msg":"String should have at least 10 characters","input":""}]}`
- **Result:** PASS — `strip_what_happened` validator strips whitespace to empty string, which then fails `min_length=10` check

#### 10e — Whitespace-only in reporter.name
- **Request:** `POST /api/submit` with `reporter.name: "   "`, valid `what_happened`
- **Response:** `201` — name stripped to `None` via `strip_str` validator
- **Result:** PASS — optional fields correctly strip-to-None

---

### Summary Table — Live Retest (DB Up)

| ID | Test | Result | Notes |
|---|---|---|---|
| RT-01 | Form health check | PASS | DB ok, ClamAV unavailable |
| RT-02 | Valid form submission ×3 | PASS | 201, unique friendly_ids, correct structure |
| RT-03 | Attachment upload — valid JPEG | CONFIRMED-BUG | 500; `/app/attachments` not mounted as volume in docker-compose; missing volume is root cause |
| RT-04 | Attachment upload — zero-byte file | PASS | 415 correctly rejected by magic-byte check |
| RT-05 | Attachment upload — text with .jpg extension | PASS | 415 correctly rejected |
| RT-06 | SQLi — DROP TABLE | PASS | 201; ORM parameterisation prevents injection |
| RT-07 | SQLi — OR 1=1 | PASS | 201; literal storage confirmed |
| RT-08 | SQLi — information_schema | PASS | 201; no data leakage |
| RT-09 | Null byte — any text field | CONFIRMED-BUG | 500; PostgreSQL rejects `\x00` in TEXT/JSONB; no sanitisation in Pydantic validators; affects `reporter.name`, `what_happened`, and all other text fields independently |
| RT-10 | Panel auth — no creds | CONFIRMED-BUG | 303 redirect to /login (not 401); consistent with DB-up or DB-down state |
| RT-11 | Panel auth — Accept: application/json | CONFIRMED-BUG | 303 still returned; Accept header ignored |
| RT-12 | Panel auth — garbage Bearer token | PASS | 401 returned correctly |
| RT-13 | Panel /login endpoint | NEW-BUG | 500 on `/login`; OIDC discovery broken in current deployment (missing `OIDC_SERVER_METADATA_URL` or OIDC provider unreachable) |
| RT-14 | Mattermost webhook — ack non-existent case | PASS | 200 graceful; DB-up resolves LT-08 |
| RT-15 | Mattermost webhook — unknown action | PASS | 200 graceful |
| RT-16 | Mattermost webhook — invalid case_id | PASS | 400 correct |
| RT-17 | XSS stored in submission | INFO | Stored as literal; friendly_id not attacker-controlled |
| RT-18 | XSS via success page ?friendly_id param | PASS | Jinja2 auto-escaping active; output is HTML-encoded |
| RT-19 | Concurrent submissions ×10 | PASS | All 201; no duplicate friendly_ids; no 500s |
| RT-20 | Rate limit fires at 15/10s | PASS | Triggers at request 16 as expected |
| RT-21 | Rate limit resets after window | PASS | 201 after 12s wait |
| RT-22 | No rate limit headers in response | INFO | Clients cannot proactively throttle |
| RT-23 | Unicode in all text fields | PASS | 201; CJK, emoji, Arabic all stored |
| RT-24 | Max-length boundary (10000 chars) | PASS | 201 |
| RT-25 | Over-max-length (10001 chars) | PASS | 422 with clear error |
| RT-26 | Whitespace-only in what_happened | PASS | 422 after strip-to-empty |
| RT-27 | Whitespace-only in reporter.name | PASS | 201; stripped to None (field optional) |

### New/Confirmed Bugs

| ID | Severity | Status | Finding |
|---|---|---|---|
| RT-BUG-01 | HIGH | CONFIRMED | Null byte (`\x00`) in any text field causes unhandled 500; PostgreSQL rejects it; no Pydantic validator strips null bytes. Fix: add `str.replace('\x00', '')` in strip validators, or a root `model_validator`. |
| RT-BUG-02 | HIGH | CONFIRMED | Attachment upload always 500; `/app/attachments` directory not created/mounted. Fix: add `volumes: - attachments_data:/app/attachments` in `docker-compose.yml` for `form` service. |
| RT-BUG-03 | HIGH | CONFIRMED | Panel API returns 303 (not 401) for unauthenticated cookie-session requests to JSON API endpoints. Fix: check `Accept` header; if `application/json`, return 401 instead of redirect. |
| RT-BUG-04 | MEDIUM | NEW | Panel `/login` returns 500 in this deployment. OIDC provider unreachable or `OIDC_SERVER_METADATA_URL` not set correctly. Blocks all panel authentication. |

---

## PostgreSQL Audit

### Summary table

| ID | Severity | Area | Finding |
|---|---|---|---|
| PG-01 | CRITICAL | RLS | `app.current_team_id` never set; all cases exposed to every panel user |
| PG-02 | HIGH | Schema | `case_history.id` type mismatch: schema UUID, both ORM models say Integer |
| PG-03 | HIGH | Security | `infra/.env` committed to repo with real credentials |
| PG-04 | HIGH | pg_notify | `pg_notify('new_case')` fires inside a transaction before commit; router can receive the notify before the row exists |
| PG-05 | MEDIUM | Schema | `friendly_id` collision check is O(n): full table scan on every form submission |
| PG-06 | MEDIUM | Migration | No migration files in `alembic/versions/`; Alembic exists but is inert; schema managed only via `docker-entrypoint-initdb.d/` |
| PG-07 | MEDIUM | Connection | `ssl=prefer` used; PG server not configured to require SSL; connection will silently fall back to plaintext inside Docker |
| PG-08 | MEDIUM | Transactions | Router `_send_with_retry` opens/closes 3–4 separate sessions per attempt with no outer transaction; interleaved failures can leave notifications in inconsistent state |
| PG-09 | LOW | Schema | No `updated_at` trigger on `forms.cases`; `updated_at` maintained only by ORM — direct SQL updates (e.g. from psql) silently skip it |
| PG-10 | LOW | Maintenance | No `postgresql.conf` tuning; stock `postgres:17-alpine` defaults; no autovacuum config for write-heavy workload |
| PG-11 | LOW | Backup | Backup is `pg_dump` via script (`scripts/backup.py`) with no WAL/PITR; RPO is as old as last backup run |
| PG-12 | LOW | Audit trail | `case_history` has no DELETE grant to anyone — effectively append-only by omission, not by constraint |
| PG-13 | LOW | RLS scope | RLS only on `forms.cases`; `case_history` and `notifications` have no RLS — anyone with `team_member` role can read all history and all notifications |
| PG-14 | INFO | Passwords | No user credentials stored in DB; auth is OIDC only. DB passwords are role passwords only (hashed by Postgres internally with scram-sha-256). |
| PG-15 | INFO | Mattermost | Mattermost uses `sslmode=disable` for its PG connection (`docker-compose.yml` line 189) |

---

### PG-01 — CRITICAL: RLS always matches `team_id IS NULL`, every panel user sees every case

**Evidence:**

`infra/postgres/00_roles.sql` lines 128–132:
```sql
CREATE POLICY team_isolation ON forms.cases
    USING (
        team_id IS NULL
        OR team_id = current_setting('app.current_team_id', true)::uuid
    );
```

`docker-compose.yml` line 57: panel connects as `team_member`.
`team_member` is subject to RLS (`FORCE ROW LEVEL SECURITY` is set, line 126).

**Nowhere in the codebase** is `SET LOCAL app.current_team_id` (or `SET app.current_team_id`) executed. A full grep of `/work/apps/` and `/work/shared/` confirms zero hits for `current_team_id`.

All inserted cases have `team_id = NULL` (the ORM models in `apps/form/src/emf_form/models.py` line 29 and `apps/panel/src/emf_panel/models.py` line 29 both define `team_id` as `nullable=True` with no default, and `apps/form/src/emf_form/routes.py` line 220 constructs `Case(...)` without setting `team_id`).

**Consequence:** The `team_id IS NULL` branch of the policy always matches. The RLS policy is structurally sound but completely inert: all authenticated panel users see all cases. Multi-team isolation (if ever intended) does not work.

**Fix:**
1. If multi-team isolation is wanted: set `app.current_team_id` at session start using a SQLAlchemy `event.listen` on `connect` or via `AsyncSession` `begin` hook, pulling the team UUID from the OIDC token's groups claim.
2. If single-team deployment is the only use case: the policy is functionally correct (all cases have `NULL` team_id, policy allows all). Document this explicitly and remove the dead `OR` branch to avoid future confusion.

---

### PG-02 — HIGH: `case_history.id` type mismatch — ORM says Integer, schema says UUID

**Evidence:**

`infra/postgres/00_roles.sql` line 45:
```sql
id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
```

`apps/form/src/emf_form/models.py` line 47:
```python
id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
```

`apps/panel/src/emf_panel/models.py` line 47 — identical.

**Consequence:** The ORM sends `DEFAULT` for `id` (autoincrement implies serial/sequence) and expects an integer PK back. Postgres has a UUID column with no sequence. Inserts of `CaseHistory` will fail at runtime with a type error or a missing sequence error as soon as the schema is initialised from the SQL file and the ORM tries to insert. The `api_case_history` endpoint at `apps/panel/src/emf_panel/routes.py` line 438 serialises `h.id` as a plain value — if it were an integer, the JSON would be numeric; if UUID, a string. The mismatch means either live data has never had history rows successfully inserted, or the schema was rebuilt to match the ORM at some point.

**Fix:** Align the two. Recommended: change the ORM to match the schema (UUID). Replace `Integer, primary_key=True, autoincrement=True` with `UUID(as_uuid=True), primary_key=True, default=uuid.uuid4` in both `apps/form/src/emf_form/models.py` and `apps/panel/src/emf_panel/models.py`.

---

### PG-03 — HIGH: `infra/.env` committed to repo with real credentials

**Evidence:** `/work/infra/.env` is present and tracked by git. It contains production-grade secrets including `TEAM_MEMBER_DB_PASSWORD`, `RESEND_API_KEY`, `SMTP_PASSWORD`, `EMF_PHONE_API_KEY`, `ROUTER_INTERNAL_SECRET`, `SECRET_KEY`, `OIDC_CLIENT_SECRET`, and `REDIS_PASSWORD`.

**Fix:** Immediately rotate all secrets in that file. Add `infra/.env` to `.gitignore`. Only `infra/.env-example` (with placeholder values) should be committed. The root `.env` is already gitignored; apply same treatment to `infra/.env`.

---

### PG-04 — HIGH: `pg_notify` fires before transaction commits; router can miss the row

**Evidence:** `apps/form/src/emf_form/routes.py` lines 240–245:
```python
await session.flush()                            # writes to DB but not committed
await session.execute(
    text("SELECT pg_notify('new_case', :payload)"),
    {"payload": str(case_id)},
)
await session.commit()                           # commit happens AFTER notify
```

`pg_notify` fires at the point the `SELECT pg_notify(...)` statement executes inside the transaction. The notification is delivered to listeners *when the transaction commits*, so strictly speaking the ordering here is safe in Postgres — a notification sent inside a transaction is only delivered to other backends after the transaction commits.

However, `apps/panel/src/emf_panel/routes.py` lines 679–683 (the `admin_trigger_call` endpoint that re-triggers an existing case) uses the same pattern:
```python
await session.execute(text("SELECT pg_notify('retrigger_case', :payload)"), ...)
await session.commit()
```

This is fine. The real risk is the `retrigger_case` path: the router's `_handle_new_case` (listener.py line 52) with `force=True` skips the existence check and calls `router.load_alert_from_db`. If an error causes the form's transaction to roll back after the `flush()` but before `commit()`, the notification is never sent (correct). The pattern is safe for the commit case but:

**Real issue:** The router runs `asyncio.create_task(_handle_new_case(...))` (listener.py line 35), which means notification handling is fire-and-forget with no back-pressure. If the router crashes or the task raises, there is no retry mechanism for the notification itself. Lost notifications mean silent failures with no re-delivery.

---

### PG-05 — MEDIUM: `friendly_id` uniqueness check is O(n) — full table scan on every submission

**Evidence:** `apps/form/src/emf_form/routes.py` lines 210–214:
```python
existing_ids_result = await session.execute(select(Case.friendly_id))
existing_ids: set[str] = set(existing_ids_result.scalars().all())
```

This fetches **every `friendly_id` in the table** into Python memory on every form submission to check for collisions. At festival scale (thousands of submissions), this is an unbounded full table scan + Python set construction on every request, serialised by the rate limiter at 15/10s peak but still O(n) per request.

`friendly_id` has a `UNIQUE` constraint (`00_roles.sql` line 27). The correct approach is to attempt the insert and catch `UniqueViolation`. Alternatively, generate the ID and check existence with a single `SELECT EXISTS(... WHERE friendly_id = ?)`.

**Fix:** Remove the `select(Case.friendly_id)` scan. Use `INSERT ... ON CONFLICT` or check-then-insert with the unique index. The `generate_unique` function in `shared/src/emf_shared/friendly_id.py` already accepts an `existing: set[str]` — change the call site to pass an empty set and rely on the DB constraint, retrying on `asyncpg.UniqueViolationError`.

---

### PG-06 — MEDIUM: No Alembic migration files; schema change path undefined

**Evidence:** `/work/apps/form/alembic/versions/.gitkeep` — the versions directory contains only a `.gitkeep`. Alembic is declared as a dependency in `apps/form/pyproject.toml` line 8, and `apps/form/alembic/env.py` is configured, but no migration scripts exist. Only `apps/form` has Alembic; `apps/panel` has no Alembic at all.

Schema is managed via `docker-entrypoint-initdb.d/` scripts that only run on **first container initialisation**. Any schema change after initial deployment requires manual `psql` intervention. There is no migration path, no version tracking, no rollback strategy.

**Fix:** Generate a baseline migration with `alembic revision --autogenerate -m "baseline"`. Commit all future changes as Alembic revisions. Add `alembic upgrade head` to the container entrypoint or as a startup step.

---

### PG-07 — MEDIUM: `ssl=prefer` — connections fall back to plaintext inside Docker

**Evidence:** `shared/src/emf_shared/db.py` line 23:
```python
connect_args={"ssl": "prefer"},
```

`infra/postgres/certs/` contains `server.crt` and `server.key`, but Postgres in `docker-compose.yml` has no command override to enable `ssl = on` in `postgresql.conf` or to pass `--ssl-cert-file`/`--ssl-key-file`. The `postgres:17-alpine` image does not enable SSL by default without explicit configuration.

**Consequence:** All app-to-database connections run over plaintext TCP inside the Docker bridge network. While intra-container traffic is low risk on a single host, `ssl=prefer` silently downgrades rather than enforcing encryption. An attacker with access to the Docker network can sniff plaintext queries including PII from `form_data`.

**Fix:** Either mount the certs and add a `command` override with `postgres -c ssl=on -c ssl_cert_file=... -c ssl_key_file=...` in docker-compose.yml, **or** change `ssl=prefer` to `ssl=disable` if encryption is deliberately not required (clarifies intent). Mattermost already uses `sslmode=disable` (`docker-compose.yml` line 189).

---

### PG-08 — MEDIUM: Router `_send_with_retry` opens multiple separate sessions per attempt — no outer transaction

**Evidence:** `apps/router/src/router/alert_router.py` lines 147–210. For each retry attempt:
1. A session is opened to insert the `Notification` row (line 147).
2. A session is opened to update `attempt_count` and `last_attempt_at` (line 162).
3. A session is opened to update `state` to SENT or FAILED (line 182 or 206).

If the process crashes or the task is cancelled between steps 2 and 3 (e.g. during `asyncio.sleep(delay_minutes * 60)`), the notification is left in state `pending` with `attempt_count > 0` — no automatic recovery. There is no periodic background job to detect and retry stuck `pending` notifications.

**Fix:** At minimum, add a startup job that checks for `pending` notifications older than N minutes and requeues them, or persist retry state via a proper queue (e.g. PostgreSQL-backed task queue via `pg_notify` with a `pending_jobs` table).

---

### PG-09 — LOW: `updated_at` maintained only in ORM; direct SQL updates silently skip it

**Evidence:** `infra/postgres/00_roles.sql` — no trigger on `forms.cases` to update `updated_at`. The column is populated by SQLAlchemy's `onupdate=lambda: datetime.now(tz=UTC)` (`apps/panel/src/emf_panel/models.py` line 39), but the panel uses `update(Case).where(...).values(...)` style bulk updates (e.g. routes.py line 465), which bypasses ORM-level `onupdate`. These calls explicitly pass `updated_at=datetime.now(tz=UTC)` in `.values()`, so it works today — but it's fragile. Any future update that forgets to include `updated_at` will silently leave a stale timestamp.

**Fix:** Add a `BEFORE UPDATE` trigger on `forms.cases` that sets `NEW.updated_at = NOW()`. This makes the invariant database-enforced.

---

### PG-10 — LOW: No PostgreSQL tuning; stock alpine defaults

**Evidence:** No `postgresql.conf` override in `infra/` (search found nothing). No `command` override in `docker-compose.yml` postgres service. The `postgres:17-alpine` image defaults to:
- `shared_buffers = 128MB`
- `max_connections = 100`
- `work_mem = 4MB`
- `effective_cache_size = 4GB` (advisory)

For a festival event handling hundreds of concurrent submitters and panel operators:
- `shared_buffers` should be ~25% of available RAM.
- `max_connections = 100` is shared across 3 services (form, panel, router) each with pool_size=5, max_overflow=10 = 15 connections per service = 45 total, leaving headroom but no margin.
- No `autovacuum` tuning for write-heavy tables (`cases`, `notifications`, `case_history`). Default autovacuum threshold is `50 + 20% of table size` rows before a vacuum triggers — fine at small scale, but dead tuple bloat from UPDATEs to `cases.status`, `cases.assignee`, and `notifications.state` can accumulate during peak festival operations.

**Fix:** Add a `command` override or a `postgres.conf` mounted volume with at minimum: `shared_buffers`, `work_mem`, `autovacuum_vacuum_scale_factor = 0.05` for high-churn tables.

---

### PG-11 — LOW: Backup is logical dump only; no WAL/PITR; RPO = last backup

**Evidence:** `scripts/backup.py` performs `pg_dump --format=custom`. The systemd timer runs at 04:00 daily (line 148). No WAL archiving is configured. No `pg_basebackup` or continuous archiving.

**RPO:** Up to 24 hours of data loss if the volume is lost between backups.
**RTO:** Time to restore = restore volume from last backup + replay (none, since no WAL). For a 4-day festival this is acceptable if the backup runs multiple times per day; a single daily backup means losing up to 24h of case data.

**Fix:** Increase backup frequency to hourly during the event, or enable WAL archiving (`archive_mode = on`, `archive_command`) for point-in-time recovery. The `pg_data` Docker volume is not backed up outside of `pg_dump` — if the host disk fails, the volume is lost.

---

### PG-12 — LOW: `case_history` is append-only by omission, not constraint

**Evidence:** `infra/postgres/00_roles.sql` lines 104 and 118 grant only `INSERT` (service_user) and `SELECT, INSERT` (team_member) on `case_history`. No `DELETE` or `UPDATE` is granted. The superuser (`emf_forms_admin`) can still delete.

This is the correct design but relies on no grants being accidentally added. There is no `SECURITY LABEL` or trigger preventing a superuser from deleting audit rows.

**Fix (optional hardening):** Add a `BEFORE DELETE OR UPDATE ON forms.case_history` trigger that raises an exception to make it tamper-resistant even for superusers. Alternatively, document explicitly that case_history is append-only by grant design.

---

### PG-13 — LOW: `case_history` and `notifications` have no RLS — all team members see all history

**Evidence:** `infra/postgres/00_roles.sql` — `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` is applied only to `forms.cases` (lines 125–126). `case_history` and `notifications` have no RLS.

Any user connecting as `team_member` can `SELECT * FROM forms.case_history` and see every audit row for every case, and `SELECT * FROM forms.notifications` to see ACK state and contact details in `acked_by`. In a multi-team deployment this would be a data boundary violation.

**Note:** As established in PG-01, `team_id` is always NULL so all cases are visible anyway. This finding is latent — it matters if PG-01 is fixed.

---

### PG-15 — INFO: Mattermost uses `sslmode=disable` for its database connection

**Evidence:** `infra/docker-compose.yml` line 189:
```
MM_SQLSETTINGS_DATASOURCE: postgres://emf_forms_admin:...@postgres/mattermost?sslmode=disable
```

Mattermost connects as `emf_forms_admin` (the superuser) with SSL explicitly disabled. This is a separate `mattermost` database but on the same Postgres instance. The superuser password is the same `ADMIN_DB_PASSWORD` used for Postgres administration.

## Architecture Review

**Scope:** Macro structural decisions — coupling, service boundaries, failure modes, evolution. Not individual bugs.

---

### 1. Service Decomposition — 5 services vs monolith

**What's gained:** Isolation of the public attack surface (form) from the staff interface (panel). The router runs as a separate process with a distinct DB role (router_user) scoped to a non-PII view. TTS as a separate service makes sense — Piper requires a native binary, and the memory/CPU footprint is unpredictable; isolation prevents a synthesis spike from starving form submissions. The jambonz adapter was planned as a 5th service but is absent from the actual codebase — telephony is handled via an in-process `EMFPhoneAdapter` inside the router, which is the better call.

**The cost:** Five Python processes, each with its own SQLAlchemy connection pool (pool_size=5, max_overflow=10) means up to 75 potential connections to a single Postgres instance that is also running Mattermost. For a festival weekend with low concurrency this is fine, but the pool sizing is never explicitly coordinated between services.

**Verdict:** The 3-service core (form, panel, router) is justified. TTS isolation is reasonable given the subprocess model. The instinct to add a 6th "admin" service (mentioned as unimplemented in plan.md Phase 12) should be resisted — that functionality should live in panel. The decomposition is broadly appropriate for the threat model and operational context.

---

### 2. pg_notify as Event Bus

**What it does:** `listener.py` opens a dedicated `asyncpg` connection (bypassing SQLAlchemy) and calls `LISTEN new_case`. When a case is inserted, a trigger fires `NOTIFY new_case, '<uuid>'`. The listener picks it up and fires `asyncio.create_task` to route.

**Advantages:** Zero additional infrastructure. NOTIFY is delivered within the same transaction scope — no event is lost after a committed write. Reconnect logic is implemented (5-second retry loop). Both `new_case` and `retrigger_case` channels are supported.

**Failure modes vs a proper queue:**
- **No durability:** `pg_notify` is fire-and-forget at the network level. If the listener process is restarting when a NOTIFY fires, the event is silently dropped. The code partially mitigates this: `_handle_new_case` checks for existing notifications before routing, so a `retrigger_case` can reprocess missed cases. But this requires manual operator intervention — there is no automatic recovery scan on router startup for unprocessed cases.
- **No back-pressure:** If routing is slow (e.g. Signal API is down, retrying every 5–15 minutes), the listener will queue `asyncio` tasks indefinitely in memory. Under burst conditions this is a memory leak.
- **Payload size:** NOTIFY payloads are limited to 8KB. The current payload is just a UUID, so this is fine.
- **Single consumer:** pg_notify has no consumer-group semantics. Running two router replicas would cause double-sends.

**Mitigation gap:** There is no startup reconciliation — the router does not query `WHERE notifications.count = 0` on startup to catch events it missed while down. This is the most significant operational risk: a brief router restart during a high-urgency incident means the conduct team gets no notification. A 30-line startup scan would fix this.

---

### 3. Shared Library Coupling

`emf_shared` contains: `config.py` (AppConfig + Settings base), `db.py` (SQLAlchemy engine init + session factory), `friendly_id.py`, `logging.py`, `middleware.py`, `phase.py`, `tracing.py`, `templates/`, `mapembed.py`.

**What's a bounded context:** `db.py`, `logging.py`, `middleware.py`, `tracing.py`, `phase.py` are pure infrastructure utilities with no business logic. These are legitimately shared.

**Coupling concern:** `config.py` contains `AppConfig` — the single config model — shared by all services. A change to `AppConfig` (adding a field, changing a type) requires rebuilding and redeploying all services. For a small codebase with one team this is acceptable, but it means there is no config schema versioning between services.

**DB coupling:** `db.py` exposes a global engine singleton (`_engine`, `_session_factory`). Each service calls `init_db()` once at startup. This is fine for single-process services but would break if any service were ever multi-worker (e.g., `uvicorn --workers 4`). Each worker process would initialise its own pool, which is correct, but the global state pattern is fragile.

**Templates in shared:** Jinja2 templates in `emf_shared/templates/` are loaded into the panel's ChoiceLoader. This is an unusual pattern — shared UI fragments in a library package. It works but makes the rendering path non-obvious.

**Verdict:** Not a dumping ground, but the shared config model is the highest coupling risk. Changes to event config, urgency levels, or SMTP config are a rebuild-all event.

---

### 4. Synchronous Notification Chain

The router does **not** send synchronously. Each channel gets its own `asyncio.create_task(_send_with_retry(...))` — they run concurrently. A slow Signal API (e.g., 30-second timeout × 4 retries) does not block email delivery. This is the correct design.

**Remaining concern:** All retry tasks live in the process's event loop. The `RETRY_DELAYS_MINUTES = [0, 5, 10, 15]` means a failing channel holds an asyncio task sleeping for up to 30 minutes. Under normal conditions (few cases) this is fine. If 20 cases arrive simultaneously with all channels down, there will be 100 sleeping tasks. Still low memory impact, but there is no cap.

**ACK fan-out:** `send_ack_to_all_channels` also uses `asyncio.create_task`, so ACK confirmations to other channels (e.g. update the Mattermost message to show who acked) are fire-and-forget. Failures here are silently dropped — there is no retry and no logging of ack-confirmation failures beyond what the adapter itself logs.

---

### 5. Redis Usage

Redis is used for two things:
1. `panel:assignees` — a Redis Set of staff usernames, used to populate the assignee dropdown.
2. Token revocation — `revoke_token(jti)` is called, but inspection of `dispatcher.py` shows revocation is stored in **in-process sets** (`_revoked`, `_active_sessions`), not Redis. Redis is only used for assignees.

**Finding:** The plan described Redis for session management and token revocation, but the implementation stores revocation state in module-level Python sets. This means:
- If the panel process restarts, all revoked tokens become valid again until they expire naturally (TTL is JWT exp claim, up to 8 hours).
- Device session limits (`max_devices`) reset on restart.
- This is a security gap for the dispatcher feature — a revoked session token remains revocable only for the lifetime of the process.

**Redis down scenario:** Only the assignees dropdown breaks. Panel login, case management, and all critical paths continue. The dependency is low-criticality.

**If Redis is kept:** Session revocation should move there. Key: `revoked:{jti}` with TTL = token remaining lifetime. `active_sessions` should also be Redis hashes.

---

### 6. OIDC Dependency — Break-Glass Access

The panel has no emergency access mechanism. If the OIDC provider is unreachable during the festival:
- All staff are locked out of the case management interface.
- The dispatcher view (token-authenticated, no OIDC) remains accessible — but dispatcher tokens must be minted by an authenticated panel user, so if OIDC was down before a token was created, dispatch is also blocked.

**What the plan specified:** Section 8 security tasks mention OIDC auth. There is no mention of break-glass access.

**Practical risk:** EMF uses an external OIDC provider (likely the EMF SSO). If that provider goes down at 2am during the festival, the conduct team cannot access case history or manage status. The public form continues to receive reports.

**Mitigation options (none implemented):**
- Local admin account with bcrypt password in `.env`, bypassed by `LOCAL_DEV` flag check.
- A pre-generated long-lived dispatcher token (24h TTL) emailed to the team lead before the event.
- `LOCAL_DEV=true` env var as documented break-glass (currently it only affects routing phase, not auth).

---

### 7. Data Model — RLS vs Separate Schemas

The design uses a single schema (`forms`) with column-level grants and RLS for isolation. This is a coherent approach.

**RLS policy concern:** The `team_isolation` policy is:
```sql
USING (team_id IS NULL OR team_id = current_setting('app.current_team_id', true)::uuid)
```
This only protects the `cases` table. The `notifications`, `case_history`, and `idempotency_tokens` tables have no RLS. A `team_member` with a crafted `team_id` context could read notifications for cases outside their team.

**`form_data` JSONB blob:** PII (reporter contact details) lives in `form_data`. The `cases_router` view correctly excludes it. The `cases_dispatcher` view also excludes it. However, `form_data` has no schema enforcement at the DB level — it's a free JSONB blob. If the form app changes the structure, the router's `form_data -> 'location' ->> 'lat'` extractions silently return NULL rather than failing visibly.

**Mattermost sharing Postgres:** The compose file shows Mattermost using `emf_forms_admin` on a `mattermost` database in the same Postgres instance. This means a Mattermost corruption or admin-credential compromise also affects the conduct data. Separate Postgres instances (or at minimum separate admin credentials) would be safer.

---

### 8. API Design

**Form service:** Unversioned (`/`, `/submit`, `/health`). No `v1` prefix. Breaking changes would require coordinated redeploy. Low risk since the form has no external API consumers.

**Panel service:** Versioned at `/api/v1/...` consistently. Good.

**Router service:** Mix of `/webhook/...`, `/internal/...`, `/ack/...` with no version prefix. The internal ACK endpoint (`/internal/ack/{case_id}`) is called by panel — coupling point. If the router API changes, panel breaks silently at runtime.

**Inter-service auth:** Router's internal endpoints use a pre-shared secret (`X-Internal-Secret` header). This is reasonable for a single-host deployment. The secret defaults to empty string (auth disabled) if not set — this is a footgun in production if the .env is not correctly populated.

**REST hygiene:** Panel uses `PATCH` for field updates (status, assignee, urgency, tags) — correct. The ack endpoint is `POST /cases/{id}/ack` which is debatable (could be `PATCH /cases/{id}/status` with `{"status":"acknowledged"}`), but consistent with the domain language.

---

### 9. Caddy as TLS Terminator

**Plan vs reality:** The plan specifies Caddy throughout. The actual `docker-compose.yml` uses Traefik labels (`traefik.enable=true`, `traefik.http.routers.*`) with an external `traefik-public` network. There is no Caddy service in `docker-compose.yml`. The `infra/caddy/` directory and Caddyfiles exist but are not referenced by the compose file.

This is a significant plan deviation: the compose file assumes Traefik is running externally (outside this compose stack), which means TLS termination configuration is not self-contained. The install script (plan.md Phase 9) references Caddy generation, but the runtime config assumes Traefik. Operators setting this up from scratch will encounter a gap.

**Security impact of plain HTTP internally:** Acceptable for single-host Docker-bridge-network deployment. All inter-container comms are on a private bridge (`contactform` network). The risk is an attacker with host access — at which point all secrets are already compromised anyway.

**TLS-to-Postgres:** The compose file mounts `./postgres/certs` but `ssl=on` is not visible in the compose environment config. Plan.md notes (Phase C backlog) that the cert ownership issue prevents SSL from working. So Postgres connections from all services are likely plain TCP on the internal Docker network.

---

### 10. Scalability Ceiling

**Hard limits:**
- `pg_notify` single consumer — cannot run multiple router replicas.
- In-process dispatcher session state (`_revoked`, `_active_sessions`) — cannot run multiple panel replicas.
- TTS audio files stored on container filesystem (`/app/audio`) — not shared across replicas.
- Single Postgres instance, no read replicas.

**Soft limits:**
- Rate limiting (`slowapi`) is in-process. Under multiple form replicas, rate limits would be per-process, not global. Redis-backed rate limiting would be needed for accurate counting.
- The 5+10 connection pool per service is fine for expected load (tens of concurrent users at most).

**Path to scale:** The form service is the only externally-facing, potentially high-traffic component. It is stateless (no session, no local state) and could horizontally scale behind a load balancer with Redis-backed rate limiting. Everything else can remain single-instance. For EMF Camp (a ~3000-person festival), the current architecture is more than sufficient.

---

### 11. Technology Choices

**FastAPI + asyncpg + SQLAlchemy 2 async + Pydantic v2:** Well-matched. All major dependencies have good async support. No mismatch found between the async model and blocking operations — Piper is correctly invoked via `asyncio.create_subprocess_exec`, not `subprocess.run`. The `ssl: "prefer"` in `create_async_engine` connect_args is slightly weak (should be `"require"` in production), but this is tactical.

**`slowapi` for rate limiting:** Uses in-memory storage by default. The form service is the rate-limited surface. Works correctly for single-process, but see scalability note above.

**Authlib for OIDC:** Solid choice. The split between authlib (for OIDC flow) and PyJWT (for bearer token verification) is pragmatic — authlib handles the redirect dance, PyJWT handles stateless API auth.

**`emf_shared` as a local editable install:** Each service's `pyproject.toml` presumably references `emf_shared` as `{path = "../../shared"}`. This means shared lib changes require rebuilding all Docker images. For a small team this is fine, but there is no shared lib versioning.

---

### 12. Plan vs Reality

| Area | Plan | Reality | Impact |
|---|---|---|---|
| TLS proxy | Caddy (generated Caddyfile per install) | Traefik (external, labels-based) | Operators must run Traefik separately; Caddy config is dead code |
| Jambonz | Standalone `apps/jambonz/` service | `EMFPhoneAdapter` inside router | Better — removes unnecessary service; check boxes marked `[x]` but no code |
| Token revocation | Redis | In-process sets | Security gap: revoked tokens survive process restart |
| Attachment uploads | `POST /attachments` endpoint + ClamAV pipeline | Endpoint exists, no volume mount, ClamAV not wired | Known bug (RT-BUG-02); marked as backlog |
| Admin app (Phase 12) | Separate sysadmin service | Not implemented | Low risk; don't build it, fold into panel |
| Postgres SSL | `ssl=on` with mounted certs | Cert ownership issue blocks SSL | Plain TCP internally; medium risk |
| SMTP password | `SMTP_PASSWORD` in `.env` | Present in `Settings` base class | Correct |
| Signal polling | Webhook-based | Both polling loop AND webhook endpoint implemented | Redundant paths; webhook is cleaner, polling is fallback |

**Key unimplemented item with operational risk:** No startup reconciliation scan in the router. If the router is restarting when a case comes in, the case goes unnotified. Plan.md does not mention this as a task, but it is the most operationally dangerous gap.

**Key misalignment:** The compose file assumes an externally-managed Traefik instance. The Caddyfiles, install script Caddy logic, and CLAUDE.md references to Caddy are all inconsistent with this. Before the festival deployment, the team needs to decide: is the reverse proxy Caddy (self-contained in the stack) or Traefik (external dependency)?

---

### Summary — Decisions Expensive to Change

| Decision | Cost to Change | Risk Level |
|---|---|---|
| pg_notify event bus (no startup reconciliation) | Medium — add a startup scan | High operational risk during incidents |
| In-process token revocation (not Redis) | Low — move state to Redis | Medium security gap |
| Caddy vs Traefik ambiguity | Medium — pick one, remove the other | High deployment confusion risk |
| Shared `AppConfig` model across all services | High — would require API versioning | Low current risk, rises with team growth |
| Single Postgres for app + Mattermost | Medium — separate Mattermost to its own PG | Low current risk |
| No break-glass OIDC bypass | Low — add LOCAL_DEV bypass or pre-seeded token | High festival operational risk |
| RLS gaps on notifications/case_history | Low — add policies | Medium for multi-team deployments |

---

## GDPR Compliance Audit

**Date:** 2026-07-11
**Auditor:** Claude (static code and architecture analysis)
**Scope:** UK GDPR compliance review — EMF Conduct System at `/work`
**Applicable law:** UK GDPR (Data Protection Act 2018); ICO guidance
**Caveat:** Static analysis only. No live stack tested. No Data Protection Officer or legal counsel consulted. This is a technical audit, not legal advice.

---

### Executive Summary

The EMF Conduct System processes sensitive personal data about festival attendees — incident reports that may contain health information, descriptions of sexual harassment or assault, details about disabilities, and other special category data. The system has good technical security foundations (OIDC access controls, PostgreSQL RLS, least-privilege DB roles, TLS in transit) but has significant gaps in GDPR documentation, data subject rights, retention, and third-party processor governance.

**Critical gaps:** no documented lawful basis, no data retention schedule implemented, no Data Subject Request (DSR) process, no breach notification procedure, privacy notice is a link to an external page not tailored to this processing.

---

### 1. Lawful Basis (Article 6 UK GDPR)

**Status: NOT DOCUMENTED — CRITICAL GAP**

No lawful basis for processing is stated anywhere in the codebase, configuration, templates, or documentation. The plan.md note `- [x] Review and document all data minimisation decisions (what is collected, legal basis, retention period placeholder)` is marked complete but the actual documentation does not exist in the repo.

**Relevant files:**
- `/work/plan.md` line 2663 — task marked `[x]` but no output exists
- `/work/apps/form/templates/form.html` — form collects PII with no lawful basis disclosure
- `/work/apps/form/templates/footer.html` — links to `https://www.emfcamp.org/about/privacy` (external, generic EMF privacy policy, not specific to this conduct system)

**Assessment:** The most defensible basis for this processing is **legitimate interests** (Article 6(1)(f)) — EMF as event organiser has a legitimate interest in handling conduct complaints to protect attendee safety. An alternative is **vital interests** (Article 6(1)(d)) for urgent/safety cases. Neither is stated. If special category data is involved (very likely — see §3), a second basis under Article 9 is also required (most likely Article 9(2)(b) — employment/social protection obligations, or Article 9(2)(g) — substantial public interest).

**Required actions:**
- Conduct a Legitimate Interests Assessment (LIA) and document the outcome
- State the lawful basis explicitly in the privacy notice linked from the form
- State the Article 9 basis for special category data

---

### 2. Data Minimisation (Article 5(1)(c) UK GDPR)

**Status: MOSTLY COMPLIANT — MINOR CONCERNS**

Collected PII (from `/work/apps/form/src/emf_form/schemas.py` and `/work/apps/form/templates/form.html`):

| Field | Necessity | Assessment |
|---|---|---|
| `what_happened` (required) | Core of complaint | Necessary |
| `incident_date`, `incident_time` (required) | Investigation | Necessary |
| `reporter.name` (optional) | Follow-up contact | Proportionate if contact consent given |
| `reporter.email` (optional, required if `can_contact=true`) | Follow-up | Proportionate |
| `reporter.phone` (optional, event-time only) | Follow-up | Proportionate |
| `reporter.pronouns` (optional) | Respectful follow-up | Borderline — could be asked at point of contact |
| `reporter.camping_with` (optional, event-time only) | Locating reporter | Borderline — third-party data (tent-mates named) |
| `others_involved` (optional) | Third-party data — named individuals who have not consented | Necessary but high-risk |
| `why_it_happened` (optional) | Context | Potentially excessive |
| `location` (lat/lon + text, optional) | Investigation | Proportionate |
| `support_needed`, `outcome_hoped`, `anything_else` (optional) | Support planning | Proportionate |
| `media_links` (optional) | Evidence | Proportionate |

**Concerns:**
- `reporter.camping_with` collects third-party data about individuals who have not submitted anything and cannot have consented — just the reporter identifying who they camp with exposes others' presence at the festival.
- `others_involved` is a free-text field that may name alleged perpetrators, witnesses, and bystanders. These individuals' data is being processed without their knowledge. This is lawful under legitimate interests / vital interests but must be addressed in the privacy notice.
- `reporter.pronouns` being collected at submission time rather than at point of follow-up contact is borderline excessive, though the field is optional.

---

### 3. Special Category Data (Article 9 UK GDPR)

**Status: NOT ADDRESSED — CRITICAL GAP**

Conduct reports at a festival are highly likely to contain special category data:
- Descriptions of sexual harassment or assault → **data concerning sex life/sexual orientation** (Art 9(1))
- Mental health crises, medical emergencies, substance use → **health data** (Art 9(1))
- Hate-based incidents targeting religion, race, disability, gender → multiple special categories (Art 9(1))

**No special category data is flagged anywhere in the system.** No extra safeguards are applied:
- No Article 9 basis is documented
- The `what_happened` field (10–10,000 chars, required) is stored in a JSONB column (`form_data`) without any field-level encryption or additional access control
- Special category data is transmitted to Mattermost, Signal, email, and telephony channels without any indication that these processors are suitable for special category data
- ClamAV and Safe Browsing are used for content safety, but no system processes or flags special category data for heightened protection

**Required actions:**
- Document Article 9(2) basis (likely (b) employment obligations or (g) substantial public interest with appropriate policy)
- Consider whether `what_happened` and free-text fields containing likely special category data should have field-level encryption (e.g. pgcrypto column encryption) or at minimum tighter access controls
- Review all notification channels for suitability to receive special category data

---

### 4. Data Retention (Article 5(1)(e) UK GDPR)

**Status: NOT IMPLEMENTED — CRITICAL GAP**

No automated retention or deletion exists anywhere in the codebase.

**Evidence:**
- `/work/apps/form/src/emf_form/models.py` — `Case` has `created_at` but no `expires_at`, no TTL, no scheduled deletion
- `/work/infra/postgres/00_roles.sql` — no DELETE grants to any role; no retention policy table; no pg_cron job
- `/work/plan.md` line 2257: `"Data retention | ✅ Decided | Manual process post-event. Export to CSV required in the panel. PII purge schedule TBD with conduct team."`
- The task is marked decided but "TBD with conduct team" — the decision has not been implemented

**What is stored indefinitely:**
- Full `form_data` JSONB blob containing all reporter PII and incident narrative
- `case_history` — change log including `changed_by` (staff OIDC usernames)
- `notifications` — records of who was alerted, when, and what channel
- `idempotency_tokens` — link between submission tokens and case UUIDs (forever)
- Uploaded image attachments — stored on local filesystem with no deletion mechanism

**Required actions:**
- Define and implement a documented retention period (e.g. 3 years post-event is common for welfare organisations, but legal/conduct team must decide)
- Implement automated deletion or anonymisation at end of retention period (pg_cron or a scheduled script)
- Separately schedule deletion of `idempotency_tokens` (no value after ~24 hours post-event)
- Add retention schedule to privacy notice

---

### 5. Data Subject Rights (Articles 15–22 UK GDPR)

**Status: NO MECHANISM EXISTS — CRITICAL GAP**

There is no mechanism to fulfil any data subject right:

| Right | Mechanism | Status |
|---|---|---|
| Right of access (Art 15) | None | **Missing** |
| Right to erasure (Art 17) | None | **Missing** |
| Right to rectification (Art 16) | None | **Missing** |
| Right to portability (Art 20) | Panel export planned (plan.md), not implemented | **Missing** |
| Right to object (Art 21) | None | **Missing** |
| Right to restrict processing (Art 18) | None | **Missing** |

**Panel API endpoints** (`/work/apps/panel/src/emf_panel/routes.py`): The panel has a `GET /api/v1/cases/{case_id}` endpoint that returns `form_data` (containing all PII), but this is only accessible to authenticated conduct team members. There is no public-facing or admin-facing DSR workflow.

No admin endpoint exists to:
- Search cases by reporter email/name to fulfil an access request
- Delete or anonymise a specific case (no DELETE routes anywhere in the codebase)
- Export a reporter's data in a portable format

**Required actions:**
- Create a documented DSR process (who receives requests, how they are fulfilled, 30-day deadline)
- Implement at minimum a panel admin capability to search by reporter PII, export, and anonymise/delete
- Publish a contact mechanism (email address) for DSRs — not present in the privacy notice link or form

---

### 6. Third-Party Processors (Article 28 UK GDPR)

**Status: NO DPAs DOCUMENTED — HIGH-RISK GAP**

The system sends notification data to external processors. No Data Processing Agreements (DPAs) are documented in the codebase or docs.

| Processor | Data sent | DPA status |
|---|---|---|
| **Resend** (email) | `friendly_id`, event name, location hint, panel URL, ack token — NOT full reporter PII | Unknown — no DPA documented |
| **Signal** (via self-hosted `signal-cli-rest-api`) | `friendly_id`, urgency, event name, location hint, panel link | Self-hosted; Signal protocol E2E encrypted; less concerning but still a processor |
| **Mattermost** | `friendly_id`, urgency, event name, location hint — NOT full reporter PII | Unknown — may be self-hosted (local profile) or external |
| **EMF phone system** (`sip2.ix1.inferno.tel:3000`) | Case reference + location hint spoken as TTS audio, phone number of conduct team member being called | Unknown — third-party API |
| **Google Safe Browsing API** | URLs submitted by reporters in `media_links` field | Privacy note in sysadmin-setup.md acknowledges this; no DPA mentioned |

**Important finding:** The notification channels send the `friendly_id` + panel link, **not** the reporter's name/email/phone number. Full PII remains in the database. This is good design from a data minimisation standpoint.

**However:** The `location_hint` is sent to all channels. For small events, "near the main stage at 2am" combined with the urgency level may be re-identifiable.

**Required actions:**
- Confirm DPAs are in place with Resend (they publish a standard DPA)
- Confirm status of Mattermost deployment — if cloud-hosted, DPA required
- Confirm the EMF phone system has appropriate data handling terms
- Document processor relationships in a Record of Processing Activities (RoPA)

---

### 7. International Data Transfers (Chapter V UK GDPR)

**Status: LIKELY OCCURRING WITHOUT DOCUMENTED BASIS**

**Resend** is a US company (Resend Inc.). Emails sent via their API are processed on servers subject to US law. Since Schrems II (2020) and post-Brexit UK GDPR, transfers to the US require an appropriate transfer mechanism.

- Resend participates in the UK/US Data Bridge (operative from October 2023) for UK-to-US transfers. If Resend is certified under this framework, transfers are covered. This needs verification.
- No transfer mechanism is documented anywhere in the codebase or configuration.

**Signal API:** The self-hosted `signal-cli-rest-api` container (`bbernhard/signal-cli-rest-api`) communicates with Signal's servers for message delivery. Signal's servers are operated by Signal Foundation (US non-profit). Transfer mechanism unclear.

**Google Safe Browsing:** Google LLC (US). Same transfer concern as Resend. The sysadmin guide notes the privacy implication but not the transfer mechanism.

**EMF phone system** (`sip2.ix1.inferno.tel`): Unknown jurisdiction.

**Required actions:**
- Verify Resend's UK GDPR / UK-US Data Bridge certification status
- Document all international transfer mechanisms in the RoPA
- If no adequate transfer mechanism exists for any processor, suspend use of that processor or put SCCs in place

---

### 8. Security of Processing (Article 32 UK GDPR)

**Status: MOSTLY COMPLIANT — SOME GAPS**

**Positives:**
- TLS in transit (Caddy/Traefik with Let's Encrypt) — `/work/infra/docker-compose.yml` confirms TLS labels
- PostgreSQL with SCRAM-SHA-256 authentication (`POSTGRES_INITDB_ARGS: "--auth-host=scram-sha-256"`)
- Role-based access: `form_user` cannot read `form_data`; `router_user` sees only a non-PII view (`cases_router`); `team_member` has full access; RLS enforces team isolation
- OIDC authentication with group-based access control for the panel
- ClamAV virus scanning for attachments (optional profile)
- Rate limiting on the public form (slowapi)
- Honeypot field for bot submissions

**Gaps:**
- **Encryption at rest:** PostgreSQL data volume (`pg_data`) is a plain Docker volume — no evidence of filesystem-level encryption. If the host is compromised or the volume is accessed directly, all PII is readable in plaintext. Article 32(1)(a) recommends pseudonymisation and encryption.
- **Database SSL between services and Postgres:** Connection strings in docker-compose.yml use `postgresql+asyncpg://...@postgres/emf_forms` — no `sslmode=require` or certificate pinning. Internal Docker network, so low-risk, but Mattermost explicitly uses `sslmode=disable` on line 189.
- **Redis:** Used for session assignees cache. Password protected but no TLS. Session data could leak if Redis is compromised.
- **Attachment storage:** Uploaded images stored on local filesystem (`attachment_dir` in settings). No evidence of encryption. Bug RT-BUG-02 from previous findings confirms attachment directory is not even properly configured.
- **Secret key hardcoded defaults:** `SECRET_KEY:-changeme`, `REDIS_PASSWORD:-changeme`, `ADMIN_DB_PASSWORD:-localdev` in docker-compose.yml — obvious risk if .env not populated.

**Required actions:**
- Enable filesystem encryption on the host (LUKS on Linux) or use encrypted EBS/volume
- Add `sslmode=require` to internal Postgres connections
- Document security measures in a technical security policy (required for Art 32 records)

---

### 9. Privacy Notices (Article 13/14 UK GDPR)

**Status: INADEQUATE — HIGH-RISK GAP**

The public report form includes a footer link: `<a href="https://www.emfcamp.org/about/privacy">Privacy policy</a>` (`/work/apps/form/templates/footer.html`).

This links to the **generic EMF Camp website privacy policy**, not a privacy notice specific to this conduct reporting system. Article 13 requires the controller to provide privacy information **at the time data is collected**, including:

- [ ] Identity of the controller (who is the data controller — EMF Camp Ltd? A committee?)
- [ ] Contact details of the controller (not present)
- [ ] DPO contact if applicable (not present)
- [ ] Lawful basis for processing (not present — see §1)
- [ ] Special category basis (not present — see §3)
- [ ] Purposes and legal basis for each category of data
- [ ] Retention period (not present — see §4)
- [ ] Data subject rights (not present — see §5)
- [ ] Right to lodge a complaint with the ICO
- [ ] Whether data is transferred internationally (not present — see §7)
- [ ] Names of recipients/processors (Resend, Signal, Mattermost)

The form body text says only: `"Your report is confidential."` This is not a compliant privacy notice.

**Required actions:**
- Create a conduct-system-specific privacy notice accessible from the form
- Ensure it covers all Article 13 requirements
- Display it prominently (not just in the footer)
- For third-party data (named subjects who didn't submit), Article 14 notice obligations apply — document how these will be met

---

### 10. Breach Notification (Articles 33–34 UK GDPR)

**Status: NO PROCESS EXISTS**

There is no breach detection, logging, or notification process anywhere in the system.

**72-hour obligation (Art 33):** If a personal data breach occurs (e.g. database exposed, OIDC bypassed, email misconfigured to wrong recipients), the controller must notify the ICO within 72 hours.

**No mechanisms exist for:**
- Detecting anomalous data access (no audit log on who accessed which case, beyond `case_history` for workflow changes)
- Detecting data exfiltration attempts
- Escalating a potential breach to the DPO/controller
- Documenting breaches in an internal breach register
- Notifying affected data subjects (Art 34) if breach is high-risk

**Prometheus/Grafana** monitoring is available (`/work/infra/docker-compose.yml` lines 253–278) but no breach-detection alerts are configured.

**Required actions:**
- Establish a breach notification procedure (documented, not just in code)
- Identify who is responsible for notifying the ICO
- Configure anomalous access alerts (e.g. bulk case downloads, access outside event period)
- Create a breach register template

---

### 11. Consent as Lawful Basis

**Status: CONSENT IS NOT THE BASIS (CORRECT)**

The form uses a `can_contact` checkbox which is sometimes described as "consent" but is more accurately an operational question about follow-up. This is correctly not framed as consent for processing.

**Important:** The `can_contact=false` path still results in the report being stored and processed — the form correctly communicates this: "We have received your report and the conduct team will review it." Reporters are not told their data won't be processed if they decline contact.

**This is correct design** — processing for legitimate interests/vital interests does not require consent. Using consent as the basis would be problematic because:
- Reporters in a welfare situation cannot freely withhold consent
- Withdrawing consent would create obligations to stop processing, which conflicts with investigating serious incidents

**No action required** on the lawful basis question specifically, but the legal basis must be documented (see §1).

---

### 12. PII in Application Logs

**Status: LARGELY CLEAN — MINOR CONCERN**

The structured logging configuration (`/work/shared/src/emf_shared/logging.py`) logs: `timestamp`, `level`, `logger`, `trace_id`, `service`, `message`.

Application log statements examined:
- Form service logs: `422 on %s: %s` with `request.url.path` and validation errors. Validation errors for `reporter.email`, `reporter.phone` etc. may contain PII values if they appear in error context. `/work/apps/form/src/emf_form/main.py` line 71.
- Router logs: `Sent case %s via %s (attempt %d)` — only case UUID, channel, attempt number. No PII.
- Phone adapter logs: description and number of conduct team members being called (these are staff, not reporter PII). Lines 70–133 of `/work/apps/router/src/router/channels/emf_phone.py`.
- uvicorn access logs (default): would include request paths. The `/api/submit` path itself contains no PII, but a 422 validation error log may include submitted values if uvicorn is set to DEBUG.

**Concern:** FastAPI's default `RequestValidationError` handler and uvicorn at DEBUG level may log request bodies. The custom handler at line 71 of `main.py` logs `exc.errors()` which contains field names and received values — if a reporter submits an invalid email like `name@[their real name]`, it appears in logs.

**Required action:**
- Ensure log level is INFO or above in production (not DEBUG)
- Review whether `exc.errors()` should be scrubbed of input values before logging
- Ensure log storage (Docker log driver / filesystem) is covered in the data retention policy

---

### 13. Anonymisation and Pseudonymisation

**Status: PARTIAL PSEUDONYMISATION — NOT FULLY ANONYMISED**

**What is pseudonymised:**
- Cases are assigned a `friendly_id` (e.g. `tiger-lamp-blue-moon`) generated from a word list (`/work/shared/src/emf_shared/friendly_id.py`) — this is not meaningfully pseudonymous (it is only a human-readable alias for the UUID, with no separation of identity from content)
- The UUID `case_id` is not derived from reporter identity — it is a random UUID4

**What is NOT separated:**
- Reporter PII (`name`, `email`, `phone`, `pronouns`, `camping_with`) is stored in the same JSONB blob (`form_data`) as the incident narrative (`what_happened`, `others_involved`, etc.)
- There is no separation of identity from incident data — a single query returns both
- The `cases_router` view (visible to `router_user`) excludes PII from `form_data`, but `form_data` contains all PII for `team_member` and panel access

**Assessment:** The routing layer correctly implements data minimisation (router sees location and metadata, not reporter identity). However, within the case management system, reporter identity and incident content are co-located with no technical separation. An Article 32 measure worth implementing is splitting `form_data` into `reporter_pii` (encrypted, tighter access) and `incident_data` (narrative).

**No critical action required**, but recommend:
- Consider field-level encryption for `reporter.email`, `reporter.phone`, `reporter.name` columns
- Document the pseudonymisation approach in the security policy

---

### 14. Data Protection Officer (DPO) (Article 37 UK GDPR)

**Status: UNKNOWN — ACTION REQUIRED TO ASSESS**

A DPO is mandatory under Art 37 if the organisation:
- Is a public authority (EMF Camp Ltd is likely a private company — probably not applicable)
- Carries out large-scale systematic monitoring of individuals (not applicable)
- Carries out large-scale processing of special category data **(potentially applicable)**

EMF 2024 had approximately 3,000 attendees. "Large-scale" is not defined in UK GDPR but the ICO considers factors including number of data subjects and sensitivity of data. A festival welfare/conduct system processing special category data about hundreds of individuals per event could meet the threshold.

**Required action:**
- Take legal advice on whether EMF Camp Ltd's processing of special category data via this system requires DPA appointment
- If not mandatory, consider voluntary appointment given the sensitivity of the data
- If a DPO is appointed, their contact details must appear in the privacy notice

---

### Summary Risk Register

| # | Area | Severity | Status | Finding |
|---|---|---|---|---|
| G-01 | Lawful basis | CRITICAL | NOT DOCUMENTED | No Article 6 or Article 9 basis documented anywhere |
| G-02 | Privacy notice | CRITICAL | INADEQUATE | Form links to generic EMF policy; no Art 13 disclosures specific to conduct system |
| G-03 | Data retention | CRITICAL | NOT IMPLEMENTED | No retention period defined or enforced; data stored indefinitely |
| G-04 | DSR process | CRITICAL | MISSING | No mechanism for access, erasure, portability, or rectification requests |
| G-05 | Special category data | CRITICAL | NOT ADDRESSED | Reports highly likely to contain Art 9 data; no extra safeguards or Article 9 basis |
| G-06 | Breach notification | HIGH | MISSING | No detection, escalation, or 72-hour ICO notification process |
| G-07 | Processor DPAs | HIGH | UNDOCUMENTED | Resend (US), Signal, Mattermost, EMF phone — no DPAs documented |
| G-08 | International transfers | HIGH | UNDOCUMENTED | Resend (US company) — transfer mechanism not documented |
| G-09 | Encryption at rest | HIGH | MISSING | Database volume not encrypted; attachment storage not encrypted |
| G-10 | Third-party data | MEDIUM | DESIGN RISK | `others_involved`, `camping_with` collect data about non-submitting individuals |
| G-11 | Log PII | LOW | MINOR RISK | Validation error logs may include submitted values at DEBUG level |
| G-12 | DPO assessment | MEDIUM | UNASSESSED | Large-scale special category processing may require DPO appointment |
| G-13 | Pronouns data | LOW | BORDERLINE | Optional collection at submission; defensible but borderline |
| G-14 | Redis TLS | LOW | MISSING | Internal but no TLS; session/cache data unencrypted in transit |

---

## FastAPI/Python Review

**Date:** 2026-07-12

Reviewed: `apps/form`, `apps/panel`, `apps/router`, `apps/tts`, `shared/`.

### FP-01 — Untracked `asyncio.create_task` in `AlertRouter` — HIGH

**Files:** `apps/router/src/router/alert_router.py:117`, `:122`, `:291`

`_route_event_time`, `_route_off_event`, and `send_ack_to_all_channels` all call `asyncio.create_task(...)` without saving the reference. If the event loop GCs the task before it completes, it is silently discarded — exceptions are also silently dropped (Python 3.12 logs a "Task was destroyed but it is pending!" warning at best). Compare: `listener.py` correctly saves tasks to `_background_tasks` and calls `add_done_callback`. The same pattern must be applied here or send failures will go unlogged.

### FP-02 — Blocking filesystem I/O in async handlers — HIGH

**Files:**
- `apps/form/src/emf_form/routes.py:331` — `case_dir.mkdir(...)` (sync)
- `apps/form/src/emf_form/routes.py:345` — `dest.write_bytes(header + rest)` (sync, up to 10 MB)
- `apps/panel/src/emf_panel/routes.py:326–329` — `attach_dir.is_dir()`, `attach_dir.iterdir()` (sync)
- `apps/panel/src/emf_panel/routes.py:960` — `path.exists()` (sync)
- `apps/tts/src/tts/main.py:54,66` — `os.unlink(path)` called from `_purge_expired()` in async handlers

All block the event loop. Uploads up to 10 MB (`dest.write_bytes`) are the most impactful. Use `asyncio.to_thread(dest.write_bytes, data)` or `anyio.to_thread.run_sync`. Directory/stat calls in request paths should also move to `asyncio.to_thread`.

### FP-03 — Dispatcher routes use `= None` with `# type: ignore[assignment]` as `Depends` default — MEDIUM

**File:** `apps/panel/src/emf_panel/routes.py:786–788`, `:853–855`, `:892–894`, `:917–919`

Four dispatcher routes declare `settings/session/redis` with `= None, # type: ignore[assignment]` then manually fall back with `if settings is None: settings = get_settings()`. FastAPI always resolves `Depends(...)` regardless of the default. Remove the `= None` defaults and the manual fallback guards.

### FP-04 — `_check_signal_token` checks wrong header name — MEDIUM

**File:** `apps/router/src/router/main.py:427–435`

FastAPI converts `x_signal_token` to header `x-signal-token`. If the signal webhook sender sends `X-Internal-Secret` (as `_check_internal_secret` expects), this guard always passes when `router_internal_secret` is set because the header names differ. Align header names or use `Header(alias="X-Signal-Token")` explicitly.

### FP-05 — `Depends(...)` as bare default (non-`Annotated`) in dependency functions — MEDIUM

**File:** `apps/router/src/router/main.py:418`, `:429`

`settings: Settings = Depends(get_settings)` without `Annotated` is the old FastAPI v0.x pattern — mypy cannot type-check it. Upgrade to `settings: Annotated[Settings, Depends(get_settings)]` for consistency.

### FP-06 — No `response_model=` on any JSON API route — MEDIUM

**Files:** `apps/panel/src/emf_panel/routes.py` (all `/api/v1/*`), `apps/router/src/router/main.py` (`/webhook/*`, `/internal/*`, `/ack/*`)

All API routes return `dict[str, object]` with no `response_model=`. OpenAPI schema shows `{}` for response bodies; FastAPI performs no output validation; Pydantic serialization optimisations are bypassed. Create lightweight `Response` Pydantic models for at least the mutation endpoints.

### FP-07 — `urgency` validated twice with divergent rule sets — LOW

**Files:** `apps/form/src/emf_form/schemas.py:143` (hardcoded `{"low","medium","high","urgent"}`) and `apps/form/src/emf_form/routes.py:163` (config-driven `config.urgency_levels`)

If `config.urgency_levels` is customised the two validators diverge. Remove the schema-level validator; use config-driven validation in the route as the single source of truth.

### FP-08 — `_app_config_cache` bypasses Pydantic immutability; non-thread-safe — LOW

**File:** `shared/src/emf_shared/config.py:103–111`

Uses `object.__setattr__` to bypass Pydantic model immutability, requires `# type: ignore`, and `hasattr` check has no lock. Since settings are already `lru_cache`d at service level via `get_settings()`, the per-instance cache is redundant. Parse `AppConfig` once in `model_post_init` and store as a proper field.

### FP-09 — `SlackAdapter` defines its own `URGENCY_EMOJI` diverging from shared module — LOW

**File:** `apps/router/src/router/channels/slack.py:13–18`

Defines its own emoji dict (🟢/🟡/🟠/🔴) vs `emf_shared.urgency` (📋/🔔/⚠️/🚨). Either document the intentional per-channel divergence or consolidate into the shared module.

### FP-10 — `_send_with_retry` repeats session open/close and `None`-check patterns — LOW

**File:** `apps/router/src/router/alert_router.py:147–211`

Three separate session context managers per notification with repeated `session.get(Notification, notif_id); if row is not None:` blocks across ~60 lines. Correct for correctness (not holding a session over `asyncio.sleep`), but fragile to maintain. A private `_update_notif_state` helper would reduce duplication.

### FP-11 — Mutable list defaults `[]` suppressed with `noqa: B006` — LOW

**File:** `apps/panel/src/emf_panel/routes.py:223–225`, `:364–365`, `:781`

`= [], # noqa: B006` is safe for FastAPI Query params, but `Query(default_factory=list)` is the idiomatic approach and avoids the suppression.

### FP-12 — `_service_name` global mutable in `emf_shared/logging.py` — LOW

**File:** `shared/src/emf_shared/logging.py:7,18–19`

No impact in production (separate processes). In test suites importing multiple services in one process, the last `configure_logging()` call wins and all log records get the wrong service name.

### FP-13 — `_audio_files` global dict mutated without lock — LOW

**File:** `apps/tts/src/tts/main.py:34`

`synthesise_file` has a TOCTOU race: `if not cache_path.exists(): ... _audio_files[token] = ...` can be entered by two concurrent identical requests. Low severity (idempotent — just wasted `_run_piper` work).

### FP-14 — `require_conduct_team` imports `get_settings` inside function body — LOW

**File:** `apps/panel/src/emf_panel/auth.py:72`

`from .settings import get_settings` is inside the function, triggering a `sys.modules` lookup on every authenticated request. Move to module level.

### Summary

| ID | Finding | Severity |
|---|---|---|
| FP-01 | Untracked `create_task` in `AlertRouter` — exceptions silently dropped | HIGH |
| FP-02 | Blocking filesystem I/O (`write_bytes`, `mkdir`, `iterdir`) in async handlers | HIGH |
| FP-03 | Dispatcher routes: spurious `= None` default + manual `if None` fallback on `Depends` | MEDIUM |
| FP-04 | `_check_signal_token` checks wrong header name | MEDIUM |
| FP-05 | `Depends(...)` as bare default (non-`Annotated`) in dependency functions | MEDIUM |
| FP-06 | No `response_model=` on any JSON API route | MEDIUM |
| FP-07 | `urgency` validated twice with divergent rule sets | LOW |
| FP-08 | `_app_config_cache` bypasses Pydantic immutability; non-thread-safe `hasattr` check | LOW |
| FP-09 | `SlackAdapter` defines its own `URGENCY_EMOJI` diverging from `emf_shared.urgency` | LOW |
| FP-10 | `_send_with_retry` repeats session open/close and `None`-check pattern 3 times | LOW |
| FP-11 | Mutable list defaults `[]` with `noqa: B006` suppression | LOW |
| FP-12 | `_service_name` global mutable in `logging.py` | LOW |
| FP-13 | `_audio_files` global dict mutated without lock — TOCTOU race | LOW |
| FP-14 | `require_conduct_team` imports `get_settings` inside function body | LOW |

---

## Performance Review

**Date:** 2026-07-12
**Scope:** Static analysis of all service source files, DB schema, and Redis usage patterns
**Context:** Single-host Docker Compose; peak ~hundreds of concurrent form submitters; ~10–20 panel users

---

### P-01 — Full table scan to generate friendly_id [HIGH]

**File:** `apps/form/src/emf_form/routes.py:210–211`

```python
existing_ids_result = await session.execute(select(Case.friendly_id))
existing_ids: set[str] = set(existing_ids_result.scalars().all())
```

Every form submission fetches **all** `friendly_id` values from the entire `cases` table to build a collision-check set. At festival scale this is a sequential scan returning O(N) rows over the wire just to verify a candidate ID that has ~1:wordlist⁴ collision probability. Fix: drop the pre-fetch entirely. Use a DB-level uniqueness constraint (already present via `UNIQUE` on `friendly_id`) and catch `IntegrityError` on INSERT, retrying with a new candidate. Zero rows transferred per submission.

---

### P-02 — Missing index on `cases.event_name` and `cases.assignee` [HIGH]

**File:** `infra/postgres/00_roles.sql:40–42`

`cases.event_name` is filtered in `dispatcher_view` (line 820) and `dispatcher_cases` (line 863). `cases.assignee` is filtered in `case_list` (line 248) and `dispatcher_view` (line 807). Neither column has an index. Both queries will sequential-scan `cases`.

Missing indexes:
- `CREATE INDEX cases_event_name_idx ON forms.cases (event_name);`
- `CREATE INDEX cases_assignee_idx ON forms.cases (assignee);`
- `CREATE INDEX cases_created_at_idx ON forms.cases (created_at DESC);` — used in all ORDER BY clauses

---

### P-03 — Missing index on `notifications.message_id` [HIGH]

**Files:** `apps/router/src/router/main.py:95, 284`

```python
select(Notification).where(Notification.message_id == target_ts)
```

Signal reaction polling runs every 10 seconds and looks up notifications by `message_id`. No index exists on `notifications.message_id` — full table scan on every poll tick.

Fix: `CREATE INDEX notifications_message_id_idx ON forms.notifications (message_id) WHERE message_id IS NOT NULL;`

---

### P-04 — `_ASSIGNEES_KEY` Redis set has no TTL [MEDIUM]

**File:** `apps/panel/src/emf_panel/routes.py:55, 545, 698`

```python
_ASSIGNEES_KEY = "panel:assignees"
await redis.sadd(_ASSIGNEES_KEY, body.assignee)
```

The `panel:assignees` set accumulates assignee names forever — across events, across years. No TTL, no expiry, no pruning. The set will contain stale names from previous events. For `list_assignees` correctness and memory hygiene, this should be scoped per-event or given a rolling TTL. Low memory impact at festival scale, but correctness concern.

---

### P-05 — `httpx.AsyncClient` created per-request in hot paths [MEDIUM]

**Files:**
- `apps/form/src/emf_form/routes.py:76` (Safe Browsing check per submission)
- `apps/panel/src/emf_panel/routes.py:659` (`_notify_router_ack` — called on every ACK)
- `apps/router/src/router/main.py:70` (Signal poll — every 10 s)

Each creates a new `httpx.AsyncClient`, which creates a new TCP connection pool, performs a TCP handshake (and TLS handshake where HTTPS), then discards the pool. This is unnecessary latency and connection churn. Fix: create a module-level or app-state `httpx.AsyncClient` with `keep_alive=True` (the default) and reuse it.

---

### P-06 — Synchronous file I/O in attachment upload (blocks event loop) [MEDIUM]

**File:** `apps/form/src/emf_form/routes.py:331–345`

```python
case_dir.mkdir(parents=True, exist_ok=True)
existing = (
    list(case_dir.glob("*.jpg")) + list(case_dir.glob("*.png")) + ...
)
dest.write_bytes(header + rest)
```

`Path.mkdir`, `Path.glob`, and `Path.write_bytes` are all synchronous blocking calls executed directly in an async handler, blocking the event loop for the duration of filesystem operations. For a 10 MB file write this is significant. Fix: wrap in `asyncio.to_thread(...)` or use `anyio.Path` async variants.

---

### P-07 — `list_tags` does a full JSONB scan with no index [MEDIUM]

**File:** `apps/panel/src/emf_panel/routes.py:641–644`

```python
text("SELECT DISTINCT jsonb_array_elements_text(tags) AS tag FROM forms.cases ORDER BY tag")
```

This unnests the `tags` JSONB array from every row in `cases` and deduplicates. No index can help a `jsonb_array_elements_text` function on an unindexed JSONB column without a GIN index. For small datasets this is fine, but it scales O(N). Fix: add `CREATE INDEX cases_tags_gin_idx ON forms.cases USING GIN (tags);` — PostgreSQL can use this for `@>` containment (also used in `case_list` tag filter at line 250) and the function can benefit from the GIN for element extraction.

---

### P-08 — Connection pool shared across all services at minimum size [MEDIUM]

**File:** `shared/src/emf_shared/db.py:21–22`

```python
pool_size=5,
max_overflow=10,
```

All three DB-connected services (form, panel, router) share the same `init_db` defaults: `pool_size=5, max_overflow=10` = 15 max connections each = up to 45 total. PostgreSQL 17's default `max_connections=100` leaves ~55 for overhead. This is adequate at this festival scale, but:
- No `pool_recycle` set — long-lived connections silently invalidated after PG timeout (default 10 min idle)
- `pool_pre_ping=True` mitigates this but adds one extra round-trip per checkout after a stale connection
- Consider `pool_recycle=300` to proactively refresh connections before the server drops them

---

### P-09 — `_send_with_retry` opens a new DB session per retry attempt [MEDIUM]

**File:** `apps/router/src/router/alert_router.py:158–196`

```python
for attempt_idx, delay_minutes in enumerate(RETRY_DELAYS_MINUTES):
    ...
    async with self._session_factory() as session:
        row = await session.get(Notification, notif_id)
        row.attempt_count = attempt_idx + 1
        await session.commit()
    ...
    async with self._session_factory() as session:
        row = await session.get(Notification, notif_id)
        row.state = NotifState.SENT
        await session.commit()
```

Per retry: two separate session open/close cycles, each doing a `SELECT` by PK then `COMMIT`. These could be combined into a single UPDATE statement per attempt, eliminating one session + one SELECT round-trip per retry cycle. Low priority at festival volume (4 retries × N channels), but wasteful.

---

### P-10 — `case_list` HTML page fetches all cases without pagination [MEDIUM]

**File:** `apps/panel/src/emf_panel/routes.py:235–257`

```python
stmt = select(Case).order_by(sort_expr)
...
result = await session.execute(stmt)
cases = result.scalars().all()
```

The HTML panel case list has no LIMIT/OFFSET. All matching cases are loaded into Python memory, iterated for map URL generation, then passed to the template. The API endpoint (`api_list_cases`) correctly paginates, but the HTML view does not. At festival scale with hundreds of reports this will slow down significantly and return large HTML responses to the 10–20 panel users.

---

### P-11 — `validate_dispatcher_token` makes 2–3 serial Redis round-trips per request [LOW]

**File:** `apps/panel/src/emf_panel/dispatcher.py:38–54`

```python
if await redis.exists(f"dispatcher:revoked:{jti}"):     # round-trip 1
    ...
is_known = await redis.sismember(devices_key, device_id) # round-trip 2
if not is_known:
    count = await redis.scard(devices_key)               # round-trip 3
    await redis.sadd(devices_key, device_id)             # round-trip 4
    await redis.expire(devices_key, ttl)                 # round-trip 5
```

All serial. Use a Redis pipeline or Lua script to collapse into 1–2 round-trips. Called on every dispatcher page load and every dispatcher API call.

---

### P-12 — `synthesise_file` purges expired audio on every request [LOW]

**File:** `apps/tts/src/tts/main.py:151, 168`

`_purge_expired()` is called synchronously at the start of both `synthesise_file` and `serve_audio`. It iterates `_audio_files` dict and calls `os.unlink` — blocking I/O in the async handler. Move to a periodic background task with `asyncio.create_task` in lifespan.

---

### P-13 — `config.json` re-read from disk on first access per process but no hot-reload [LOW]

**File:** `shared/src/emf_shared/config.py:104–111`

`app_config` property caches on the Settings instance, which is itself `lru_cache`'d in each service. This is correct for production. No concern for the load profile, but if `config.json` changes, a process restart is required with no warning or detection. Non-performance, informational.

---

### Summary — Performance Risk Register

| # | Area | Impact | Finding |
|---|---|---|---|
| P-01 | DB — full scan | HIGH | Full `friendly_id` table fetch on every form submission |
| P-02 | DB — missing indexes | HIGH | `event_name`, `assignee`, `created_at` unindexed |
| P-03 | DB — missing index | HIGH | `notifications.message_id` unindexed; Signal poll hits full scan every 10 s |
| P-04 | Redis — no TTL | MEDIUM | `panel:assignees` set grows unbounded across events |
| P-05 | HTTP — connection churn | MEDIUM | New `httpx.AsyncClient` per request in hot paths |
| P-06 | File I/O — blocks event loop | MEDIUM | Sync `mkdir`/`glob`/`write_bytes` in async attachment handler |
| P-07 | DB — GIN index missing | MEDIUM | `list_tags` full JSONB scan; tag containment filter also unindexed |
| P-08 | DB — pool recycle | MEDIUM | No `pool_recycle`; stale connections silently dropped by server |
| P-09 | DB — extra round-trips | MEDIUM | Retry loop opens redundant sessions; could use single UPDATE |
| P-10 | Panel — no pagination | MEDIUM | HTML case list returns all cases; no LIMIT |
| P-11 | Redis — serial round-trips | LOW | Dispatcher token validation: up to 5 serial Redis calls |
| P-12 | TTS — sync purge in handler | LOW | `_purge_expired` blocks event loop; should be background task |
| P-13 | Config — no hot-reload | LOW | `config.json` requires process restart to pick up changes |

---

## GDPR/Privacy Review

**Date:** 2026-07-12
**Reviewer:** Claude (static code analysis)
**Basis:** Review of current codebase at `/work` — supplemental to the GDPR Compliance Audit above (2026-07-11). Only findings **not already captured** in that audit are listed here.

---

### G-15 — /metrics endpoint publicly accessible (MEDIUM)

All four apps expose unauthenticated Prometheus metrics at `/metrics` via `Instrumentator().instrument(app).expose(app, endpoint="/metrics")`:

- `/work/apps/form/src/emf_form/main.py` line 79
- `/work/apps/panel/src/emf_panel/main.py` line 62
- `/work/apps/router/src/router/main.py` line 513

None of the Caddy configs (`/work/infra/caddy/Caddyfile.prod`, `Caddyfile.wolfcraig`) block `/metrics` from public routes — every vhost does a blanket `reverse_proxy` with no path restrictions. Anyone who requests `https://report.emf.camp/metrics` receives submission counts by urgency, phase, and event name (`emf_cases_submitted_total` counter labels at `main.py` lines 27-31), plus HTTP latency histograms and per-path request counts.

While not direct PII, submission volume at specific urgency levels during an event is operationally sensitive and reveals operational information about the conduct team. The ICO considers metadata aggregation a privacy concern where it can allow inference about individuals (e.g. spike in "urgent" submissions at 2am could be inferred as a serious incident).

**Fix:** Block `/metrics` at the Caddy layer in all vhosts (`respond /metrics 404`) and restrict Prometheus scraping to the internal Docker network, which already works (Prometheus scrapes `form:8000`, etc. internally per `/work/infra/prometheus/prometheus.yml`).

---

### G-16 — Dispatcher token in URL query string appears in access logs (LOW-MEDIUM)

`GET /dispatcher?token=<jwt>` and `GET /api/v1/dispatcher/cases?token=<jwt>` pass the dispatcher JWT as a query parameter (defined at `/work/apps/panel/src/emf_panel/routes.py` lines 781 and 851).

Uvicorn's default access log format logs the full request path including query string. If access logs are forwarded to any aggregator (Docker log driver, syslog, Grafana Loki), the JWT is stored in plaintext. While the token has a configurable TTL (default 8 hours per `dispatcher_session_ttl_hours`), it grants case-view access to the dispatcher panel and can be replayed by anyone who reads the logs within the TTL window.

`acked_by` is set to `"dispatcher"` (static string) for dispatcher ACKs so acked-by attribution is not a concern, but the token itself is the access credential.

**Fix:** Move the token to an `Authorization: Bearer` header for API calls, or set it as an `HttpOnly` cookie at first load and use the cookie for subsequent API calls (the `device_id` cookie pattern at line 844 already shows this approach). If query param is kept, explicitly pass `--no-access-log` to uvicorn or ensure log rotation and access controls are covered by the data retention policy.

---

### G-17 — Pydantic v2 `exc.errors()` includes submitted values in WARNING logs (LOW)

`/work/apps/form/src/emf_form/main.py` line 71:

```python
_log.warning("422 on %s: %s", request.url.path, exc.errors())
```

In Pydantic v2, `ValidationError.errors()` returns a list of dicts that include an `input` key containing the **actual submitted value** for each failing field. This fires at WARNING level (not DEBUG) on every validation error on the public `/api/submit` endpoint. If a reporter submits an invalid value in `reporter.email` (e.g. `adam.smith@` — truncated address), the string `adam.smith@` appears verbatim in structured logs.

The prior audit noted this at G-11 as a potential concern only at DEBUG level. The more specific finding is that it is a certainty at WARNING level in Pydantic v2 regardless of log level configuration.

**Fix:** Strip `input` from each error before logging:

```python
scrubbed = [{k: v for k, v in e.items() if k != "input"} for e in exc.errors()]
_log.warning("422 on %s: %s", request.url.path, scrubbed)
```

---

### G-18 — Volunteer display names stored indefinitely in audit trail (LOW)

`forms.notifications.acked_by` (VARCHAR 128) and `forms.case_history.changed_by` (VARCHAR 128) store the display name of the conduct team volunteer who handled each action. The value comes from `_username(user)` at `/work/apps/panel/src/emf_panel/routes.py` lines 186-192, which prefers `preferred_username`, then `name`, then `sub`, then `email` from OIDC claims. On EMFcamp's UFFD-based IdP, `name` may be a real full name and `email` a personal address.

These are staff/volunteer personal data and are stored indefinitely alongside case data (no retention mechanism — covered at G-03 for cases, but the volunteer data has its own distinct legal basis and retention question). The conduct team volunteers are not reported as having been notified that their names are recorded per handling action.

**Action:** Include conduct team volunteer data in the internal Article 13/14 notice given to team members at onboarding. Confirm the retention period for `case_history` and `notifications` covers this data and is communicated to volunteers.

---

### G-19 — No disclosure that submitted URLs are checked via Google Safe Browsing (LOW)

`/work/apps/form/src/emf_form/routes.py` lines 197-208 — when `media_links` is present and `GOOGLE_SAFE_BROWSING_API_KEY` is set, each URL submitted by the reporter is sent to `https://safebrowsing.googleapis.com/v4/threatMatches:find` (Google LLC, US). The reporter is not informed of this at submission time.

The `media_links` field could contain URLs that indirectly identify the reporter (e.g. a link to their own social media post submitted as evidence). The form HTML (`/work/apps/form/templates/form.html`) has no disclosure near the `media_links` field about this processing.

The prior audit noted Google Safe Browsing at G-07 as a processor concern (DPA/transfer). This is a distinct Article 13 disclosure gap on the form itself.

**Fix:** Add a one-line hint beneath the `media_links` field in `form.html`: "Links may be checked against Google Safe Browsing for safety."

---

### Supplemental Risk Register

| # | Area | Severity | Finding |
|---|---|---|---|
| G-15 | /metrics public | MEDIUM | All apps expose unauthenticated Prometheus metrics through Caddy — no path restriction in prod vhosts |
| G-16 | Dispatcher token in URL | LOW-MEDIUM | JWT in query string logged by uvicorn access log; replay possible within TTL |
| G-17 | 422 logs submitted values | LOW | Pydantic v2 `exc.errors()` includes `input` key; fires at WARNING on public form endpoint, not just DEBUG |
| G-18 | Volunteer names stored indefinitely | LOW | `acked_by`/`changed_by` store real OIDC display names; no separate retention schedule or Article 13 notice to volunteers |
| G-19 | Safe Browsing disclosure absent | LOW | Reporter not informed submitted URLs are sent to Google API; Article 13 gap on form |

---

## Architecture Review (Pass 2)

**Date:** 2026-07-12
**Reviewer:** Claude (static code analysis)
**Scope:** Second-pass architectural review — new findings not already documented in prior sections.

---

### AR2-01 — HIGH: Panel service missing `attachments` volume mount — attachment serving always broken

The `form` service mounts `attachments:/app/attachments` (`infra/docker-compose.yml:37`). The `panel` service has no such mount. Yet `emf_panel/routes.py` reads from `settings.attachment_dir` (default `/app/attachments`) in two routes:

- `case_detail` (line 324): globs attach_dir for filenames
- `serve_attachment` (line 959): serves `FileResponse` from that path

In the panel container `/app/attachments` does not exist as a shared volume, so `case_detail` always returns an empty attachment list and `serve_attachment` always raises 404. Attachments uploaded via the form are invisible to panel users.

**Fix:** Add `volumes: - attachments:/app/attachments:ro` to the `panel` service block in `infra/docker-compose.yml`. The `:ro` flag is appropriate — panel only reads. The form-service volume mount (RT-BUG-02) must also be present.

---

### AR2-02 — HIGH: `EMFPhoneAdapter._trigger_ack` calls back into its own process via HTTP — latent double-notification risk

`apps/router/src/router/channels/emf_phone.py:136–148` — on phone `ACKNOWLEDGE`, `_trigger_ack` POSTs to `{router_self_url}/internal/ack/{case_id}`. The internal ACK handler then calls `alert_router.send_ack_to_all_channels(...)`, which fires `asyncio.create_task(adapter.send_ack_confirmation(...))` for every channel. `EMFPhoneAdapter.send_ack_confirmation` is currently `pass`, so no loop occurs.

Risk: if `send_ack_confirmation` is ever implemented for phone, or if the `internal/ack` call runs concurrently with another ACK trigger (e.g. the panel conductor ACKs the same case simultaneously), `send_ack_to_all_channels` will fire twice. There is no idempotency key on the `/internal/ack` endpoint. A second call produces a duplicate `CaseHistory` row.

Better design: have `EMFPhoneAdapter.send()` return a sentinel that `_send_with_retry` understands, then call `mark_acked` in-process, eliminating the self-HTTP round-trip entirely.

---

### AR2-03 — MEDIUM: Case list HTML view loads all cases with no pagination

`apps/panel/src/emf_panel/routes.py:235–256` — `stmt = select(Case).order_by(sort_expr)` with no LIMIT. The REST API equivalent (`/api/v1/cases`, line 369) has correct `limit`/`offset` pagination. The HTML view fetches every case row, iterates them for `map_urls`, then issues a second bulk query for `notif_states`. At hundreds of cases this is manageable; at multi-year accumulated data (no retention enforcement per GDPR finding G-03) this becomes a full table scan on every panel page load.

**Fix:** Add `page`/`per_page` query params (default 50) to the HTML view, mirroring the API.

---

### AR2-04 — MEDIUM: `LOCAL_DEV=true` is the default in compose for `form` and `msg-router` — prod routing silently wrong if `.env` is incomplete

`infra/docker-compose.yml` lines 34 and 114:
```
LOCAL_DEV: ${LOCAL_DEV:-true}
```

In `alert_router.py:68`, `LOCAL_DEV=true` forces `phase = Phase.EVENT_TIME` regardless of event dates, enabling all notification channels. The compose defaults to `true`, meaning any deployment that does not explicitly set `LOCAL_DEV=false` in `.env` will route every case through Signal, phone, Mattermost, and email — including pre-event or post-event periods.

**Fix:** Change compose defaults to `${LOCAL_DEV:-false}`. Document `LOCAL_DEV=true` as a dev-only `.env` override in `.env-example`.

---

### AR2-05 — MEDIUM: Version string `"0.1.0"` hardcoded in all four health endpoints

All four services hardcode `"version": "0.1.0"` in their `/health` response (`routes.py:370`, `routes.py:980`, `main.py:509`, `tts/main.py:191`). The value is never updated. This makes it impossible to detect version skew between deployed services from health checks or Grafana.

**Fix:** Read from package metadata via `importlib.metadata.version("emf-form")` etc. at startup, or inject via a `BUILD_VERSION` environment variable set in the Dockerfile `ARG`/`ENV` block.

---

### AR2-06 — MEDIUM: No `UNIQUE (case_id, channel)` constraint on `notifications` — deduplication is application-level TOCTOU

`infra/postgres/00_roles.sql:56–70` — `notifications` has indexes on `case_id` and `state` but no uniqueness constraint on `(case_id, channel)`. The app checks for existing notifications in `_handle_new_case` before routing, but this is a non-atomic read-then-write. Two concurrent `retrigger_case` events (e.g. double-click on the panel "retrigger" button) can both pass the count check and spawn duplicate routing tasks, resulting in two notification rows per channel and double-delivery.

**Fix:** Add `ALTER TABLE forms.notifications ADD CONSTRAINT notifications_case_channel_uq UNIQUE (case_id, channel);` and change the insert to `INSERT ... ON CONFLICT DO NOTHING`. The application guard becomes a performance optimisation rather than a correctness control.

---

### AR2-07 — LOW: Signal polling loop and Signal webhook endpoint both active simultaneously — duplicate ACK processing risk

`apps/router/src/router/main.py:56–113` (`_poll_signal_reactions`) and `@api.post("/webhook/signal")` (lines 263–299) implement the same emoji-reaction ACK logic. Both run concurrently when `signal_api_url` and `signal_sender` are configured. Signal-cli REST API queues messages per-client, so a reaction consumed by the webhook is removed from the poll queue. However under race conditions (webhook fires while a poll is mid-request) both paths can attempt `mark_acked` for the same notification. The second call is a no-op at the DB level, but `send_ack_to_all_channels` would fire twice, sending a duplicate ACK confirmation email/Mattermost message.

**Fix:** Remove the polling loop. The webhook is lower-latency and strictly better. If polling is needed as a fallback for environments without public webhook reachability, gate it on an explicit `SIGNAL_POLL_FALLBACK=true` setting so both are never active simultaneously.

---

### AR2-08 — LOW: `TTS` audio token index is in-process memory — lost on restart, leaving orphaned WAV files

`apps/tts/src/tts/main.py:34`: `_audio_files: dict[str, tuple[str, float, bool]] = {}`. Tokens issued by `/synthesise/file` are valid only while the process is alive. A TTS restart between synthesis and phone playback yields a 404 for the audio URL, silently failing the phone call announcement. The WAV file also remains on disk indefinitely (since cleanup only runs via `lifespan` on clean exit).

The cache path is already content-addressed (`sha256(text)` as filename, line 154). A simpler design: serve audio directly as `/audio/{sha256}` with no token layer — files exist on disk or they do not. The token adds no security value since TTS is unauthenticated anyway.

---

### Summary — New Findings (Pass 2)

| ID | Severity | Finding |
|---|---|---|
| AR2-01 | HIGH | Panel `attachments` volume not mounted — all attachment serving broken in production |
| AR2-02 | HIGH | `EMFPhoneAdapter._trigger_ack` self-HTTP call is a latent double-notify risk |
| AR2-03 | MEDIUM | Panel case list HTML view has no pagination — full table scan on every load |
| AR2-04 | MEDIUM | `LOCAL_DEV=true` compose default forces event-time routing in any deployment missing `.env` |
| AR2-05 | MEDIUM | `"0.1.0"` hardcoded in all health endpoints — version detection impossible post-deploy |
| AR2-06 | MEDIUM | No `UNIQUE (case_id, channel)` constraint — duplicate notification delivery under concurrent retriggering |
| AR2-07 | LOW | Signal polling + webhook both active simultaneously — race condition yields duplicate ACK confirmations |
| AR2-08 | LOW | TTS audio token state lost on restart; content-addressed serving would eliminate the gap |

## PostgreSQL Review

Supplementary findings not covered in the existing `## PostgreSQL Audit` section. All line references are to `infra/postgres/00_roles.sql` unless stated otherwise.

### Summary table

| ID | Severity | Area | Finding |
|---|---|---|---|
| PR-01 | HIGH | Schema | `case_history.changed_by`: `NOT NULL` in SQL (line 47), `nullable=True` in both ORM models |
| PR-02 | HIGH | Schema | `idempotency_tokens.token`: `VARCHAR(64)` in SQL (line 73), `String(256)` in form ORM model |
| PR-03 | MEDIUM | Index | Missing composite index on `notifications(case_id, state)` — queried together on every case list load |
| PR-04 | MEDIUM | Index | Missing index on `cases.assignee` — filtered on panel routes lines 248, 807, 864 |
| PR-05 | MEDIUM | Index | Missing index on `cases.event_name` — filtered on panel routes line 820 (dispatcher view) |
| PR-06 | MEDIUM | Index | Missing index on `cases.created_at` — used as primary ORDER BY on panel routes lines 369, 817 |
| PR-07 | MEDIUM | Schema | No CHECK constraints on enum-like VARCHAR columns (`urgency`, `status`, `phase`, `channel`, `state`) |
| PR-08 | MEDIUM | Schema | `idempotency_tokens` has no TTL or cleanup job; grows forever |
| PR-09 | LOW | Index | No partial index for the hot `WHERE assignee IS NULL` dispatcher path |
| PR-10 | LOW | FK | All FK constraints have no `ON DELETE` rule; case deletion requires explicit ordering (relevant to GDPR erasure, G-04) |
| PR-11 | LOW | Performance | `_NOTIF_SORT` correlated subquery in panel routes executes per case row when sorted by notif |
| PR-12 | LOW | Role | `service_user` role is granted UPDATE on cases but no service connects as it; dead code with standing privilege |

---

### PR-01 — HIGH: `case_history.changed_by` nullable mismatch — SQL NOT NULL, ORM nullable

**Evidence:**

`infra/postgres/00_roles.sql` line 47:
```sql
changed_by  VARCHAR(128) NOT NULL,
```

`apps/form/src/emf_form/models.py` line 49 and `apps/panel/src/emf_panel/models.py` line 49:
```python
changed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
```

**Consequence:** The ORM annotation permits `None`. Any `CaseHistory` construction that omits `changed_by` will attempt to insert `NULL` into a `NOT NULL` column and fail at runtime with a Postgres constraint violation. No current call site triggers this — all pass explicit values — but the annotation is a latent trap for future callers following the type signature.

**Fix:** Change both ORM models: `changed_by: Mapped[str] = mapped_column(String(128), nullable=False)`.

---

### PR-02 — HIGH: `idempotency_tokens.token` column length mismatch — SQL VARCHAR(64), ORM String(256)

**Evidence:**

`infra/postgres/00_roles.sql` line 73:
```sql
token      VARCHAR(64) PRIMARY KEY,
```

`apps/form/src/emf_form/models.py` line 64:
```python
token: Mapped[str] = mapped_column(String(256), primary_key=True)
```

**Consequence:** An `X-Idempotency-Key` header between 65 and 256 characters passes ORM validation but fails at Postgres with `value too long for type character varying(64)`. The mismatch is invisible at the application layer until runtime.

**Fix:** Align definitions. Change ORM to `String(64)` and add `max_length=64` on `x_idempotency_key` in `apps/form/src/emf_form/routes.py`. If longer keys are needed, change the SQL column to `VARCHAR(256)`.

---

### PR-03 — MEDIUM: Missing composite index on `notifications(case_id, state)`

**Evidence:**

Existing separate indexes (`infra/postgres/00_roles.sql` lines 69–70) cover each column alone. All hot query paths filter both columns together:
- `apps/panel/src/emf_panel/routes.py` lines 202, 241, 681, 904
- `apps/router/src/router/alert_router.py` lines 238–244

**Consequence:** Postgres picks one single-column index and re-checks the other predicate in memory rather than going directly to matching rows.

**Fix:**
```sql
CREATE INDEX notifications_case_state_idx ON forms.notifications (case_id, state);
DROP INDEX IF EXISTS notifications_case_idx;  -- redundant; leftmost column of composite covers it
```

---

### PR-04 — MEDIUM: Missing index on `cases.assignee`

**Evidence:**

`apps/panel/src/emf_panel/routes.py`:
- Line 248: `WHERE assignee = ?` — panel list filtered by name
- Line 807: `WHERE assignee IS NULL` — dispatcher unassigned list
- Line 864: `WHERE assignee IS NULL` — dispatcher API

No index on `forms.cases(assignee)` in `infra/postgres/00_roles.sql`.

**Fix:**
```sql
CREATE INDEX cases_assignee_idx ON forms.cases (assignee);
```

---

### PR-05 — MEDIUM: Missing index on `cases.event_name`

**Evidence:**

`apps/panel/src/emf_panel/routes.py` line 820:
```python
stmt = stmt.where(Case.event_name == active_event)
```
This is the dispatcher hot read path — polled repeatedly. No index on `forms.cases(event_name)` in `infra/postgres/00_roles.sql`.

**Fix:**
```sql
CREATE INDEX cases_event_name_idx ON forms.cases (event_name);
```

---

### PR-06 — MEDIUM: Missing index on `cases.created_at`

**Evidence:**

`apps/panel/src/emf_panel/routes.py`:
- Line 369: `ORDER BY created_at DESC` — default sort for API list
- Line 817: `ORDER BY ..., created_at ASC` — dispatcher secondary sort

No index on `forms.cases(created_at)`. Every `ORDER BY created_at` needs a full scan and sort.

**Fix:**
```sql
CREATE INDEX cases_created_at_idx ON forms.cases (created_at DESC);
```

---

### PR-07 — MEDIUM: No CHECK constraints on enum-like VARCHAR columns

**Evidence:**

`infra/postgres/00_roles.sql` uses raw `VARCHAR` for columns with a fixed valid set — no database-level `CHECK` constraints:
- `cases.urgency VARCHAR(16)` — valid: `low`, `medium`, `high`, `urgent`
- `cases.status VARCHAR(32)` — valid: `new`, `assigned`, `in_progress`, `action_needed`, `decision_needed`, `closed`
- `cases.phase VARCHAR(16)` — valid: `pre_event`, `event_time`, `post_event`
- `notifications.channel VARCHAR(32)` — valid: `email`, `signal`, `mattermost`, `slack`, `telephony`
- `notifications.state VARCHAR(16)` — valid: `pending`, `sent`, `failed`, `acked`

**Consequence:** Direct `psql` inserts or migration scripts can store arbitrary values. Application-layer guards (`VALID_TRANSITIONS`, Pydantic) are the only enforcement.

**Fix:**
```sql
ALTER TABLE forms.cases ADD CONSTRAINT cases_urgency_chk
    CHECK (urgency IN ('low','medium','high','urgent'));
ALTER TABLE forms.cases ADD CONSTRAINT cases_status_chk
    CHECK (status IN ('new','assigned','in_progress','action_needed','decision_needed','closed'));
ALTER TABLE forms.notifications ADD CONSTRAINT notifs_state_chk
    CHECK (state IN ('pending','sent','failed','acked'));
ALTER TABLE forms.notifications ADD CONSTRAINT notifs_channel_chk
    CHECK (channel IN ('email','signal','mattermost','slack','telephony'));
```

---

### PR-08 — MEDIUM: `idempotency_tokens` never purged

**Evidence:**

`infra/postgres/00_roles.sql` lines 72–76 — `idempotency_tokens` has `created_at` but no TTL. No application code or script deletes from this table. Tokens accumulate permanently across festival deployments.

**Consequence:** No documented expiry window for clients. Table grows indefinitely; no mechanism for clients to know when a key can be safely reused.

**Fix:** Add a cleanup step to `scripts/backup.py` or a dedicated maintenance script:
```sql
DELETE FROM forms.idempotency_tokens WHERE created_at < NOW() - INTERVAL '7 days';
```
Document the 7-day window in the API contract for `X-Idempotency-Key`.

---

### PR-09 — LOW: No partial index for `WHERE assignee IS NULL`

**Evidence:** `apps/panel/src/emf_panel/routes.py` lines 807 and 864 both use `WHERE assignee IS NULL AND urgency IN (...)`. The dispatcher path is the most frequent read query. A partial composite index would be smaller and faster.

**Fix (extends PR-04):**
```sql
CREATE INDEX cases_unassigned_urgency_idx ON forms.cases (urgency, created_at)
    WHERE assignee IS NULL;
```
Covers `WHERE assignee IS NULL AND urgency IN (...) ORDER BY urgency, created_at` without scanning assigned cases.

---

### PR-10 — LOW: FK constraints have no `ON DELETE` rule; case deletion requires explicit ordering

**Evidence:**

`infra/postgres/00_roles.sql` lines 46, 58, 74 — all three child tables reference `forms.cases(id)` with the default `NO ACTION`. No documented deletion sequence exists in `scripts/` or runbooks.

**Consequence:** A GDPR erasure request (see G-04) hits FK violation errors without a script. Correct deletion order: `idempotency_tokens` → `notifications` → `case_history` → `cases`.

**Fix:** Script and document the deletion sequence. Do not add `ON DELETE CASCADE` to `case_history` — cascade would silently destroy audit evidence on accidental parent deletion.

---

### PR-11 — LOW: `_NOTIF_SORT` correlated subquery executes per row in case list

**Evidence:**

`apps/panel/src/emf_panel/routes.py` lines 200–205:
```python
_NOTIF_SORT = (
    select(func.count())
    .where(Notification.case_id == Case.id, Notification.state == "acked")
    .correlate(Case)
    .scalar_subquery()
)
```

When `sort=notif` is requested, Postgres evaluates this subquery once per case row. For 500 cases that is 500 correlated executions against `forms.notifications`. The panel already batch-fetches `notif_states` (routes.py lines 272–284) for display — the sort should use the same aggregation via a join.

**Fix:** Replace the correlated subquery with a lateral join or CTE:
```sql
LEFT JOIN (
    SELECT case_id, COUNT(*) FILTER (WHERE state = 'acked') AS ack_count
    FROM forms.notifications GROUP BY case_id
) n ON n.case_id = cases.id
ORDER BY ack_count
```

---

### PR-12 — LOW: `service_user` role is dead code with standing UPDATE privilege

**Evidence:**

`infra/postgres/00_roles.sql` lines 102–104 grant `service_user` column-level SELECT and UPDATE on `forms.cases` and INSERT on `forms.case_history`. `infra/postgres/00_init.sh` creates the role with a password (`${SERVICE_DB_PASSWORD}`). No service in `apps/` connects as `service_user`.

**Consequence:** Unused role with UPDATE privileges is a standing attack surface if `SERVICE_DB_PASSWORD` is compromised.

**Fix:** If reserved for a future automation service, document this explicitly. Otherwise:
```sql
REVOKE ALL ON forms.cases FROM service_user;
REVOKE ALL ON forms.case_history FROM service_user;
DROP ROLE IF EXISTS service_user;
```
And remove the corresponding `CREATE ROLE` line from `infra/postgres/00_init.sh`.

---

## SRE Review

**Date:** 2026-07-12
**Reviewer:** Senior SRE (automated analysis)
**Scope:** Single-host Docker Compose deployment, 4-day festival event window
**Context:** Critical system — missed reports mean people in distress don't get help

---

### 1. SLO Candidates

Recommended SLOs for the festival window:

| Signal | Target | Rationale |
|---|---|---|
| Form availability (HTTP 2xx on `/health`) | 99.5% (≤43 min downtime/4 days) | Public-facing; must accept reports at all times |
| Form submission p95 latency | < 3 s | Submitter experience; includes DB write |
| Notification dispatch p95 latency | < 60 s | Time from pg_notify to first delivery attempt |
| Notification delivery success rate | ≥ 95% per channel | At least one channel must deliver per case |
| Panel availability (authenticated users) | 99% (≤58 min/4 days) | Conduct team can tolerate brief outages |
| Unacknowledged case age p95 | < 5 min | Operational SLO: team must respond fast |

None of these SLOs are currently defined or measured. `emf_notification_dispatch_seconds` histogram is registered but **never observed** — `notification_dispatch_seconds` is created in `router/main.py` lines 39–43 but `.observe()` is never called anywhere in the codebase. `SlowNotificationDispatch` alert will never fire.

---

### 2. Runbook Gaps

**SRE-01 [CRITICAL] — No Alertmanager; alerts are silent**

`prometheus.yml` has no `alerting:` section and no Alertmanager is in the compose file or monitoring profile. Alert rules are evaluated but notifications go nowhere. At 3am, `ListenerSilent` or `ServiceDown` fires and no one is woken up. Fix: add Alertmanager to the monitoring profile with a Mattermost/Signal/email receiver, or wire Prometheus remote-write to an external alerting endpoint.

**SRE-02 [CRITICAL] — `dispatch_seconds` histogram never observed; `SlowNotificationDispatch` alert blind**

`notification_dispatch_seconds` Histogram (`router/main.py:39`) is created but `.observe()` is never called in `_send_with_retry` or anywhere else. The `SlowNotificationDispatch` alert (`alert_rules.yml:63`) will never fire. Fix: wrap the send call in `_send_with_retry` with `with notification_dispatch_seconds.labels(channel=channel_name).time():`.

**SRE-03 [CRITICAL] — `ListenerSilent` alert expression references non-existent metric**

`alert_rules.yml:8` uses `emf_notification_state_total_created` — this metric does not exist. `emf_notification_state_total` is a Counter with labels `channel` and `state` only. The `or absent(emf_notification_state_total)` branch fires immediately on startup (before any notification), causing a spurious CRITICAL alert on every cold start. Fix: use `increase(emf_notification_state_total[1h]) == 0` scoped to the router job, with a startup inhibit window.

**SRE-04 [HIGH] — No restore procedure documented or tested**

`scripts/backup.py` produces encrypted `.dump.zst.age` files. There is no corresponding restore script and no documented procedure anywhere in the repo. A 3am DB corruption event requires ad-hoc improvisation. Fix: add `scripts/restore.py --backup <file>` and document a tested restore drill before the event.

**SRE-05 [HIGH] — PENDING notifications not recovered after router restart**

`_send_with_retry` writes a `PENDING` notification row then dispatches in-memory via `asyncio.create_task`. If the router restarts mid-flight (between PENDING write and SENT update), the notification row stays `PENDING` forever with no retry. The resolution summary lists this as fixed, but the current code in `listener.py` and `alert_router.py` has no startup recovery sweep — no query for `WHERE state = 'pending'` at boot. Either the fix was reverted or never landed. Fix: on router startup, query for PENDING notifications older than 60s and retrigger via `NOTIFY retrigger_case, '<uuid>'`.

**SRE-06 [HIGH] — `docker compose up -d` causes full downtime on every update**

`scripts/prod-update` runs `docker compose up -d` which recreates all containers. Every deploy drops the form and panel for 5–15s per service. During active event hours this risks losing a report submission in progress. Fix: deploy one service at a time with `--no-deps`; add a post-deploy health check.

**SRE-07 [HIGH] — No on-call runbook**

No `runbook.md` or ops guide covers: how to access logs, how to check DB connectivity, how to manually retrigger a notification (`NOTIFY retrigger_case, '<uuid>'`), how to restart a single service, or escalation contacts. Fix: create `docs/runbook.md` with failure mode → diagnosis → remediation for each CRITICAL alert.

---

### 3. Observability

**SRE-08 [HIGH] — Trace IDs not verified in anyio thread path (email/Resend)**

`outbound_headers()` propagates `X-Trace-ID` to Signal, Mattermost, Slack, and EMF phone adapters. `EmailAdapter` never calls `outbound_headers()`. More importantly, `anyio.to_thread.run_sync()` creates a new OS thread; Python `contextvars.ContextVar` values are copied by default only if `anyio` passes the context explicitly. Verify that trace IDs appear in Resend failure logs.

**SRE-09 [MEDIUM] — No end-to-end submit→notify latency metric**

The system has `emf_cases_submitted_total` (form) and `emf_notification_state_total` (router) but nothing correlating the two with a timestamp delta. Total dispatch latency (form POST → pg_notify → listener wakeup → channel send) is invisible. Fix: expose `emf_case_notification_lag_seconds` histogram from the router using the case's `created_at` field (already on `CaseAlert`).

**SRE-10 [MEDIUM] — Monitoring profile is opt-in; zero observability on default stack**

If `--profile monitoring` is not used, Prometheus and Grafana do not run. Alert rules are never evaluated. `prod-update` does not include the monitoring profile. Recommend either making Prometheus part of the core stack, or explicitly documenting that `--profile monitoring` is required in production.

**SRE-11 [LOW] — node_exporter absent; DiskUsageHigh never fires**

`alert_rules.yml:49` notes "requires node_exporter — skip silently if absent". node_exporter is not in `docker-compose.yml`. Disk can fill silently. Fix: add node_exporter to the monitoring profile.

---

### 4. Alert Quality

| Alert | Issue |
|---|---|
| `ListenerSilent` | Expression uses non-existent metric; `absent()` false-fires on cold start — **broken** |
| `SlowNotificationDispatch` | Histogram never observed; always no-data — **broken** |
| `ServiceDown` | `up == 0` for 1 min — correct |
| `NotificationFailed` | `for: 0m`, instant on any failed delivery — correct |
| `FormHighErrorRate` | Division-by-zero → NaN → false; acceptable |
| `DiskUsageHigh` | node_exporter absent — **never fires** |
| Missing | No alert on Redis down |
| Missing | No alert on case submitted with no SENT notification after 2 min — most important operational signal |

---

### 5. Backup and Restore

**SRE-12 [HIGH] — No restore script or tested restore procedure**

`scripts/backup.py` is solid (pg_dump custom format, zstd, age encryption, optional rsync). Gaps:
- No `scripts/restore.py` exists
- `--rsync` optional; if not configured, backups stay on the same host (single point of failure)
- Systemd unit calls `sys.executable` — may be system Python, not venv Python
- No backup verification (row count check, test restore to scratch DB)

**SRE-13 [MEDIUM] — Backup timer not installed by `install.py`**

The systemd timer must be installed manually via `python backup.py --systemd`. If the operator forgets, there are no automated backups during the event.

---

### 6. Secret Rotation

**SRE-14 [HIGH] — Rotating `SECRET_KEY` invalidates all outstanding email ACK links**

`SECRET_KEY` signs email ACK tokens (JWT). Rotating it immediately invalidates all tokens in in-flight emails. During an active event, this breaks acknowledge links in any email already sent. Fix: support a `SECRET_KEY_PREV` fallback in `decode_ack_token` so the old key is accepted during a rotation window.

**SRE-15 [MEDIUM] — Secret rotation requires full service restart**

`SECRET_KEY`, `REDIS_PASSWORD`, `RESEND_API_KEY`, `OIDC_CLIENT_SECRET`, and SMTP credentials are read at startup only. Rotating any of them mid-festival requires `docker compose up -d` with the associated downtime.

---

### 7. Deployment Process

**SRE-16 [HIGH] — Single-worker uvicorn in production**

All services run uvicorn with no `--workers` flag (defaults to 1). Form and panel handle all traffic in a single asyncio event loop. Under load, a slow DB query stalls all requests. Fix: `--workers 2` for form and panel (stateless). Router must stay at 1 worker (pg_notify listener is per-process).

**SRE-17 [MEDIUM] — `prod-update` loses prod compose config if `git pull` fails**

`prod-update` does `git checkout HEAD -- infra/docker-compose.yml` then `git pull` then `git restore --source=prod`. If the pull fails, the prod compose override is lost until manually restored. Fix: copy the prod file to a temp location before the pull.

---

### 8. Capacity Limits

**SRE-18 [MEDIUM] — No resource limits on any container**

No `mem_limit`, `cpus`, or `deploy.resources` constraints. A TTS synthesis burst or ClamAV scan can OOM-kill postgres. Fix: set limits on tts (512m), signal-api (256m), clamav (1g).

**SRE-19 [MEDIUM] — Rate limiter is in-memory; bypassed under multi-worker**

`slowapi` in the form service uses in-memory counters per worker process. With `--workers N`, an attacker gets N× the effective rate limit. Fix: use Redis backend: `Limiter(key_func=get_remote_address, storage_uri=settings.redis_url)`.

**SRE-20 [LOW] — DB connection pool may saturate under multi-worker load**

`pool_size=5, max_overflow=10` (15 total) per process. With 2 workers per service and 3 app services, peak DB connections = 90. Postgres 17 default `max_connections=100`. Fix: set postgres `max_connections=200` or tune pool sizes down.

---

### 9. Dependency Failure Modes

| Dependency | Impact | Handled? |
|---|---|---|
| PostgreSQL down | All services 500; reports lost | `restart: unless-stopped` + healthcheck |
| Redis down | Panel sessions break | Graceful degradation claimed fixed; verify in panel routes |
| OIDC provider down | No new panel logins; existing sessions survive | Acceptable |
| Resend API down | Email fails; 4 retries over 30 min; FAILED | Signal/Mattermost fallback; `NotificationFailed` alert fires |
| signal-api down | Signal channel fails | `restart: unless-stopped`; no profile — always in stack |
| Host disk full | Postgres WAL fills; DB writes fail | DiskUsageHigh alert broken (SRE-11) |
| Host OOM | Docker kills containers; restart recovers | No memory limits; recovery order not guaranteed |

**SRE-21 [CRITICAL] — No fallback if all notification channels fail simultaneously**

If email, Signal, and Mattermost all fail (network partition, misconfiguration), cases are written to DB but no one is alerted. The only recovery path is a conduct team member proactively checking the panel. There is no dead-man's-switch or fallback SMS gateway. This is the highest-impact single gap for a festival environment.

---

### 10. On-Call Ergonomics

**SRE-22 [HIGH] — No persistent log aggregation; logs lost on `docker compose down -v`**

Services emit JSON-structured logs to stdout captured by Docker's json-file driver. `docker compose down -v` wipes logs. No Loki, no syslog forwarding. At 3am there is no single pane to correlate form → router → email failure by trace ID. Fix: add Loki + promtail to the monitoring profile, or configure Docker daemon `log-opts` with `max-size`/`max-file`.

**SRE-23 [MEDIUM] — Health endpoint returns `status: ok` when notification channels are degraded**

`router/main.py:509` returns `{"status": "ok"}` when `db_ok=True` even if `email_ok=False`. The Docker healthcheck passes even when notifications cannot be sent. Fix: return `{"status": "degraded"}` and HTTP 207 when any required channel is down.

**SRE-24 [LOW] — No pre-event prod smoke test**

No script validates the full pipeline against the live stack before the festival starts. `scripts/run_e2e.sh` runs against an isolated stack only. Fix: add `scripts/smoke-test-prod.sh` that submits a canary case, verifies notification receipt, and cleans up.

---

### SRE Summary Risk Register

| ID | Area | Severity | Finding |
|---|---|---|---|
| SRE-01 | Alerting | CRITICAL | No Alertmanager — all alerts silent |
| SRE-02 | Observability | CRITICAL | `dispatch_seconds` histogram never observed |
| SRE-03 | Alerting | CRITICAL | `ListenerSilent` expression broken; false-fires on cold start |
| SRE-21 | Reliability | CRITICAL | No fallback if all notification channels fail simultaneously |
| SRE-04 | Backup | HIGH | No restore procedure or script |
| SRE-05 | Reliability | HIGH | PENDING notifications not recovered after router restart |
| SRE-06 | Deployment | HIGH | Full downtime on every deploy |
| SRE-07 | On-call | HIGH | No runbook |
| SRE-08 | Observability | HIGH | Trace IDs not verified in anyio thread (email) path |
| SRE-12 | Backup | HIGH | Backup not tested; no rsync configured by default |
| SRE-14 | Secrets | HIGH | SECRET_KEY rotation invalidates outstanding ACK links |
| SRE-16 | Capacity | HIGH | Single-worker uvicorn in production |
| SRE-22 | On-call | HIGH | No persistent log aggregation |
| SRE-09 | Observability | MEDIUM | No end-to-end submit→notify latency metric |
| SRE-10 | Observability | MEDIUM | Monitoring profile opt-in; zero observability on default stack |
| SRE-13 | Backup | MEDIUM | Backup timer not installed by `install.py` |
| SRE-15 | Secrets | MEDIUM | Secret rotation requires full service restart |
| SRE-17 | Deployment | MEDIUM | `prod-update` loses prod compose config if pull fails |
| SRE-18 | Capacity | MEDIUM | No resource limits on any container |
| SRE-19 | Capacity | MEDIUM | Rate limiter in-memory; bypassed under multi-worker |
| SRE-23 | On-call | MEDIUM | Health endpoint 200 when channels degraded |
| SRE-11 | Alerting | LOW | node_exporter absent; DiskUsageHigh never fires |
| SRE-20 | Capacity | LOW | DB pool may saturate under multi-worker |
| SRE-24 | On-call | LOW | No pre-event prod smoke test |
