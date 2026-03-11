# EMF Conduct System — Project Memory

## Project location
`~/projects/emf-conduct` (moved from ~/Documents/emf-conduct Feb 2026)

## User preferences
- Full autonomy granted in ~/projects/emf-conduct — no need to ask permission for file changes
- No unnecessary comments or docstrings
- No `Any`/`unknown` types; no unexplained `# type: ignore`
- Run typecheck continuously while implementing
- Mark plan.md Section 15 tasks `[x]` as work completes
- Don't stop until all tasks done

## Key files
- `plan.md` — implementation plan + Section 15 detailed TODO checklist
- `CLAUDE.md` — project conventions, key paths, all copy-paste commands
- `MEMORY.md` — this file; kept in project root (not ~/.claude) so it persists across VM relaunches

## Tech stack
- Python 3.12+, FastAPI, SQLAlchemy 2 async, asyncpg, Pydantic v2, pydantic-settings
- `uv` for package management (never pip directly)
- PostgreSQL 17, Caddy, Docker Compose
- Shared lib: package name `emf_shared` at `shared/src/emf_shared/`
- Config: `config.json` + `.env` only (no YAML/TOML for app config)
- Internal Docker network uses `http://` — TLS at Caddy only

## Git workflow
- git-flow: main, develop, feature/* branches
- Active branch: `feature/implementation`
- Commit style: `feat(scope):` / `fix(scope):` / `chore:`

## Architecture
- Monorepo: apps/form (8000), apps/panel (8001), apps/router (8002), apps/tts (8003), apps/jambonz (8004)
- Shared lib at shared/src/emf_shared/
- infra/: caddy, postgres, grafana, prometheus, zap, swagger, docker-compose.yml

## Implementation status
All phases complete (0–13 core + 15–19 extras):
- Phases 0–11: full implementation committed on feature/implementation
- Phase 15: e2e tests (tests/e2e/, scripts/run_e2e.sh, infra/docker-compose.e2e.yml)
- Phase 16: bad strings script (scripts/bad_strings_test.py, blns PyPI package)
- Phase 17: Swagger UI (infra/swagger/, added to docker-compose.yml profiles: [local, swagger])
- Phase 18: accented char tests in apps/form/tests/test_routes.py; TTS README note
- Phase 19: OWASP ZAP (infra/zap/, scripts/run_zap.py, docker-compose profile: zap, security.yml job)
- Phases Q–V: urgency editing, ACK tokens, cross-channel ACK, Mattermost Posts API, Jambonz auto-call,
  Signal map link, also_sent_via (committed 652ca21 on feature/implementation)

## Local dev setup (macOS)
- dnsmasq: `/opt/homebrew/etc/dnsmasq.d/emf-conduct.conf` — explicit `address=` entries per hostname (not a wildcard); add new hostnames here and `sudo brew services restart dnsmasq`
- dnsmasq.conf must use `conf-dir=` not `conf-file=` to pick up all `.conf` files
- Caddy local CA cert: extract from `infra_caddy_data` volume and trust with `sudo security add-trusted-cert`
- Static files path: `Path(__file__).parent.parent.parent / "static"` (3 parents from `src/emf_form/main.py` → `/app`)
- `LOCAL_DEV=true` env var on form service: overrides `is_active_routing_window` to show all fields
- Static files (CSS/JS) in Docker are baked in at build time — `?v=N` cache-busting only works after rebuilding the image
- panel/form template changes require `docker compose build <service> && up -d <service>` to take effect

## EMF map integration
- `emf-map` Docker service: built from `../../emf/map/web` (relative to `infra/`), served at `map.emf-forms.internal`
- Uses MapLibre GL; `?embed=true` hides header; URL hash format: `#zoom/lat/lon` (NOT `#map=zoom/lat/lon`)
- Click-to-pin: patched `~/projects/emf/map/web/src/index.ts` to add `map.on('click', ...)` in embed mode
- postMessage: patched `~/projects/emf/map/web/src/marker.ts` to emit `{ type: 'emf-marker', lat, lon }` to parent
- EMF 2024 tile data is still live on `map.emfcamp.org`; correct coords for site: x=32333, y=21636 at zoom 16
- CSP for map.emf-forms.internal needs: `connect-src https://map.emfcamp.org`, `frame-ancestors https://report.emf-forms.internal`
- Form's report.emf-forms.internal CSP needs: `frame-src https://map.emf-forms.internal`

## Panel OIDC (local dev)
- `tokenCallbacks` in mock-oidc JSON_CONFIG does NOT reliably inject claims into userinfo
- Workaround: user must type `{"groups": ["team_conduct"]}` in the mock-oidc claims field when logging in
- mock-oidc container: `ghcr.io/navikt/mock-oauth2-server:2.1.10`, interactiveLogin=true, NettyWrapper
- MismatchingStateError fixed: `auth_callback` now catches it and redirects to `/` if user already in session (caused by browser double-request on redirect)
- Panel uses `--proxy-headers --forwarded-allow-ips=*` on uvicorn CMD (required for HTTPS redirect_uri)
- DB uses `ssl="prefer"` (not "require") — postgres SSL certs owned by root, unreadable by postgres user

## DB permissions
- `form_user`: INSERT on cases + SELECT (friendly_id) only — needed for friendly-ID collision check
- `team_member`: SELECT, UPDATE on cases (full)
- `router_user`: SELECT on cases_router view (no PII)
- `panel_viewer`: SELECT on cases_dispatcher view
- `form_user` can NOT select full rows from cases — only the friendly_id column

## Form submission bugs fixed
- `model_dump()` returns Python date/time objects → not JSON serializable for JSONB; use `model_dump(mode='json')`
- JS fetch handler didn't check HTTP status — navigated to success even on 422; fixed to show error on 4xx
- `can_contact` changed from `bool | None` to required `bool`; old form submissions with null caused 422
- Event name changed from "emfcamp2026" to "EMF 2026" — old form submissions with old name caused 422
- Static JS (form.js) gets cached by browser; add ?v=N to script src in template to bust cache on changes

## Panel case actions
- case_detail.html originally used HTML `method="post"` forms → routes expect PATCH + JSON → 405
- Fixed by adding `apps/panel/static/panel.js` that intercepts form submits, sends PATCH + JSON, reloads on success
- Forms now have `data-case-id="{{ case.id }}"` and no method/action attributes
- Status transition forms use `class="status-form"` for JS selection (event delegation via document listener)
- panel.js uses `initPatchForm(formId, endpoint, getBody, successMsg)` factory for assignee+tags forms
- Dispatcher buttons use `data-action=ack|trigger` with event delegation on `#dispatcher-main`

## E2E test patterns
- `SyncDB` in tests/e2e/conftest.py: persistent asyncpg connection in dedicated thread with its own event loop
- `db` fixture is session-scoped — call `sync_db.connect()` in fixture body (not __init__) so teardown runs on failure
- `fetchrow` issues ROLLBACK on PostgresError to prevent stale connection poisoning subsequent tests
- `_submit_and_capture` uses `try/finally` to guarantee `page.unroute()` even on timeout
- Location lat/lon stored in form_data JSONB (not excluded); case_detail shows [map] link when present
- Playwright tests fire postMessage to simulate emf-map iframe pin: `window.dispatchEvent(new MessageEvent(...))`

## Common gotchas
- schemathesis 4.x API: `schemathesis.openapi.from_url()` not `schemathesis.from_url()`
- e2e postgres init uses trust auth (`infra/postgres/e2e/`) — no psql variable syntax
- Standalone scripts use PEP 723 inline deps (`# /// script` header), run via `uv run`
- ZAP policy inline via `policyDefinition` in YAML (no external policy file for headless mode)
- mypy on non-package test dirs needs `explicit_package_bases = true` in pyproject.toml
- OSM tile CSP: `*.tile.openstreetmap.org` requires subdomain — use `{s}.tile.openstreetmap.org` with subdomains option

## Mattermost setup
- Custom `infra/mattermost/Dockerfile` using Ubuntu 24.04 + official arm64 binary from releases.mattermost.com
  (official Docker images crash under Rosetta with `lfstack.push` Go runtime bug)
- Needs its own DB: `psql -U emf_forms_admin -d emf_forms -c "CREATE DATABASE mattermost;"`
- Runs on port 8065; profile: local
- Bot token goes in `MATTERMOST_TOKEN` in .env (shown once at bot creation — must copy immediately)
- Channel ID (`MATTERMOST_CHANNEL_ID`) and `mattermost_url` set in config.json
- `mattermost_url` must be `http://mattermost:8065` (internal Docker network name)
- Token ID shown in admin UI is NOT the bearer token — only the value shown at creation time is used

## AlertRouter session fix (current branch)
- Bug: `AsyncSession` was shared across `asyncio.create_task` calls in `_route_event_time`/`_route_off_event`
  → first task to commit closed the transaction for all others → `ResourceClosedError`
- Fix: `AlertRouter` now takes `session_factory: async_sessionmaker[AsyncSession]`
- `_send_with_retry` creates its own session per DB operation via `async with self._session_factory() as session:`
- `get_session_factory()` added to `shared/src/emf_shared/db.py`
- Wired in `apps/router/src/router/main.py` lifespan
- Tests updated: `_make_mock_session_factory(notif_mock)` helper in test_router.py

## DB migration gotcha
- Postgres init scripts in `infra/postgres/` only run on first volume creation
- If the schema changes after the volume exists, run `ALTER`/`DROP+CREATE` manually in the running container
- `cases_router` view was missing `location_lat`/`location_lon` columns on live DB (added after initial volume creation)
  → Fixed by: `DROP VIEW forms.cases_router; CREATE VIEW ... (with lat/lon); GRANT SELECT ... TO router_user;`
- `CREATE OR REPLACE VIEW` cannot rename columns — must DROP + CREATE
- e2e view in `infra/postgres/e2e/00_roles.sql` may need the same fix if e2e stack was created before the change

## Mattermost notifications — WORKING ✅
All these issues were fixed before end-to-end worked:
1. Session-sharing bug: `_send_with_retry` now owns its session via `session_factory`
2. `cases_router` view missing `location_lat`/`location_lon` (fixed with DROP+CREATE in live DB)
3. Phase was PRE_EVENT (event starts July 2026) → added `LOCAL_DEV=true` to router in docker-compose, router settings, and `AlertRouter.__init__` to force EVENT_TIME routing
4. `mattermost_url`/`mattermost_channel_id` were nested inside events[0] in config.json instead of top-level → moved to top level
5. File bind mount lost inode after edit → `docker compose up --force-recreate` to refresh

config.json mattermost fields (top-level):
- `"mattermost_url": "http://mattermost:8065"`
- `"mattermost_channel_id": "s9xu19yfdbn3xmk9m8xfqdoxha"`
- `"mattermost_webhook": null`
