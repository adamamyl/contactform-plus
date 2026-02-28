# Research & Analysis: EMF Conduct System

## Overview

This document is a detailed analysis of `plan.md`, covering the architecture, components, constraints, and design decisions for the EMF Festival Conduct & Accessibility system. The system is intended to be open-sourced and potentially reused by other festivals.

---

## Technology Stack & Constraints

### Core Stack
- **Language**: Python (EMF preference)
- **Database**: PostgreSQL
- **Deployment**: Docker (Dockerized, targeting a VPS and local dev)
- **Package management**: `uv` (Astral) — handles venv creation, dependency resolution, and all Python packaging
- **Reverse proxy / TLS termination**: Caddy (handles local hostnames and production; self-signed certs acceptable locally)
- **Protocols**: HTTP/2 only, TLS 1.3 only — no HTTP/1.1, no TLS 1.2 or earlier

### Config Format
- `.env` files or JSON (YAML and TOML are explicitly rejected)
- Future consideration: per-team config editable via web UI

### Code Quality & Security Tools
- `ruff` — linting and formatting
- `bandit` — static security analysis
- `gitleaks` / `git-secrets` — pre-commit hooks to prevent credential commits
- OWASP Top 10 (2025 edition) — explicit test coverage required

### Observability
- **Health monitoring**: All components must expose health endpoints consumable by the sysadmin team
- **Dashboards**: Grafana — dashboard payloads (JSON) must be supplied for each component
- **Abuse detection**: Anomaly detection on submissions (e.g., high volume from a single source) must be built in and surfaced

### General Principles
- **Principle of Least Privilege** throughout
- **TLS between all internal components** (even inside Docker network)
- **No user input trusted** — validate and sanitise everything at system boundaries
- **No reinventing the wheel** — prefer maintained libraries over bespoke code
- **Data minimisation** — collect only what is strictly necessary
- **Emoji support** required
- **Responsive UI** — must work on mobile phones
- **i18n** — parked as a nice-to-have, but design should not preclude it
- **Bot/AI agent protection** on public forms (CAPTCHA, rate limiting, honeypot fields, or similar)

---

## System Components

### App 1 — Public Conduct Report Form (Front-end)

**Purpose**: A guided web form for members of the public to submit accessibility or conduct complaints to the EMF team.

#### Phase Awareness
The app must be aware of three temporal phases, driven by config:
1. **Pre-event** — limited actions available
2. **Event time** — full feature set, including urgency routing and real-time alerts
3. **Post-event** — reduced actions

Event dates are defined in config (JSON format implied):
```json
{
  "events": [
    { "name": "emfcamp2026", "startDate": "2026-07-12", "endDate": "2026-07-20" },
    { "name": "emfcamp2028", "startDate": "2028-07-05", "endDate": "..." }
  ]
}
```

#### Form Structure (Two Sections)

**Section 1 — Core Report (required)**
| Field | Type | Notes |
|---|---|---|
| Name/persona | Short text | Optional |
| Pronouns | Short text | Optional |
| Phone | Short text | Optional |
| Email address | Valid email | Optional |
| Camping with… | Short text | Optional |
| What are you reporting | Long text | Prompted: "I was by… when I saw…" |
| When did this take place | Date picker | Locale-formatted |
| Time | 24hr HH:MM | Defaults to now |
| Where did it take place | Short text + optional geolocation | Reference: map.emfcamp.org |
| Any more information | Long text | Photo/video links acceptable |
| Additional support needed | Long text | Signposts First Aid, Info Desk |

**Section 2 — Optional / Can Be Filled Later**
| Field | Type | Notes |
|---|---|---|
| Other people involved | Long text | Names or descriptions |
| Do you know why this happened | Long text | |
| Can we contact you for more info | Yes/No | |
| Anything else we should know | Long text | |

#### Submitter-Facing Features
- User can set **priority/urgency** of their report
- Option to receive case details by **email**
- Intro text directs urgent event-time issues to DECT phone (1234) before the form

#### Database Record (per submission)
| Field | Description |
|---|---|
| `case_number` | UUID (primary key) |
| `friendly_name` | Four random lowercase words, hyphen-separated (e.g., `tiger-lamp-blue-moon`); 1-to-1 with UUID |
| `form_data` | Structured JSON of all submitted fields |
| `urgency` | User-set priority level |
| `phase` | Which phase was active at submission |
| `timestamps` | Created at, updated at |
| + additional fields for conduct team workflow | Status, assignee, tags, etc. |

The schema must be designed for easy extensibility (new questions added without migrations pain).

---

### App 2 — Conduct Team Case Management Panel

**Purpose**: Internal tool for the conduct team to review, triage, and work cases.

#### Access Control
- SSO-gated using **EMF single-sign-on** (OAuth2/OIDC — likely Keycloak or UFFD)
- Only members of the configurable `team_conduct` group may access
- Local dev: mock OAuth flow or a minimal Keycloak/UFFD instance

#### Features
- List and view all cases
- **Tags** — categorise complaints (freeform or predefined)
- **Assign** to a named team member
- **Status workflow**: `new` → `assigned` → `in progress` → `action needed` → `decision needed` → `closed`
- No formal workflow engine required, but the status machine should be enforced

#### Multi-tenancy Consideration (Future)
- Design with row-based permissions so different teams can use the same instance
- For now: bespoke for EMF conduct team
- Future: teams create their own forms and config via admin UI

---

### App 2b — Dispatcher View

**Purpose**: A stripped-down view for a dispatcher (e.g., someone routing calls on-site) to triage and action cases without seeing sensitive details.

#### Key Design Points
- **No login** — accessed via a short-lived, time-limited session URL (generated from the conduct panel)
- Time limit is **configurable**
- Valid for a maximum of **two concurrent devices**
- Session must be **hard-terminated** after expiry — tokens revoked, no grace period
- Only exposes: `urgency`, `friendly_id`, `status`
- Dispatcher can: trigger a call / Signal message, transition the case state
- Permissions enforced at the data layer — the dispatcher token cannot access anything else

---

### App 2c — Admin App (Under Discussion)

**Purpose**: Sysadmin tooling for provisioning, managing teams, forms, SSO, and app config — without exposing case data.

Considered capabilities:
- Group → form → database management
- SSO management
- Provisioning new teams
- Does **not** expose case data

Status: Proposed, not yet confirmed as in-scope.

---

### App 3 — Router / Notification System

**Purpose**: Stateful, reliable notification dispatcher that routes case alerts to the appropriate channels based on phase and urgency.

#### Notification Channels
| Channel | When |
|---|---|
| On-site telephony (Jambonz) | Event time — primary |
| Signal group | Event time — secondary / fallback for urgency |
| Email | All phases; event time as tracking record |

#### Key Behaviours
- **Phase-aware**: different routing rules pre/during/post event
- **Technology-agnostic abstraction**: the router exposes generic functions; event-specific adapters (e.g., Jambonz) do the plumbing
- **Reliability**: stateful message tracking — sent, queued, failed, ACK'd
- **Retry logic**: configurable retry intervals
- **Multi-recipient email**: if multiple addresses configured, send to all
- **Deduplication / alert-fatigue prevention**: explicit "Calling X also" and "XYZ ACK'd" acknowledgements; consistent emoji signifiers for status
- **Availability check**: before using on-site telephony, verify it's up; fall back to Signal for urgent cases if down

#### Generic Router Capabilities
- Text-to-speech (TTS) output
- Send email
- Make a phone call
- Post in a Signal group
- Post in a Mattermost channel
- Retry with backoff
- Require ACK from a human
- Handle updates to avoid duplicated alerts

#### State Machine (per notification)
`pending` → `sent` → `acked` / `failed` → `retrying` → `escalated`

#### Email Strategy
- **Event time**: send to all configured addresses (including `event-time-dispatcher@emfcamp.org`) with minimal info (non-sensitive routing data only)
- **Outside event time**: send full case link + relevant details to `conduct@emfcamp.org` (configurable)

---

### App 4 — Text-to-Speech (TTS) Service

**Purpose**: Convert pertinent case details into a spoken audio stream for telephony routing.

#### Requirements
- Clear, succinct message format: `"New URGENT case: <type> at <location>."`
- Must support interactive telephony prompts: `"Press 1 to ACK, press 2 to call next person"`
- Output: audio data stream suitable for piping into Jambonz or a similar system
- Should be a generic, swappable component (not hard-wired to Jambonz)

---

### App 5 — Jambonz Integration (Event-Specific Adapter)

**Purpose**: The concrete adapter that connects the generic router to the EMF 2026 Jambonz phone system.

Reference: https://docs.jambonz.org/reference/introduction

#### Call Flow
1. Receive TTS stream from App 4
2. Route to Jambonz API/SDK — whichever surface provides the required feature set
3. Deliver to the conduct team call group
4. If no answer:
   - Retry at 5 min, 10 min, 15 min (3 attempts)
   - Escalate to shift leader (same number — phone rotates; configurable)
   - For urgent cases only: escalate to person on shift for site
   - Final escalation: lead (personal number; configurable)

#### Design Note
This component is explicitly called out as **most likely to be thrown away** — it is EMF 2026-specific. The abstraction layer in App 3/4 must be solid so this can be replaced without touching the router logic.

---

## Cross-Cutting Concerns

### Security Architecture
| Concern | Approach |
|---|---|
| Credential leakage | gitleaks + git-secrets pre-commit hooks from day one |
| Input validation | Trust nothing from users; validate at all system boundaries |
| OWASP Top 10 (2025) | Explicit test cases required for each item |
| Static analysis | bandit on all Python code |
| Linting | ruff |
| Transport security | TLS 1.3 + HTTP/2 everywhere, including internal Docker networking |
| Principle of Least Privilege | Applied to all service accounts, tokens, DB roles, and session URLs |
| Bot protection | On public forms (mechanism TBD: rate limiting, CAPTCHA, honeypot) |
| Session management | Hard expiry, device-limited tokens for dispatcher view |

### Local Development
- Caddy for local TLS (self-signed certs acceptable)
- Local hostnames alongside production config
- Mock OAuth/SSO or minimal Keycloak/UFFD standup
- Full Docker Compose setup mirroring production

### Data Model Design Principles
- UUID primary keys for all cases
- Human-readable friendly IDs (four-word hyphenated; cardinality 1:1 with UUID)
- Structured JSON for form responses (allows schema evolution without full migrations)
- Designed for minimal data collection (GDPR / data minimisation alignment)
- Row-level security scaffolding for future multi-tenancy

---

## Open Questions & Gaps in the Plan

1. **endDate for emfcamp2028** is truncated in the config example — needs completion.
2. **Bot/AI protection mechanism** not specified — CAPTCHA vendor, honeypot, or rate-limiting approach needs a decision.
3. **TTS provider** not specified — local TTS engine (e.g., Coqui, Piper) vs cloud (e.g., Google TTS, ElevenLabs)?
4. **Signal integration** — signal-cli-rest-api is referenced but not confirmed; self-hosted vs managed?
5. **SSO provider** — Keycloak or UFFD? Plan mentions both. Need to confirm which EMF runs.
6. **Admin app (2c)** — scope not confirmed; decision needed before implementation.
7. **i18n** — parked, but the form field model should allow for translated labels without structural changes.
8. **Priority/urgency levels** — not enumerated. Need agreed-upon set (e.g., low/medium/high/urgent).
9. **Case data retention policy** — not mentioned; relevant for GDPR compliance.
10. **Who can see what in the conduct panel** — are there sub-roles within `team_conduct`?
11. **Mattermost** — mentioned in router generic capabilities but no further detail given.
12. **ACK mechanism** — "Press 1 to ACK" over phone, but what about Signal/email ACK?

---

## Implementation Order (Suggested)

Based on dependencies and risk:

1. **Infrastructure scaffolding** — Docker Compose, Caddy, gitleaks hooks, ruff/bandit config, uv setup
2. **App 1** — Public form (highest user-facing value; drives database schema)
3. **App 2** — Conduct team panel (depends on App 1 data model; needs SSO)
4. **App 2b** — Dispatcher view (depends on App 2)
5. **App 3** — Router (depends on Apps 1 & 2 being able to produce events)
6. **App 4** — TTS service (can be developed in parallel with App 3)
7. **App 5** — Jambonz adapter (depends on Apps 3 & 4; event-specific, defer until closer to EMF 2026)
8. **App 2c** — Admin app (lowest priority; nice-to-have)

---

## Key Risks

| Risk | Mitigation |
|---|---|
| Jambonz API not meeting needs | Evaluate SDK vs REST API early; design adapter layer first |
| Signal CLI self-hosting complexity | Spike signal-cli-rest-api early in router development |
| UFFD/Keycloak SSO mock complexity | Build SSO mock as a first-class dev-time component |
| Data minimisation vs. case investigation needs | Design schema iteratively with conduct team input |
| Alert fatigue during event | ACK logic + deduplication must be tested under load before event |
| Form abuse during event | Bot protection must be in place before go-live |
