# Notification System — How It Works & How to Test It

## 1. Architecture overview

```
Form submission
      │
      ▼
forms.cases INSERT
      │
      ▼ (trigger)
pg_notify "new_case" <case_id>
      │
      ▼
msg-router listener (listener.py)
  asyncpg LISTEN new_case
      │
      ▼
AlertRouter.route(alert, session)
      │
      ├─ Phase: EVENT_TIME ──► email (always)
      │                    ──► signal (if signal_mode allows)
      │                    ──► mattermost (if configured)
      │                    ──► slack (if configured)
      │
      └─ Phase: OFF_EVENT  ──► email only
```

Each channel goes through `_send_with_retry` independently (via `asyncio.create_task`), with up to 4 attempts at 0, 5, 10, and 15 minute intervals. A `forms.notifications` row is created at the start of each channel's attempt sequence.

---

## 2. The notifications table

```sql
SELECT id, case_id, channel, state, attempt_count,
       last_attempt_at, message_id, acked_by, acked_at
FROM forms.notifications
ORDER BY created_at DESC LIMIT 20;
```

| Column | Meaning |
|---|---|
| `channel` | `email`, `signal`, `mattermost`, `slack` |
| `state` | `pending` → `sent` → `acked` or `failed` |
| `attempt_count` | how many sends have been tried |
| `message_id` | channel-specific: SMTP `Message-ID` header; Signal timestamp (integer ms); Mattermost post `id` (26-char alphanumeric, from Posts API `201` response — **not** the literal `"mattermost"`, which is the legacy webhook fallback); Slack `channel_id\|ts` string (once upgraded to `chat.postMessage`) |
| `acked_by` | who acked: OIDC username (panel button), `"email_link"` (magic link), Signal phone number (emoji reaction), Mattermost `user_name` from the button-action payload, Slack `user.name` from the interaction payload |

---

## 3. Channel details

### 3.1 Email

**Config needed** (config.json + .env):
```json
{
  "smtp": {
    "host": "smtp.example.com",
    "port": 587,
    "from_addr": "conduct@emfcamp.org",
    "use_tls": true,
    "username": "conduct@emfcamp.org"
  },
  "conduct_emails": ["team@emfcamp.org"],
  "events": [{
    "dispatcher_emails": ["dispatcher@emfcamp.org"]
  }]
}
```
```env
SMTP_PASSWORD=...
ACK_BASE_URL=https://panel.emf-forms.internal
```

**Recipients**: `events[].dispatcher_emails` if set, else `conduct_emails`.

**Email format**:
- Subject: `[EMF XXXX Team] {emoji} [{URGENCY}] New case: {friendly_id}`
- Body: case details + `View full details: {panel_url}/cases/{case_id}`
- Note: ACK magic link exists in the code (`/ack/{jwt}`) but **is not currently wired** — `_send_with_retry` calls `adapter.send(alert)` without an `ack_token`, so the ACK line never appears in emails. The `/ack/{token}` endpoint in the router is functional; the missing piece is generating and passing the token in `_send_with_retry`.

Should be a link to ack -- which updates all the other channels. 

**ACK confirmation email**:
- Subject: `[EMF XXXX Team] ✅ (ACK) {friendly_id}` ACK: {friendly_id}`
- Headers: `In-Reply-To` + `References` set to original Message-ID (threads in mail clients)
- Triggered when: panel ACK button pressed, or email link clicked
- Body: {friendly_id} has been acknowledged. See {panel_url}/cases/{case_id}` for more details.

**Local testing — MailHog**:
```yaml
# add to docker-compose.yml (or override)
# set for local/dev only.
# set the name as with others
mailhog:
  image: mailhog/mailhog
  ports:
    - "1025:1025"  # SMTP
    - "8025:8025"  # Web UI
  networks:
    - contactform
```
Then in config.json:
```json
{
  "smtp": {
    "host": "mailhog",
    "port": 1025,
    "from_addr": "conduct@emfcamp.org",
    "use_tls": false
  }
}
```
Navigate to `http://localhost:8025` to see received mail.

---

### 3.2 Signal

**Service**: `signal-api` container (`bbernhard/signal-cli-rest-api:latest`, MODE=native)
Internal hostname: `signal-api`, port 8080 (assumed default).

**Config needed**:
```env
SIGNAL_API_URL=http://signal-api:8080
SIGNAL_SENDER=+447450085696
```
```json
{
  "events": [{
    "signal_group_id": "base64encodedgroupid==",
    "signal_mode": "always"
  }]
}
```

**`signal_mode` values**:
| Value | When Signal fires |
|---|---|
| `always` | Every new case |
| `fallback_only` | No phone available — `_signal_phone_available()` delegates to `JambonzAdapter.is_available()` (fixed in Phase R.4; previously hardcoded `False`, making this mode behave identically to `always`) |
| `high_priority_and_fallback` | Urgency high/urgent OR no phone available |

**Message format**:
```
{emoji} *New {urgency} case*: WORD-WORD-WORD-WORD
Location: Near the bar <include map link/image>
More info: {panel_url}/cases/{case_id}
Also sent by: email, mattermost, jambonz call

ACK by reacting with :emoji
```

(ideally also to update the message if ACKed in another channel -- the ack logic should look at channels configured and update accordingly.)

**Signal setup (one-time)**:
1. Start signal-api: `docker compose up -d signal-api`
2. Register a phone number (needs SMS or voice verification):
   ```bash
   # captcha challenge (visit https://signalcaptchas.org/registration/generate.html first)
   curl -X POST http://localhost:PORT/v1/register/+441234567890 \
     -H 'Content-Type: application/json' \
     -d '{"captcha": "signalcaptcha://..."}'

   # complete registration with received SMS code
   curl -X POST http://localhost:PORT/v1/register/+441234567890/verify/123456
   ```
3. Create/link a group (or use an existing group ID):
   ```bash
   # list groups the sender belongs to
   curl http://localhost:PORT/v1/groups/+441234567890
   ```
4. Set `signal_group_id` in config.json to the group's base64 ID (from the `id` field in the groups response).

**ACK via Signal reaction**:
- A team member reacts to the alert message with the `call_me hand` emoji (🤙)
- Does the signal-cli need to be daemonized to work with async & sync workflows? 
- signal-cli-rest-api must be configured to POST reaction events to `POST http://msg-router:8002/webhook/signal`
  - Configure this in signal-cli-rest-api as a webhook: see its docs for `RECEIVE_MODE` and webhook URL env vars
- The router matches the reaction's `targetSentTimestamp` against `notifications.message_id`
- On match: sets state=acked, sends ACK confirmation message quoting the original

**Testing Signal locally**:
```bash
# health check
curl http://signal-api:8080/v1/health   # from inside Docker network

# manually send a test message (from host, expose port)
curl -X POST http://localhost:PORT/v2/send \
  -H 'Content-Type: application/json' \
  -d '{
    "message": "test",
    "number": "+441234567890",
    "recipients": ["group.base64groupid=="]
  }'

# trigger a notification end-to-end: submit a form and watch router logs
docker compose logs -f msg-router
```

---

### 3.3 Mattermost

For Mattermost testing, we'll spin up a dockerized preview version if it gives us the functionality we need:

`docker run --name mattermost-preview -d --publish 8065:8065 --publish 8443:8443 mattermost/mattermost-preview`

Otherwise use: https://docs.mattermost.com/deployment-guide/server/containers/install-docker.html . This should only be on local/dev testing.

**Config needed** (config.json):
```json
{
  "mattermost_webhook": "https://mattermost.example.com/hooks/XXXXXXXXXXX"
}
```

**Message format** (Markdown):
```
{emoji} **New {urgency} case**: WORD-WORD-WORD-WORD
Event: EMF 2026
Location: Near the bar <link to map>
[View case](https://panel.emf-forms.internal/cases/{uuid})

ACK | Investigate

```

**How our adapter currently sends**: via Mattermost **incoming webhook** (POST to webhook URL). The response body is just the text `ok` — no post ID is returned. This means:
- `notifications.message_id` is stored as the literal string `"mattermost"` (useless as a real ID)
- ACK confirmation posts a new unthreaded message to the same webhook
- No threading between alert and ACK confirmation

**Limitation vs Posts API**: Mattermost's REST Posts API (`POST /api/v4/posts`) returns a full Post object including an `id` field, which can be used as `root_id` to thread the ACK confirmation as a reply. See section 11 for the full API comparison.

**No incoming webhook ACK from Mattermost**: cannot ACK from Mattermost directly — must use panel or email link. See section 11 for details on Mattermost's native acknowledgement feature.

**Testing Mattermost locally (fake webhook)**:

Option C — Mattermost sandbox:
- Run `mattermost/mattermost-team-edition` locally (or use a test workspace)
- Create an incoming webhook in Mattermost admin → Integrations → Incoming Webhooks
- Use that URL in config.json

---

### 3.4 Slack (lower priority than Mattermost, Signal, Jambonz)

**Config needed** (config.json):
```json
{
  "slack_webhook": "https://hooks.slack.com/services/XXX/YYY/ZZZ"
}
```

**Message format** (Slack mrkdwn):
```
{emoji} *New {urgency} case*: WORD-WORD-WORD-WORD
Event: EMF 2026 | Location: Near the bar
<https://panel.emf-forms.internal/cases/{uuid}|View case>
```

**How our adapter currently sends**: via Slack **incoming webhook** (POST to webhook URL with `{"text": "..."`). The response body is just `ok` — no message ID or `ts` value is returned. This means:
- `notifications.message_id` is stored as the literal string `"slack"`
- ACK confirmation posts a new unthreaded message to the same webhook

**Limitation vs chat.postMessage**: Slack's `chat.postMessage` API returns `ts` (the message timestamp, which acts as its ID) and `channel`. These can be used to send a threaded ACK reply via `thread_ts`. See section 11 for full API details.

**No incoming webhook ACK from Slack**: panel or email link only.

**Testing Slack locally**:

Option A — fake webhook (same echo server as above).

Option C — real Slack app with `chat.postMessage` (future upgrade):
1. Create a Slack app, add `chat:write` and `chat:write.public` scopes under OAuth
2. Install to workspace and copy the Bot User OAuth Token (`xoxb-...`)
3. Switch `SlackAdapter` to use `https://slack.com/api/chat.postMessage` with `Authorization: Bearer xoxb-...` header instead of the webhook URL
4. Parse response JSON to extract `ts` — store as `message_id` in notifications table
5. Thread ACK confirmation by passing `thread_ts: original_ts`

---

### 3.5 Jambonz (telephony)

Jambonz is **not** a channel in `AlertRouter` — it is a separate service (`jambonz-adapter`) with its own logic. The panel's "Call" button triggers Jambonz, not a form submission.

**How the Call button works** (in the panel):
- `data-action="admin-trigger"` button in cases.html → panel.js sends `POST /api/cases/{id}/trigger`
- Panel routes to jambonz-adapter via `JAMBONZ_API_URL`
- jambonz-adapter calls TTS, then calls Jambonz, which places the phone call

**Jambonz call flow**:
1. `JambonzAdapter._get_tts_url(alert)`: POSTs to TTS `POST /synthesise/file` with `{friendly_id, urgency, location_hint, include_dtmf: true}`
2. TTS synthesises speech via Piper (en_GB-alan-medium model), caches as WAV, returns `{audio_url: "/audio/{token}"}`
3. JambonzAdapter POSTs to `POST /v1/Accounts/{account_sid}/Calls` with `{application_sid, to, from, tag: {case_id, audio_url}}`
4. Jambonz places the call and serves the audio
5. DTMF digit "1" → ACK; digit "2" → pass to next responder
6. Jambonz POSTs to `POST /webhook/jambonz` with `{digit, case_id, call_sid}`

**Escalation sequence** (`escalation.py`):
```
Step 1: call_group         (immediate)
Step 2: shift_leader       (5 min after step 1, if no ACK)
Step 3: escalation_number  (10 min after step 2, if no ACK)
```
Escalation stops as soon as `is_acked(case_id)` returns True.

**Known gap**: the DTMF <explain dtmf> webhook currently only logs the ACK — it does not write to the `notifications` table or call `mark_acked`. The `notification_state_total` Prometheus counter is also defined but not incremented anywhere. This must be fixed, and if the call is ACKed, it should update the database & other channels.

**Env vars needed** (jambonz-adapter container):
```env
JAMBONZ_API_URL=https://api.jambonz.io        # or self-hosted
JAMBONZ_API_KEY=...
JAMBONZ_ACCOUNT_SID=...
JAMBONZ_APPLICATION_SID=...
JAMBONZ_FROM_NUMBER=+441234567890             # E.164 DID
TTS_SERVICE_URL=http://tts:8003              # default
```

**Testing Jambonz**:

**Public URL requirement**: Jambonz cloud must POST back to our DTMF webhook when the callee presses a digit. This means `jambonz-adapter:8004` must be reachable from the internet.

- **Self-hosted Jambonz** (on the same Docker Compose network): use Docker-internal URLs throughout — no public exposure needed. Recommended for local dev.
- **Jambonz cloud** (jambonz.io): the DTMF webhook URL must be a public HTTPS URL. Route it through Caddy as `jambonz.emf-forms.internal → jambonz-adapter:8004`. For local dev against Jambonz cloud, use a tunnel (e.g. `ngrok http 8004`) to get a temporary public URL, and set that as the DTMF webhook in the Jambonz application config. The calling webhook similarly needs a public URL if Jambonz cloud is serving the call-control verbs.

For production EMF, self-hosted Jambonz on the same VPS is strongly preferred — no tunnel needed and all traffic stays internal.

1. Sign up at jambonz.io (or self-host)
2. Create an Application with:
   - Calling webhook: `https://panel.emf-forms.internal/webhook/jambonz` (or jambonz-adapter endpoint — must be public if using Jambonz cloud)
   - Set DTMF webhook to `http://jambonz-adapter:8004/webhook/jambonz` (internal) or the public Caddy URL / ngrok URL
3. Obtain a DID (phone number)
4. Configure env vars above
5. In the panel, open a case and click "Call"
6. Verify the call is placed and audio is played

**Testing TTS without Jambonz**:
```bash
# stream audio to stdout
curl -s -X POST http://localhost:8003/synthesise \
  -H 'Content-Type: application/json' \
  -d '{"friendly_id":"test-case","urgency":"urgent","location_hint":"near the bar","include_dtmf":true}' \
  > /tmp/out.raw
# play raw PCM (16kHz mono signed 16-bit little-endian):
ffplay -f s16le -ar 16000 -ac 1 /tmp/out.raw

# or get a file URL:
curl -X POST http://localhost:8003/synthesise/file \
  -H 'Content-Type: application/json' \
  -d '{"friendly_id":"test-case","urgency":"high","location_hint":"stage area"}'
```

---

## 4. Phase detection (EVENT_TIME vs OFF_EVENT)

`current_phase(config)` checks whether "now" falls within any event's date range plus `signal_padding` (default: 2 days before/after).

**For local testing**: set the event dates in config.json to today to be in EVENT_TIME phase, otherwise off-event means email-only.

```json
{
  "events": [{
    "name": "EMF 2026",
    "start_date": "2026-03-07",
    "end_date": "2026-03-09",
    "signal_mode": "always",
    "signal_padding": {"before_event_days": 2, "after_event_days": 2}
  }]
}
```

Or set `CURRENT_EVENT_OVERRIDE=event_name` in `.env` to force the router into EVENT_TIME phase for a specific event without editing dates. This env var is not yet implemented — it needs to be read in `current_phase()` (or an override layer above it in the router) and short-circuit to `Phase.EVENT_TIME` for the named event. Add to `.env-example` once implemented (tracked in plan.md TODOs).


---

## 5. Triggering notifications manually

### 5a. Submit a form
The normal path. Go to `https://report.emf-forms.internal`, fill out the form, submit. The router fires automatically.

### 5b. pg_notify directly (fastest for ad-hoc testing)
```bash
# get a real case_id first
docker exec -it infra-postgres-1 psql -U emf_forms_admin -d emf_forms \
  -c "SELECT id FROM forms.cases ORDER BY created_at DESC LIMIT 1;"

# then notify
docker exec -it infra-postgres-1 psql -U emf_forms_admin -d emf_forms \
  -c "SELECT pg_notify('new_case', '<case_id_here>');"
```
The router listener picks this up within its 5-second poll loop.

### 5c. Insert a case directly
```bash
docker exec -it infra-postgres-1 psql -U emf_forms_admin -d emf_forms << 'SQL'
INSERT INTO forms.cases (
  id, friendly_id, event_name, urgency, status, form_data, created_at, updated_at
) VALUES (
  gen_random_uuid(),
  'test-case-' || floor(random()*1000)::text,
  'EMF 2026',
  'high',
  'new',
  '{"can_contact": false, "location": {}}',
  NOW(), NOW()
) RETURNING id;
SQL
# then use the returned id with pg_notify above
```

---

## 6. ACK flows end-to-end

The main design rule is when multiple channels are used, an ACK in one of them updates elsewhere to avoid duplication of effort. This should be robust and stateful for each channel (email, mattermost, signal, jambonz, web UI, slack)

### 6a. Panel ACK button
1. Open a case in the panel
2. Click "ACK" in the row actions or case detail
3. panel.js sends `PATCH /api/cases/{id}/ack` (or `POST` — check routes.py)
4. Panel marks the notification acked in DB
5. Verify: `SELECT state, acked_by, acked_at FROM forms.notifications WHERE case_id = '<id>';`

### 6b. Email magic link (NOT YET WIRED)
The `/ack/{token}` endpoint works; `create_ack_token` exists in `router/ack/tokens.py`. But `_send_with_retry` doesn't pass a token to `EmailAdapter.send`, so no ACK link appears in emails. To test the endpoint manually:
```python
# run from inside the router virtualenv or with uv run
import uuid
from router.ack.tokens import create_ack_token

# create a token for a real notification id
notif_id = uuid.UUID("paste-notification-id-here")
secret = "paste-SECRET_KEY-value-here"
token = create_ack_token(notif_id, secret)
print(token)
```
Then visit `https://panel.emf-forms.internal/ack/{token}` (or the router's direct URL).

### 6c. Signal emoji reaction
1. Ensure `signal-cli-rest-api` is configured to forward events to `POST http://msg-router:8002/webhook/signal`
2. In Signal, react to a case alert message with 🤙
3. Check router logs: `docker compose logs -f msg-router`
4. Verify notification state in DB

To test the webhook without a real Signal reaction:
```bash
# find the notification message_id (Signal timestamp) from the DB
NOTIF_MSG_ID=$(docker exec infra-postgres-1 psql -U emf_forms_admin -d emf_forms -Atc \
  "SELECT message_id FROM forms.notifications WHERE channel='signal' AND state='sent' LIMIT 1;")

# simulate a reaction webhook
curl -X POST http://localhost:8002/webhook/signal \
  -H 'Content-Type: application/json' \
  -d "{
    \"envelope\": {
      \"source\": \"+447700900000\",
      \"dataMessage\": {
        \"reaction\": {
          \"emoji\": \"🤙\",
          \"targetSentTimestamp\": $NOTIF_MSG_ID
        }
      }
    }
  }"
```

### 6d. Jambonz DTMF ACK (gap — not fully wired)
```bash
# simulate DTMF digit 1 (ACK) from a call
curl -X POST http://localhost:8004/webhook/jambonz \
  -H 'Content-Type: application/json' \
  -d '{"call_sid": "test-call-123", "digit": "1", "case_id": "<uuid>"}'
# Currently just logs — does not write to notifications table -- fix this.
```

---

## 7. Verification checklist

After each test:

```sql
-- State of notifications for the last 5 cases
SELECT c.friendly_id, n.channel, n.state, n.attempt_count,
       n.message_id, n.acked_by, n.acked_at
FROM forms.notifications n
JOIN forms.cases c ON c.id = n.case_id
ORDER BY n.created_at DESC
LIMIT 20;
```

Expected:
- `state = 'sent'` (or `'acked'`) after successful delivery
- `attempt_count = 1` on first success
- `message_id` populated (SMTP Message-ID for email, Signal timestamp for signal, `"mattermost"`/`"slack"` for webhooks)
- Router logs show `"[SUCCESS|FAIL] Sent case ... via ..."` at INFO level
- `state = 'failed'` after 4 attempts — router logs show `"🚨 All 4 send attempts failed"`

---

## 8. Router health endpoint

```bash
curl http://localhost:8002/health
# {
#   "status": "ok",
#   "checks": {
#     "database": "ok",
#     "email": "ok",       # tries SMTP connection
#     "signal": "ok"   ,    # tries GET /v1/health on signal-api
#     "mattermost": "ok",
#     "jambonz": "ok"
#   }
# }
```

Mattermost and Slack health checks only verify the webhook URL is non-empty, not that it's reachable.

---

## 9. Known gaps / follow-up work

| Gap | Location | Notes |
|---|---|---|
| Email ACK link not sent | `alert_router._send_with_retry` | `create_ack_token` exists but not called; need to generate token before calling `adapter.send` |
| DTMF ACK not written to DB | `jambonz/main.py` webhook handler | Logs the ACK but doesn't call `mark_acked` |
| `_signal_phone_available()` always returns `False` | `alert_router.py:96` | Placeholder; real implementation would check Jambonz/phone roster | -- fix this.
| `notification_state_total` counter not incremented | `main.py` | Counter defined, never called | -- fix this
| Slack: no real message_id → no threading | `SlackAdapter.send` | Returns `"slack"` instead of `ts`. Switch to `chat.postMessage` API to get real `ts`, then thread ACK reply via `thread_ts` | -- skip slack for now, if signal/mattermost don't work, we might fallback to slack.
| Mattermost: no real message_id → no threading | `MattermostAdapter.send` | Returns `"mattermost"` instead of post `id`. Switch from incoming webhook to Posts API (`POST /api/v4/posts`) with Bearer token to get post `id` for `root_id` threading | -- use stuff from eslewhere to redesign this, in the planning stage.
| Mattermost: `requested_ack` not used | `MattermostAdapter.send` | Posts API supports `metadata.priority.requested_ack: true` to trigger Mattermost's native acknowledgement tracking — useful as a secondary layer on top of our system's ACK |
| No Jambonz channel adapter in AlertRouter | `alert_router.py` | Jambonz is panel-initiated only; no automatic call on new case | -- this should be dependent on the config values; if set, it should be automatic.
| ClamAV socket not connected | Upload pipeline | Container exists, clamd not wired | -- skip for now.

---

## 10. Config.json example (notifications-relevant fields)

```json
{
  "events": [
    {
      "name": "EMF 2026",
      "start_date": "2026-05-28",
      "end_date": "2026-05-31",
      "signal_group_id": "base64GroupIdFromSignal==",
      "signal_mode": "high_priority_and_fallback",
      "signal_padding": { "before_event_days": 2, "after_event_days": 2 },
      "dispatcher_emails": ["dispatch@emfcamp.org"]
    }
  ],
  "conduct_emails": ["conduct@emfcamp.org"],
  "smtp": {
    "host": "smtp.fastmail.com",
    "port": 587,
    "from_addr": "conduct@emfcamp.org",
    "use_tls": true,
    "username": "conduct@emfcamp.org"
  },
  "panel_base_url": "https://panel.emf-forms.internal",
  "mattermost_webhook": "https://mattermost.emfcamp.org/hooks/XXXX",
  "slack_webhook": null
}
```

And `.env`:
```env
SMTP_PASSWORD=...
SIGNAL_API_URL=http://signal-api:8080
SIGNAL_SENDER=+441234567890
ACK_BASE_URL=https://panel.emf-forms.internal
```

---

## 11. Existing Python libraries — what's out there

Research conducted March 2026. Maintenance status based on latest PyPI release dates.

### 11.1 Slack — `slack-sdk` (official, strongly recommended)

| | |
|---|---|
| PyPI | `slack-sdk` |
| Version | 3.40.1 (Feb 2026) |
| Maintained by | Slack (official) |
| Python | ≥3.7, tested through 3.14 |
| Async | Yes — `AsyncWebClient` (uses aiohttp under the hood) |
| Licence | MIT |

The official Slack SDK. Sub-packages cover every Slack API surface:

| Sub-package | What it does |
|---|---|
| `slack_sdk.web.async_client.AsyncWebClient` | Full Web API including `chat_postMessage`, returns typed responses |
| `slack_sdk.webhook.async_client.AsyncWebhookClient` | Thin async client for posting to an incoming webhook URL |
| `slack_sdk.socket_mode` | Receives events via WebSocket (no public HTTP endpoint needed) |
| `slack_sdk.models` | Block Kit UI builder helpers |
| `slack_sdk.signature` | Verifies `X-Slack-Signature` on incoming webhooks |
| `slack_sdk.oauth` | OAuth 2.0 flow helpers |

**Why this matters for us**: `AsyncWebClient.chat_postMessage` returns a typed `SlackResponse` where `response["ts"]` gives the message timestamp. We can store that as `message_id` and thread the ACK confirmation by passing `thread_ts=original_ts`. This is a direct, minimal change to `SlackAdapter`.

```python
from slack_sdk.web.async_client import AsyncWebClient

client = AsyncWebClient(token=slack_bot_token)
resp = await client.chat_postMessage(channel=channel_id, text=text)
message_id = resp["ts"]  # store in notifications.message_id

# ACK confirmation as threaded reply:
await client.chat_postMessage(channel=channel_id, text="ACK: ...", thread_ts=message_id)
```

**Verdict**: replace our hand-rolled `httpx` calls with `slack-sdk`. Minimal code, official support, typed responses, async-native, retry/rate-limit handling built in.

---

### 11.2 Mattermost — `mattermostautodriver` (best maintained)

| | |
|---|---|
| PyPI | `mattermostautodriver` |
| Version | 11.4.2 (Mar 2026) — tracks Mattermost server versions |
| Python | ≥3.10 |
| Async | No — synchronous only |
| Licence | MIT |

Auto-generated from the official Mattermost OpenAPI spec. Provides a `TypedDriver` that wraps every API endpoint including `posts.create_post`, with WebSocket support for receiving events.

```python
from mattermostautodriver import Driver

driver = Driver({
    "url": "mattermost.example.com",
    "token": "personal_access_token",
    "scheme": "https",
    "port": 443,
})
driver.login()

post = driver.posts.create_post(options={
    "channel_id": "CHANNEL_ID",
    "message": text,
    "metadata": {"priority": {"priority": "urgent", "requested_ack": True}},
})
message_id = post["id"]  # use as root_id for threaded reply

# ACK confirmation as threaded reply:
driver.posts.create_post(options={
    "channel_id": "CHANNEL_ID",
    "message": "ACK: ...",
    "root_id": message_id,
})
```

**Limitation**: no async support. For our FastAPI/asyncio codebase this means running Mattermost calls in a thread pool (`asyncio.to_thread`), or wrapping calls manually.

**Alternative — `mattermostdriver`** (PyPI: `mattermostdriver`, by Vaelor): the original hand-maintained driver that `mattermostautodriver` forked from. Less actively updated. Not recommended for new code.

**Verdict**: `mattermostautodriver` is the right choice for the Posts API upgrade. Worth the sync-in-async wrapper overhead to get real post IDs and threading. Our current incoming-webhook approach has no Python library that adds value — a webhook URL POST is just `httpx`.

---

### 11.3 Signal — no mature Python library; hand-roll is correct

Three candidates exist; none are a clear win:

| Library | PyPI | Notes |
|---|---|---|
| `signal-messenger-python-api` | `signal-messenger-python-api` | Async wrapper (aiohttp) auto-generated from signal-cli-rest-api Swagger. Generated with Claude 3.7. Low community activity, unclear maintenance cadence. |
| `signalbot` | `signalbot` | Framework for building Signal bots (receive + respond). Overkill — we only need send + webhook receive. |
| `pysignald` | `pysignald` | Uses the `signald` daemon (different from signal-cli-rest-api). Incompatible with our Docker stack. |

**Assessment**: signal-cli-rest-api exposes a straightforward REST interface at `/v2/send` and `/v1/health`. Our `SignalAdapter` is ~60 lines of `httpx` and already async-native. Adding a third-party wrapper around a two-endpoint API buys nothing and introduces a dependency on an auto-generated, lightly-maintained library.

**Verdict**: keep our hand-rolled `SignalAdapter`. No action needed.

---

### 11.4 Email — `aiosmtplib` (what we already use; correct choice)

| | |
|---|---|
| PyPI | `aiosmtplib` |
| Version | 5.1.0 (Jan 2026) |
| Python | ≥3.10 |
| Dependencies | None (zero external deps) |
| Async | Yes — purpose-built for asyncio |
| Licence | MIT |

Production-stable, zero dependencies, actively maintained by Cole Maclean. Supports STARTTLS, TLS/SSL, SMTP AUTH, and both the high-level `send()` coroutine (what we use) and a lower-level `SMTP` class for connection reuse.

We're already using this correctly. The one gap is connection pooling — `aiosmtplib` opens a new connection per send. For high volume this matters; for a conduct system with low message rates it doesn't.

**Verdict**: no change needed. Already the right library.

---

### 11.5 Jambonz — no Python SDK exists; REST calls are the way

Jambonz has official SDKs for **Node.js only** (standard SDK, WebSocket SDK, Node-RED plugin). No Python SDK exists and none is planned as far as public docs show.

The REST API we call (`POST /v1/Accounts/{sid}/Calls`) is a single endpoint with a straightforward JSON payload. Our `JambonzAdapter` (~80 lines of `httpx`) covers exactly what's needed. There is no Python community library that adds value here.

The only thing worth considering: the Jambonz **WebSocket call-control API** (for real-time verb injection mid-call — play audio, collect DTMF, etc.). That would require a raw WebSocket client (`websockets` or `aiohttp.ClientSession.ws_connect`), but we don't currently need it.

**Verdict**: keep our hand-rolled `JambonzAdapter`. No library to adopt.

---

### 11.6 Webhook receiving — FastAPI is the library

For *receiving* inbound webhooks (Signal reactions, Jambonz DTMF, future Mattermost outgoing webhooks) we already use FastAPI route handlers. This is the correct approach — no dedicated webhook-receiver library adds value in a FastAPI codebase.

The one thing worth adding is **signature verification** for inbound payloads:

| Service | Verification mechanism | Python helper |
|---|---|---|
| Slack | `X-Slack-Signature` HMAC-SHA256 | `slack_sdk.signature.SignatureVerifier` |
| Mattermost outgoing webhook | Token in payload `token` field | manual compare |
| Jambonz | Bearer token or shared secret | manual header check |
| signal-cli-rest-api | No signing — internal Docker network only | n/a |

For Slack specifically, `slack_sdk.signature.SignatureVerifier` handles the timestamp + HMAC check that prevents replay attacks. Worth adding to `/webhook/signal` if we ever expose it externally.

---

### 11.7 Summary — adopt, keep, or skip

| Channel | Current approach | Recommended library | Action |
|---|---|---|---|
| Slack | hand-rolled `httpx` webhook POST | `slack-sdk` (`AsyncWebClient`) | **Upgrade** — get `ts`, enable threading, signature verification |
| Mattermost | hand-rolled `httpx` webhook POST | `mattermostautodriver` | **Upgrade** — get post `id`, enable threading, `requested_ack` |
| Signal | hand-rolled `httpx` REST | none — keep `httpx` | **No change** |
| Email | `aiosmtplib` | `aiosmtplib` (already used) | **No change** |
| Jambonz | hand-rolled `httpx` REST | none — keep `httpx` | **No change** |
| Inbound webhooks | FastAPI route handlers | FastAPI (already used) | **No change**; add `slack_sdk.signature` for Slack |

---

## 12. API deep-dive: Slack and Mattermost

### 12.1 Slack — chat.postMessage vs incoming webhooks

Our adapter currently uses an **incoming webhook URL** (no auth, fixed channel, returns just `ok`). The full `chat.postMessage` API is richer:

**`POST https://slack.com/api/chat.postMessage`**

Auth: `Authorization: Bearer xoxb-...` (Bot token with `chat:write` scope; add `chat:write.public` to post to public channels the bot hasn't joined).

Key request fields:
| Field | Type | Notes |
|---|---|---|
| `channel` | string | Channel ID, name, or user ID |
| `text` | string | Message body; 4000-char soft limit; required as fallback when using `blocks` |
| `blocks` | array | Block Kit structured content (richer than plain text) |
| `attachments` | array | Legacy rich content; max 100 |
| `thread_ts` | string | Parent message `ts` — makes this a threaded reply |
| `reply_broadcast` | bool | If true, thread reply also appears in channel |
| `mrkdwn` | bool | Default true; enables `*bold*`, `_italic_`, `<url|text>` syntax |
| `metadata` | object | Custom JSON payload for event metadata |

**Success response**:
```json
{
  "ok": true,
  "channel": "C01234ABCDE",
  "ts": "1712345678.123456",
  "message": { "text": "...", "type": "message", ... }
}
```

The `ts` value is the message's unique timestamp-ID. To thread a reply to this message, pass `"thread_ts": "1712345678.123456"` in a subsequent `chat.postMessage` call. **Never use a reply's own `ts` as `thread_ts`; always use the parent's `ts`.**

**Key error codes**:
| Code | Meaning |
|---|---|
| `channel_not_found` | Invalid channel |
| `not_in_channel` | Bot not in private channel (add `chat:write.public` or invite bot) |
| `no_text` | Neither `text` nor `blocks` provided |
| `rate_limited` | Slow down — ~1 msg/sec per channel |
| `invalid_blocks` | Malformed Block Kit JSON |

**Rate limits**: approximately 1 message per second per channel; workspace-wide burst of several hundred per minute.

**What this means for our code**: if we switch `SlackAdapter` from an incoming webhook to `chat.postMessage`, we get back `ts`, can store it as `message_id`, and thread the ACK confirmation as a reply using `thread_ts`. The ACK confirmation becomes a visible reply to the original alert rather than a new top-level post.

---

### 12.2 Mattermost — Posts API vs incoming webhooks

#### Incoming webhooks (what we use now)

POST to webhook URL, payload `{"text": "..."}`. Response: `HTTP 200` with body `ok` (plain text). **No post ID returned.** No threading support. All posts display a BOT badge.

Supported payload fields: `text`, `channel` (override), `username`, `icon_url`, `icon_emoji`, `attachments`, `type`, `props`, `priority`.

#### Posts API (upgrade path) -- do this.

**`POST /api/v4/posts`**

Auth: `Authorization: Bearer {user_or_bot_token}` (token needs `create_post` permission on the channel).

Request body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `channel_id` | string | yes | Target channel |
| `message` | string | yes | Markdown-formatted text |
| `root_id` | string | no | Post ID to reply to — creates a thread |
| `file_ids` | array | no | Up to 5 attached file IDs |
| `props` | object | no | Arbitrary metadata stored with the post |
| `metadata.priority.priority` | string | no | `""`, `"important"`, or `"urgent"` — displays a priority label |
| `metadata.priority.requested_ack` | bool | no | If true, recipients see an "Acknowledge" button (see below) |

**Success response** (`201 Created`): a full Post object:
```json
{
  "id": "abc123def456...",
  "create_at": 1712345678000,
  "user_id": "...",
  "channel_id": "...",
  "root_id": "",
  "message": "...",
  "type": "",
  "props": {},
  "metadata": {}
}
```

The `id` field is the post's unique identifier. To thread a reply: set `root_id` to this `id` in a subsequent CreatePost call.

**What this means for our code**: switching `MattermostAdapter` from incoming webhook to Posts API requires:
1. A bot/personal access token (create a bot account in Mattermost System Console → Integrations → Bot Accounts)
2. Knowing the `channel_id` of the target channel (not the display name)
3. Storing the returned post `id` as `message_id` in the notifications table
4. Threading ACK confirmation via `root_id: original_post_id`
5. Config change: store token + channel_id instead of webhook URL

---

### 12.3 Mattermost native acknowledgements (`SaveAcknowledgementForPost`)

**`POST /api/v4/users/{user_id}/posts/{post_id}/ack`**

This is Mattermost's own built-in acknowledgement system — **entirely separate from our system's ACK concept**. It lets Mattermost users "acknowledge" that they have read an important post, and displays a count of who has acked it.

Requirements:
- The post must have been created with `metadata.priority.requested_ack: true`
- Caller needs `read_channel` permission on the channel (standard for any channel member)
- Minimum Mattermost server version: 7.7
- Likely requires Professional or Enterprise plan (post priority/acknowledgement is a commercial feature — check your Mattermost edition)

Response (`200 OK`): a `PostAcknowledgement` object with `user_id`, `post_id`, and `acknowledged_at` (Unix milliseconds).

**Removing an ack**: `DELETE /api/v4/users/{user_id}/posts/{post_id}/ack` — only within 5 minutes of acking.

**How this relates to our system**:

Our ACK ("this conduct team member is taking responsibility for this case") is a different concept from Mattermost's "I've read this message" ack. However, they can complement each other:

- If we switch to the Posts API and set `requested_ack: true`, Mattermost will track which team members have acknowledged the alert post inside Mattermost itself
- A team member pressing ACK in Mattermost does NOT call our system's `mark_acked` — those are independent
- Our system's ACK (panel button, email link, Signal reaction) marks the case as owned by someone
- Mattermost's ack is a "I've seen this" receipt visible to the whole team

For a conduct team, both are useful: Mattermost ack gives visibility ("everyone has at least seen it"), our system's ACK means "someone is handling it".

**To use `requested_ack` in our Posts API upgrade**:
```python
payload = {
    "channel_id": channel_id,
    "message": text,
    "metadata": {
        "priority": {
            "priority": "urgent" if alert.urgency in ("urgent", "high") else "",
            "requested_ack": True,
        }
    },
}
```

---

## 13. PagerDuty-style rich cards with interactive buttons

### 13.1 What PagerDuty does (reference screenshot)

The PagerDuty Slack integration shows:

- **Coloured left-border card** (red = triggered) with the incident title as a bold hyperlink
- **Two-column field grid**: Assigned, Service, Dial-in Number, Meeting URL
- **Status line** inline in the card: "Reassigned to Bruce Soord | Today at 11:12 PM"
- **Acknowledge** and **Resolve** buttons directly in the card — clicking them updates the card in-place and posts a status update (yellow border, "Acknowledged by Jon Sykes") as a new message in the same channel
- When a team member comments in Slack they appear as normal messages threaded below the card
- A second card appears for the reassignment event (same format, different action type)

The key UX principles:
1. The card updates **in-place** when acknowledged — the buttons disappear, the border turns green, the card reads "Acknowledged by X"
2. State transitions (acknowledge, reassign) produce **new status messages** in the channel rather than cluttering the original card
3. No new top-level post for the ACK — keeps the channel clean
4. The card is self-contained: urgency, location, assigned person, and action buttons all visible without clicking anything

---

### 13.2 Our equivalent design

#### Slack alert card (Block Kit inside `attachments` for coloured border)

Slack Block Kit `blocks` at top level don't support a coloured left border. The coloured border comes from the legacy `attachments[].color` field. PagerDuty (and everyone who does this correctly) uses `attachments[0].color + attachments[0].blocks` to combine Block Kit layout with a coloured sidebar.

```json
{
  "text": "🔴 URGENT – WORD-WORD-WORD-WORD (notification fallback)",
  "attachments": [
    {
      "color": "#c62828",
      "blocks": [
        {
          "type": "section",
          "text": {
            "type": "mrkdwn",
            "text": "*🔴 New URGENT case — <https://panel.emf-forms.internal/cases/UUID|WORD-WORD-WORD-WORD>*"
          }
        },
        {
          "type": "section",
          "fields": [
            {"type": "mrkdwn", "text": "*Event*\nEMF 2026"},
            {"type": "mrkdwn", "text": "*Location*\nNear the bar"}
          ]
        },
        {
          "type": "actions",
          "block_id": "case_actions",
          "elements": [
            {
              "type": "button",
              "text": {"type": "plain_text", "text": "Acknowledge"},
              "style": "primary",
              "action_id": "ack_case",
              "value": "<notification_uuid>"
            },
            {
              "type": "button",
              "text": {"type": "plain_text", "text": "View in Panel"},
              "action_id": "view_case",
              "url": "https://panel.emf-forms.internal/cases/UUID"
            }
          ]
        }
      ]
    }
  ]
}
```

Urgency colour mapping (matches existing CSS variables):

| Urgency | Colour |
|---|---|
| `urgent` | `#c62828` (red) |
| `high` | `#e65100` (orange) |
| `medium` | `#1565c0` (blue) |
| `low` | `#558b2f` (green) |

**After ACK** — the card is updated in-place (via `chat.update`) to remove the actions block, turn the border green, and add who acked it:

```json
{
  "text": "✅ WORD-WORD-WORD-WORD acknowledged",
  "attachments": [
    {
      "color": "#2e7d32",
      "blocks": [
        {
          "type": "section",
          "text": {
            "type": "mrkdwn",
            "text": "*🔴 URGENT — <https://panel.../cases/UUID|WORD-WORD-WORD-WORD>*\n✅ *Acknowledged by* @adam"
          }
        },
        {
          "type": "section",
          "fields": [
            {"type": "mrkdwn", "text": "*Event*\nEMF 2026"},
            {"type": "mrkdwn", "text": "*Location*\nNear the bar"}
          ]
        }
      ]
    }
  ]
}
```

---

#### Mattermost alert card (`props.attachments` with `actions`)

Mattermost supports Slack-compatible attachment format via `props.attachments`. Interactive buttons use an `actions` array with an `integration.url` — Mattermost POSTs to that URL when clicked.

```json
{
  "channel_id": "CHANNEL_ID",
  "message": "🔴 New URGENT case: WORD-WORD-WORD-WORD",
  "props": {
    "attachments": [
      {
        "fallback": "New URGENT case: WORD-WORD-WORD-WORD",
        "color": "#c62828",
        "title": "🔴 New URGENT case: WORD-WORD-WORD-WORD",
        "title_link": "https://panel.emf-forms.internal/cases/UUID",
        "fields": [
          {"title": "Event", "value": "EMF 2026", "short": true},
          {"title": "Location", "value": "Near the bar", "short": true}
        ],
        "actions": [
          {
            "name": "Acknowledge",
            "type": "button",
            "integration": {
              "url": "http://msg-router:8002/webhook/mattermost/action",
              "context": {
                "action": "ack",
                "notification_id": "NOTIFICATION_UUID"
              }
            }
          }
        ]
      }
    ]
  }
}
```

**After ACK** — the post is updated in-place via `PUT /api/v4/posts/{post_id}` to remove `actions` and add the acked-by field:

```json
{
  "id": "POST_ID",
  "message": "✅ WORD-WORD-WORD-WORD acknowledged by @adam",
  "props": {
    "attachments": [
      {
        "color": "#2e7d32",
        "title": "✅ URGENT case acknowledged: WORD-WORD-WORD-WORD",
        "title_link": "https://panel.emf-forms.internal/cases/UUID",
        "fields": [
          {"title": "Event", "value": "EMF 2026", "short": true},
          {"title": "Location", "value": "Near the bar", "short": true},
          {"title": "Acknowledged by", "value": "@adam", "short": true}
        ]
      }
    ]
  }
}
```

---

### 13.3 Interaction flow — button click

#### Slack

1. User clicks "Acknowledge" button in Slack
2. Slack POSTs to `POST /webhook/slack/action` with `Content-Type: application/x-www-form-urlencoded`, body: `payload=<URL-encoded JSON>`
3. The `payload` JSON contains:
   - `type`: `"block_actions"`
   - `actions[0].action_id`: `"ack_case"`
   - `actions[0].value`: notification UUID
   - `user.name`: Slack username of clicker
   - `response_url`: URL to POST updated message to (valid for 30 min)
   - `container.channel_id` + `message.ts`: for `chat.update`
4. Router verifies `X-Slack-Signature` header using signing secret
5. Calls `mark_acked(notification_id, slack_username, session)`
6. POSTs updated (green, no buttons, "Acknowledged by @name") message body to `response_url`
7. Returns `HTTP 200` immediately (Slack requires response within 3 seconds)

#### Mattermost

1. User clicks "Acknowledge" button in Mattermost
2. Mattermost POSTs to `integration.url` (`POST /webhook/mattermost/action`) with JSON body:
   ```json
   {
     "user_id": "...",
     "user_name": "adam",
     "channel_id": "...",
     "post_id": "POST_ID",
     "context": {"action": "ack", "notification_id": "NOTIFICATION_UUID"}
   }
   ```
3. Router calls `mark_acked(notification_id, user_name, session)`
4. Router calls `PUT /api/v4/posts/{post_id}` with updated (green, no button, acked-by) post body
5. Returns `HTTP 200` with JSON `{"update": {"message": "Acknowledged"}}` (Mattermost uses this to show a brief ephemeral confirmation)

---

### 13.4 Non-button ACK paths (panel, email link, Signal reaction)

When ACK comes from outside Slack/Mattermost, we still want to update the card in-place so the Slack/Mattermost view is consistent.

- `notifications.message_id` for Slack is stored as `"CHANNEL_ID|TS"` so `send_ack_confirmation` can call `chat.update(channel=channel_id, ts=ts, ...)`
- `notifications.message_id` for Mattermost is the post `id` so `send_ack_confirmation` can call `PUT /api/v4/posts/{post_id}`
- `send_ack_confirmation(alert, message_id, acked_by)` gains an `acked_by` parameter so the updated card shows who pressed ACK

---

### 13.5 New config fields required

**`config.json`** additions to `AppConfig`:
```json
{
  "slack_channel_id": "C01234ABCDE",
  "mattermost_url": "https://mattermost.emfcamp.org",
  "mattermost_channel_id": "CHANNEL_ID"
}
```

**`.env`** additions to router `Settings`:
```env
SLACK_BOT_TOKEN=xoxb-...          # chat:write + chat:write.public scopes
SLACK_SIGNING_SECRET=...          # for verifying inbound interaction payloads
MATTERMOST_TOKEN=...              # personal access token or bot account token
ROUTER_BASE_URL=http://msg-router:8002   # where Mattermost can reach our action endpoint
```

The old `slack_webhook` and `mattermost_webhook` fields in `config.json` remain for backward compatibility (plain-text fallback if bot token not configured) but the new rich-card path takes precedence when `slack_bot_token` / `mattermost_token` are set.

---

### 13.6 New router endpoints required

| Endpoint | Method | Purpose |
|---|---|---|
| `/webhook/slack/action` | POST | Receives Slack button interactions (`block_actions`); verifies signature, calls `mark_acked`, updates card via `response_url` |
| `/webhook/mattermost/action` | POST | Receives Mattermost button clicks; calls `mark_acked`, updates post via `PUT /api/v4/posts/{id}` |

---

### 13.7 Code changes required (implementation plan)

| File | Change |
|---|---|
| `shared/src/emf_shared/config.py` | Add `slack_channel_id`, `mattermost_url`, `mattermost_channel_id` to `AppConfig` |
| `apps/router/src/router/settings.py` | Add `slack_bot_token`, `slack_signing_secret`, `mattermost_token`, `router_base_url` |
| `apps/router/src/router/channels/base.py` | Add `acked_by: str = ""` param to `send_ack_confirmation` |
| `apps/router/src/router/channels/slack.py` | Full rewrite: use `slack-sdk` `AsyncWebClient`, Block Kit card with button, `chat.update` on ACK |
| `apps/router/src/router/channels/mattermost.py` | Full rewrite: use `httpx` Posts API, attachment card with button, `PUT` update on ACK |
| `apps/router/src/router/channels/email.py` | Add `acked_by` param to `send_ack_confirmation` (pass-through, email threads don't show it) |
| `apps/router/src/router/channels/signal.py` | Add `acked_by` param to `send_ack_confirmation` (pass-through) |
| `apps/router/src/router/alert_router.py` | Thread `acked_by` through `send_ack_confirmations`; store `channel_id\|ts` as Slack `message_id` |
| `apps/router/src/router/main.py` | Add `/webhook/slack/action` and `/webhook/mattermost/action` endpoints; update adapter init |
| `apps/router/pyproject.toml` | Add `slack-sdk>=3.40.1` |

---

### 13.8 Existing libraries that reduce the implementation work

#### Mattermost — what exists, and the public-URL problem

Ideally we'd do some signature/secrets matching to increase confidence of coming from us, not third-party. We don't need full cryptographic signing, but maybe compare a secret sent against a secret known?

Unlike Slack, **Mattermost has no Socket Mode equivalent**. When a user clicks an interactive button, Mattermost always sends an HTTP POST to the `integration.url` you configured in the action definition. That URL must be reachable from the Mattermost server — there is no outbound-WebSocket option.

**Full request payload Mattermost sends on button click:**
```json
{
  "user_id": "rd49ehbqyjytddasoownkuqrxe",
  "user_name": "adam",
  "post_id": "gqrnh3675jfxzftnjyjfe4udeh",
  "channel_id": "j6j53p28k6urx15fpcgsr20psq",
  "team_id": "5xxzt146eax4tul69409opqjlf",
  "context": {
    "action": "ack",
    "notification_id": "NOTIFICATION_UUID"
  }
}
```

**Integration response options** (HTTP 200 + JSON):
- Update the original post: `{"update": {"message": "...", "props": {...}}}`
- Ephemeral message to the clicker: `{"ephemeral_text": "You acknowledged this."}`
- Both: include both keys

**The public-URL question:**

| Mattermost deployment | Integration URL | Public URL needed? |
|---|---|---|
| Self-hosted on same Docker Compose network | `http://msg-router:8002/webhook/mattermost/action` | No — internal network |
| Self-hosted on a different server/network | `https://router.emf-forms.internal/webhook/mattermost/action` | Yes — via Caddy |
| Cloud-hosted (mattermost.com) | `https://router.emf-forms.internal/webhook/mattermost/action` | Yes — via Caddy |

For EMF's setup (everything in Docker Compose on one server), the integration URL can use the Docker-internal hostname. **No Caddy routing needed** for self-hosted Mattermost.

**Python frameworks for Mattermost bots:**

`mmpy_bot` (PyPI: `mmpy-bot`, v2.x, Jan 2025) — uses WebSocket to **receive** Mattermost events (chat messages, etc.) without a public URL. But for button clicks it offers no native handler — the repo owner's own response to the question "how do I handle button clicks?" was to point at Mattermost docs saying you need a separate HTTP endpoint. Snyk classifies it as Inactive. **Not useful for our case.**

`mattermostautodriver` — wraps the full Mattermost API, can receive WebSocket events, but is synchronous-only. For our two operations (create post, update post), wrapping in `asyncio.to_thread` is more overhead than just using `httpx`.

**Conclusion for Mattermost**: no framework adds meaningful value. Handle the button POST in a plain FastAPI endpoint (`/webhook/mattermost/action`). Use `httpx` directly for `POST /api/v4/posts` (create) and `PUT /api/v4/posts/{id}` (update). This is ~50 lines of code and is fully async.

---

#### `slack-bolt` — the right tool for Slack interactive messages

`slack-bolt` (PyPI: `slack-bolt`, v1.27.0, Nov 2025, official Slack library, MIT) is a higher-level framework built on top of `slack-sdk`. It eliminates most of the boilerplate for handling interactive messages:

| What bolt handles for us | Without bolt |
|---|---|
| Signature verification (`X-Slack-Signature`) | Manual HMAC check on every request |
| Parsing `payload=<url-encoded-json>` form body | Manual `urllib.parse.unquote` + `json.loads` |
| Dispatching by `action_id` | Manual `if action_id == "ack_case"` routing |
| `ack()` — Slack requires a response within 3 seconds | Manual HTTP 200 before doing async work |
| `respond()` — updates the original message via `response_url` | Manual POST to `response_url` |
| `app.client` — pre-authenticated `AsyncWebClient` | Construct client manually with token |

**Socket Mode** (the standout feature): instead of exposing an HTTP endpoint that Slack calls (which requires a public URL, Caddy proxy, etc.), Socket Mode opens an **outbound WebSocket** from our router to Slack. Slack sends interactions over this WebSocket. No public endpoint needed, no ngrok, no Caddy routing for this path.

```
Without Socket Mode:  Slack → HTTPS → Caddy → msg-router:8002/webhook/slack/action
With Socket Mode:     msg-router → WSS → Slack (outbound, no inbound port needed)
```

Socket Mode needs one extra token: an **App-Level Token** (`xapp-`) with the `connections:write` scope, in addition to the Bot Token (`xoxb-`).

**Button click handler with bolt (full example)**:

```python
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

app = AsyncApp(token=slack_bot_token)  # no signing_secret needed in Socket Mode

@app.action("ack_case")
async def handle_ack(ack, body, respond, session):
    await ack()  # must call within 3 seconds
    notification_id = body["actions"][0]["value"]
    user_name = body["user"]["name"]
    alert = await mark_acked(notification_id, user_name, session)
    await respond({  # updates the original message in-place
        "replace_original": True,
        "text": f"✅ {alert.friendly_id} acknowledged",
        "attachments": [{"color": "#2e7d32", "blocks": [...acked_blocks...]}]
    })

# Start Socket Mode listener alongside FastAPI
handler = AsyncSocketModeHandler(app, app_token=slack_app_token)
await handler.start_async()  # called in lifespan
```

**FastAPI adapter** (if HTTP mode preferred over Socket Mode):

```python
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
bolt_handler = AsyncSlackRequestHandler(app)

@fastapi_app.post("/webhook/slack/action")
async def slack_events(req: Request):
    return await bolt_handler.handle(req)
```

**Verdict**: use `slack-bolt` with **Socket Mode**. Eliminates the public webhook endpoint requirement entirely, handles all Slack protocol concerns, and the `respond()` utility does exactly the "update card in place" behaviour we want. Replace raw `slack-sdk` plan with `slack-bolt`.

---

#### For Mattermost — no equivalent framework; `httpx` is correct

No Mattermost equivalent of Bolt exists. The interactive button flow is simpler on the Mattermost side anyway:

- Mattermost POSTs plain JSON to our `integration.url` — no signature scheme, no form-encoded payload, just a JSON body with `context`
- `mattermostautodriver` is sync-only (wraps the whole Mattermost API) — using `asyncio.to_thread` around it adds overhead we don't need
- For our two operations (create post, update post), direct `httpx` calls are 10 lines each and fully async-native

**Verdict**: for Mattermost, use `httpx` directly against the Posts API. No library adoption needed beyond what we already have.

---

#### Revised library summary

| Channel | Library | Why |
|---|---|---|
| Mattermost send + interactive buttons | `httpx` (already a dep) | Direct Posts API; no sync wrapper overhead; button POST is plain JSON to FastAPI handler; internal Docker URL avoids Caddy |
| Slack send + interactive buttons | `slack-bolt` (`AsyncApp` + Socket Mode) | Handles all Slack protocol; Socket Mode avoids public URL entirely; `respond()` does in-place card update |
| Signal | `httpx` (already a dep) | Two-endpoint API; no library value |
| Email | `aiosmtplib` (already a dep) | Already correct |
| Jambonz | `httpx` (already a dep) | No Python SDK exists |

**New dep to add**: `slack-bolt>=1.27.0` (brings `slack-sdk` with it as a transitive dep — no need to add both).

**Revised `.env` for Slack** (Socket Mode removes need for `SLACK_SIGNING_SECRET`):
```env
SLACK_BOT_TOKEN=xoxb-...    # chat:write scope
SLACK_APP_TOKEN=xapp-...    # connections:write scope (Socket Mode only)
```

**`.env` for Mattermost** (Posts API, no webhook URL):
```env
MATTERMOST_TOKEN=...         # personal access token or bot account token
```

**`config.json`** (non-secret config):
```json
{
  "mattermost_url": "https://mattermost.emfcamp.org",
  "mattermost_channel_id": "CHANNEL_ID",
  "slack_channel_id": "C01234ABCDE"
}
```
