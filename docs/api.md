# EMF Conduct System — API Reference

All API endpoints follow REST conventions: plural nouns, correct HTTP verbs, standard status codes. The panel API is versioned under `/api/v1/`.

## Authentication

Panel endpoints require an OIDC bearer token. Obtain one via the `client_credentials` flow:

```
POST https://oidc.emf-forms.internal/default/token
Authorization: Basic <base64(panel:CLIENT_SECRET)>
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials&scope=openid email profile groups
```

Include the token on all panel requests:

```
Authorization: Bearer <access_token>
```

The token must contain `groups: ["team_conduct"]`. Tokens expire after 1 hour.

---

## Panel API — Cases

Base URL: `https://panel.emf-forms.internal`

### `GET /api/v1/cases`

List cases with optional filtering and pagination.

**Query params:**

| Param | Type | Default | Description |
|---|---|---|---|
| `limit` | int | 50 | Items per page (1–200) |
| `offset` | int | 0 | Pagination offset |
| `status` | string (repeatable) | — | Filter: `new` `assigned` `in_progress` `action_needed` `decision_needed` `closed` |
| `urgency` | string (repeatable) | — | Filter: `low` `medium` `high` `urgent` |

**Response `200`:**
```json
{
  "items": [
    {
      "id": "uuid",
      "friendly_id": "EMF-001",
      "event_name": "EMF 2026",
      "urgency": "medium",
      "status": "new",
      "assignee": null,
      "tags": [],
      "location_hint": "Stage A",
      "created_at": "2026-06-01T12:00:00+00:00",
      "updated_at": "2026-06-01T12:00:00+00:00",
      "_links": {
        "self": "/api/v1/cases/uuid",
        "history": "/api/v1/cases/uuid/history",
        "status": "/api/v1/cases/uuid/status",
        "urgency": "/api/v1/cases/uuid/urgency",
        "assignee": "/api/v1/cases/uuid/assignee",
        "tags": "/api/v1/cases/uuid/tags",
        "ack": "/api/v1/cases/uuid/ack",
        "calls": "/api/v1/cases/uuid/calls"
      }
    }
  ],
  "total": 42,
  "limit": 50,
  "offset": 0
}
```

---

### `GET /api/v1/cases/lookup`

Resolve a friendly ID to a UUID, or a UUID to a friendly ID.

**Query params (provide exactly one):**

| Param | Description |
|---|---|
| `friendly_id` | e.g. `EMF-001` |
| `id` | UUID of the case |

**Response `200`:**
```json
{"id": "uuid", "friendly_id": "EMF-001"}
```

**Response `404`:** Not found.
**Response `422`:** Neither param provided.

---

### `GET /api/v1/cases/{case_id}`

Full case detail including `form_data`.

**Response `200`:** Same shape as list item but also includes `form_data` (the full submission JSONB).

**Response `404`:** Case not found.

---

### `GET /api/v1/cases/{case_id}/history`

Audit trail of all field changes for a case, oldest first.

**Response `200`:**
```json
[
  {
    "id": 1,
    "changed_by": "alice",
    "field": "status",
    "old_value": "new",
    "new_value": "assigned",
    "changed_at": "2026-06-01T12:05:00+00:00"
  }
]
```

---

### `PATCH /api/v1/cases/{case_id}/status`

Transition a case through the status state machine.

**Body:** `{"status": "assigned"}`

**Valid transitions:**

```
new → assigned
assigned → in_progress | new | closed
in_progress → action_needed | decision_needed | closed
action_needed → in_progress | decision_needed | closed
decision_needed → closed | in_progress
closed → (none)
```

**Response `200`:** `{"status": "assigned"}`
**Response `422`:** Invalid transition.

---

### `PATCH /api/v1/cases/{case_id}/urgency`

**Body:** `{"urgency": "high"}`

Levels: `low` | `medium` | `high` | `urgent`

**Response `200`:** `{"urgency": "high"}`
**Response `422`:** Invalid urgency level.

---

### `PATCH /api/v1/cases/{case_id}/assignee`

**Body:** `{"assignee": "alice"}` or `{"assignee": null}` to unassign.

**Response `200`:** `{"assignee": "alice"}`

---

### `PATCH /api/v1/cases/{case_id}/tags`

Replaces all tags. Send an empty array to clear.

**Body:** `{"tags": ["noise", "welfare"]}`

**Response `200`:** `{"tags": ["noise", "welfare"]}`

---

### `POST /api/v1/cases/{case_id}/ack`

Acknowledge a case: marks all pending notifications as acked and sets the assignee to the authenticated user.

**Response `200`:** `{"ok": true}`

---

### `POST /api/v1/cases/{case_id}/calls`

Re-trigger the notification dispatch pipeline for a case (via `pg_notify`). Use when a call or alert needs resending.

**Response `200`:** `{"ok": true}`

---

## Panel API — Lookup Lists

### `GET /api/v1/assignees`

Sorted list of known assignee usernames (from Redis).

**Response `200`:** `["alice", "bob"]`

### `GET /api/v1/tags`

Sorted list of all distinct tags used across cases.

**Response `200`:** `["noise", "theft", "welfare"]`

---

## Panel API — Dispatcher Sessions

Dispatcher sessions are short-lived JWTs that grant read-only access to unassigned cases. They are designed to be shared with a radio operator or dispatcher screen.

### `POST /api/v1/dispatcher/sessions`

Create a dispatcher session token.

**Body:** `{"send_to": null}`

**Response `200`:**
```json
{
  "url": "https://panel.emf-forms.internal/dispatcher?token=eyJ...",
  "expires_in_hours": 8
}
```

---

### `DELETE /api/v1/dispatcher/sessions/{jti}`

Revoke a dispatcher token immediately. The `jti` is the JWT ID from the token payload.

**Response `204`:** No content.

---

## Panel API — Dispatcher (token auth)

These endpoints authenticate via `?token=<dispatcher_jwt>` query param rather than the bearer token. They are intended for the dispatcher UI and radio-operator screens.

### `GET /api/v1/dispatcher/cases`

| Param | Default | Description |
|---|---|---|
| `token` | required | Dispatcher JWT |
| `all` | false | If true, include assigned cases |

**Response `200`:** Array of cases (same shape as case list items, without `form_data`).

### `POST /api/v1/dispatcher/cases/{case_id}/ack`

**Body:** `{"acked_by": "dispatcher"}`

**Response `200`:** `{"ok": true}`

### `POST /api/v1/dispatcher/cases/{case_id}/calls`

Re-trigger notifications for a case.

**Response `200`:** `{"ok": true}`

---

## Report Form API

Base URL: `https://report.emf-forms.internal`

No authentication required. Rate limited.

### `POST /api/submit`

Submit an incident report.

**Headers:** `X-Idempotency-Key: <uuid>` (optional, prevents duplicate submissions on retry)

**Body (minimum):**
```json
{
  "event_name": "EMF 2026",
  "what_happened": "Description of incident (10–2000 chars)",
  "urgency": "medium",
  "phase": "weekend",
  "can_contact": false
}
```

**Response `201`:** `{"friendly_id": "EMF-042", "status": "received"}`
**Response `200`:** Duplicate (idempotency key already seen).
**Response `422`:** Validation error.

---

## Message Router API

Base URL: `https://router.emf-forms.internal`

### `GET /health`

Returns status of database, email, and signal adapter.

### `POST /webhook/signal`

Called by Signal CLI REST API on incoming reactions. Emoji 🤙 triggers ACK.

### `POST /webhook/mattermost/action`

Called by Mattermost when a button is clicked. Requires `X-Webhook-Secret` header.

### `GET /ack/{token}`

Email magic-link ACK endpoint. The `token` is a JWT embedded in notification emails.

---

## Health Checks

All services expose `GET /health` returning:

```json
{
  "status": "ok",
  "checks": { "database": "ok" },
  "version": "0.1.0"
}
```

`status` is `"degraded"` if any check fails.

---

## Postman / Bruno Import

- **Postman:** Import `docs/swagger-spec.json` (Postman Collection v2.1)
- **Bruno:** Collection at `~/projects/bruno/emf/EMF Conduct - Total Restoration/`

Set these environment variables before running:

| Variable | Value |
|---|---|
| `panel_url` | `https://panel.emf-forms.internal` |
| `router_url` | `https://router.emf-forms.internal` |
| `report_url` | `https://report.emf-forms.internal` |
| `oidc_url` | `https://oidc.emf-forms.internal` |
| `access_token` | Populated by Auth request |
| `dispatcher_token` | Populated by Create Dispatcher Session |

Run **Get Bearer Token** first — it populates `access_token` automatically via a post-request script.
