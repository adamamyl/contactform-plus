# EMF Phone System Setup

The EMF phone system (`sip2.ix1.inferno.tel`) handles outbound calls to conduct team members when a new case arrives. It accepts a plain-text message and a SIP extension number, places the call, plays the message with a DTMF prompt appended, and returns the result synchronously.

This replaces Jambonz for EMF events. No internal adapter service or audio file hosting is needed.

---

## How it works

1. A case is submitted and the router decides to make a phone call (based on `emf_phone_mode` in `config.json`).
2. The router posts to the phone API with the target extension and a short text message.
3. The API places the call. The recipient hears the message followed by: *"Press 1 to acknowledge this case. Press 2 to pass to the next responder."*
4. The API returns a result synchronously (once the call ends):
   - `ACKNOWLEDGE` — recipient pressed 1 → the router marks the case as acknowledged across all channels
   - `SKIP` — recipient pressed 2 → router tries the next configured extension
   - `NO-ANSWER`, `HANGUP`, `NO-INPUT` — router tries the next extension, then retries on schedule

---

## Step 1 — Obtain credentials

Contact the EMF network/infra team to get:

- **API URL** — e.g. `http://sip2.ix1.inferno.tel:3000`
- **Bearer token** — the shared secret for the `/api/conduct/alert` endpoint
- **Extension numbers** — the SIP extensions to call (e.g. `7483` for the site phone, `2326` for a named responder)

---

## Step 2 — Configure `.env`

```bash
EMF_PHONE_API_URL=http://sip2.ix1.inferno.tel:3000
EMF_PHONE_API_KEY=<bearer-token-from-infra-team>
```

---

## Step 3 — Configure `config.json`

Add `emf_phone_mode` and `emf_phone_targets` to the active event:

```json
{
  "name": "EMF 2026",
  "start_date": "2026-07-12",
  "end_date": "2026-07-20",
  "emf_phone_mode": "high_priority_only",
  "emf_phone_targets": [
    {"number": 7483, "description": "site", "order": 1, "delay_seconds": 0},
    {"number": 2326, "description": "adam", "order": 2, "delay_seconds": 120},
    {"number": 9999, "description": "backup",  "order": 3, "delay_seconds": 300}
  ]
}
```

### `emf_phone_mode` options

| Value | Behaviour |
|-------|-----------|
| `"disabled"` (default) | No calls made |
| `"always"` | Call on every new case |
| `"high_priority_only"` | Call only for `high` or `urgent` urgency |

### `emf_phone_targets` fields

| Field | Type | Description |
|-------|------|-------------|
| `number` | integer | SIP extension to dial |
| `description` | string | Human label for logs (e.g. `"site"`, `"adam"`) |
| `order` | integer | Ascending call order — lowest called first |
| `delay_seconds` | integer | Seconds to wait after the previous target before calling this one |

The first target (`order: 1`) is always dialled immediately. Subsequent targets are only tried if the previous one did not result in `ACKNOWLEDGE`.

---

## Step 4 — Apply

Force-recreate the router container to pick up the new config:

```bash
cd /opt/emf-conduct
docker compose -f infra/docker-compose.yml up -d --force-recreate msg-router
```

Check the router logs to confirm the adapter initialised:

```bash
docker compose -f infra/docker-compose.yml logs --tail=50 msg-router
```

You should see no errors at startup. The adapter does a lightweight availability check (HTTP GET to the API endpoint) when the first case arrives.

---

## Step 5 — Test

Retrigger routing for an existing case (replace the UUID with a real case from `forms.cases`):

```bash
docker exec infra-postgres-1 \
  psql -U emf_forms_admin emf_forms \
  -c "SELECT pg_notify('new_case', '<case-uuid-here>');"
```

Watch logs in another terminal:

```bash
docker compose -f infra/docker-compose.yml logs -f msg-router
```

You should see lines like:

```
EMF phone: calling site (7483) for case <uuid>
EMF phone: site (7483) result=ACKNOWLEDGE for case <uuid>
```

If result is `ACKNOWLEDGE`, the case will be marked acknowledged in the panel automatically.

---

## Escalation behaviour

When a call does not receive `ACKNOWLEDGE`:

1. The next target in the list (by `order`) is tried after `delay_seconds` have elapsed.
2. Once all targets are exhausted without `ACKNOWLEDGE`, `send()` returns failure and the existing retry schedule kicks in: the full target sequence is retried at 5 min, 10 min, and 15 min intervals.

`SKIP` (pressed 2), `NO-ANSWER`, `HANGUP`, and `NO-INPUT` all move to the next target.

---

## Troubleshooting

**No call is placed:**
- Check `emf_phone_mode` is not `"disabled"`.
- Check the router log for `"EMF phone: calling ..."` — if absent, the adapter is not being reached (config or mode issue).
- Check urgency: `"high_priority_only"` only calls for `high` or `urgent` cases.

**Call placed but no DTMF response registered:**
- The API result is logged as `result=<value>`. If it shows `NO-INPUT`, the phone rang but nobody pressed a digit.
- If it shows `HANGUP`, the call was answered and hung up without DTMF input.

**API errors (non-200):**
- Logged as `EMF phone API returned <status> for <description> (<number>), case <uuid>`.
- Check `EMF_PHONE_API_KEY` is correct.
- Confirm the API URL is reachable from the router container: `docker exec infra-msg-router-1 curl -s -o /dev/null -w "%{http_code}" http://sip2.ix1.inferno.tel:3000/api/conduct/alert`

**Ack not propagating after ACKNOWLEDGE:**
- The adapter posts to `ROUTER_SELF_URL/internal/ack/<case-id>`. Confirm `ROUTER_SELF_URL` (default `http://msg-router:8002`) is reachable inside Docker.
