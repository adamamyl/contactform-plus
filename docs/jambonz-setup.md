# Jambonz Setup

Jambonz handles outbound phone calls to conduct team members when a new case arrives. The `jambonz-adapter` service bridges the EMF notification router and the Jambonz telephony API.

## Overview

When a case is submitted, the router can initiate an outbound call to a SIP endpoint (a softphone, a physical phone, or a SIP trunk). The call plays a synthesised audio summary of the case and waits for the responder to press a digit to acknowledge.

**Services involved:**

| Service | Container | Port |
|---------|-----------|------|
| Notification router | `infra-msg-router-1` | 8002 |
| Jambonz adapter | `emf-jambonz` | 8004 |
| TTS (Piper) | `infra-tts-1` | 8003 |
| Jambonz API (external) | `api.jambonz.cloud` | 443 |

---

## Step 1 — Create a jambonz.cloud account

1. Go to https://jambonz.cloud and sign up for a free account.
2. After login, click **Accounts** in the left sidebar. You will see your account listed with a **SID** — this is a UUID like `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`. Note it down as `JAMBONZ_ACCOUNT_SID`.

> **Gotcha:** The Accounts page shows multiple identifiers. You want the one labelled **SID** — a hyphenated UUID. Ignore any base64-looking strings.

---

## Step 2 — Create an API key

1. In the left sidebar, go to **Settings** → **API Keys**.
2. Click **Add**, give it a name (e.g. `emf-conduct`), and copy the key. This is `JAMBONZ_API_KEY`.

---

## Step 3 — Provision a phone number (optional for softphone-only setups)

If you want calls to originate from a real phone number (shown as caller ID):

1. Go to **Phone Numbers** → **Add**.
2. Choose a number. Note it down in E.164 format (e.g. `+447123456789`) as `JAMBONZ_FROM_NUMBER`.

If you're testing with a softphone only and don't need a real DID, you can use any E.164 string as a placeholder — Jambonz will accept it for SIP-to-SIP calls.

---

## Step 4 — Set up a SIP user for your softphone (Linphone / Zoiper / etc.)

This creates a SIP account that your softphone will register with, so Jambonz can call it.

1. In jambonz.cloud, go to **SIP Realm** in the sidebar.
2. Note your **SIP realm hostname** (e.g. `ABCDE.sip.jambonz.cloud`).
3. Click **Add SIP User**, choose a username (e.g. `staging`) and a password.
4. The full SIP address is `username@realm` — e.g. `staging@ABCDE.sip.jambonz.cloud`. Note this down for `call_group_number` in `config.json`.

**Configuring Linphone:**

1. Open Linphone → **Use SIP account**.
2. Fill in:
   - **Username**: the username you created (e.g. `staging`)
   - **Password**: the password you set
   - **Domain**: the SIP realm hostname (e.g. `ABCDE.sip.jambonz.cloud`)
3. Linphone should show a green "registered" status. If it shows red, double-check the domain — do not include `sip:` prefix here.

---

## Step 5 — Create a Jambonz application

The application tells Jambonz which URLs to call when a call starts and when its status changes.

1. Go to **Applications** → **Add**.
2. Fill in:
   - **Name**: e.g. `EMF Conduct`
   - **Calling webhook**: `https://panel.<your-domain>/webhook/jambonz/call`
   - **Call status webhook**: `https://panel.<your-domain>/webhook/jambonz/status`
3. Save and note the **Application SID** (UUID). This is `JAMBONZ_APPLICATION_SID`.

On wolfcraig the domain is `panel.emf.thisparish.org`, so:
- Calling webhook: `https://panel.emf.thisparish.org/webhook/jambonz/call`
- Call status webhook: `https://panel.emf.thisparish.org/webhook/jambonz/status`

> **How the webhook path works:** `panel.emf.thisparish.org` is handled by ghost-docker's Caddy. The Caddyfile has a `handle /webhook/jambonz*` block that proxies those paths to the `emf-jambonz` container (port 8004), while all other paths go to the panel. The jambonz-adapter is not separately exposed — it rides on the panel's domain.

---

## Step 6 — Configure .env

Add/update these values in `/opt/emf-conduct/.env`:

```bash
JAMBONZ_API_URL=https://api.jambonz.cloud
JAMBONZ_API_KEY=<your-api-key>
JAMBONZ_ACCOUNT_SID=<uuid-from-step-1>
JAMBONZ_APPLICATION_SID=<uuid-from-step-5>
JAMBONZ_FROM_NUMBER=+447123456789
TTS_AUDIO_BASE_URL=https://panel.emf.thisparish.org
JAMBONZ_WEBHOOK_BASE_URL=https://panel.emf.thisparish.org
```

**Why `TTS_AUDIO_BASE_URL`?** Jambonz (a cloud service) needs to fetch the synthesised audio file over the internet. The `emf-jambonz` container exposes `/audio/{filename}` as a public proxy to the internal TTS service. Setting this to the public panel domain means the audio URL sent to Jambonz is `https://panel.emf.thisparish.org/audio/...`, which Jambonz can reach. Without this, the URL would be the internal `http://tts:8003/...` which Jambonz cannot reach.

**Why `JAMBONZ_WEBHOOK_BASE_URL`?** After playing the audio the adapter tells Jambonz to send DTMF digits back to an `actionHook` URL. This must be a public URL — the same panel domain.

---

## Step 7 — Configure config.json

Add `jambonz_mode` and `call_group_number` to the active event in `config.json`:

```json
{
  "name": "EMF 2026",
  "jambonz_mode": "always",
  "call_group_number": "staging@ABCDE.sip.jambonz.cloud"
}
```

`jambonz_mode` options:
- `"disabled"` (default) — no calls are made
- `"always"` — call on every new case
- `"high_priority_only"` — call only for `high` or `urgent` urgency

`call_group_number` format determines how Jambonz routes the call:
- `+441234567890` (starts with `+`) → PSTN phone call
- `sip:user@host` (starts with `sip:`) → SIP URI call
- `user@realm` (anything else) → Jambonz registered SIP user call ← use this for softphones

---

## Step 8 — Apply and verify

Force-recreate the containers so they pick up the new env/config:

```bash
cd /opt/emf-conduct
docker compose -f infra/docker-compose.yml -f infra/docker-compose.wolfcraig.yml \
  up -d --force-recreate msg-router jambonz-adapter
```

Check the jambonz-adapter health (the Jambonz API must be reachable and credentials valid):

```bash
docker compose -f infra/docker-compose.yml exec jambonz-adapter \
  python3 -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8004/health').read())"
```

Expected: `{"status":"ok","checks":{"jambonz_api":"ok"},"version":"0.1.0"}`

If `jambonz_api` shows `"error"`, run the credential check manually:

```bash
docker compose -f infra/docker-compose.yml exec jambonz-adapter \
  python3 -c "
import urllib.request, os, json
url = os.environ['JAMBONZ_API_URL'].rstrip('/') + '/v1/Accounts/' + os.environ['JAMBONZ_ACCOUNT_SID']
req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + os.environ['JAMBONZ_API_KEY']})
try:
    resp = urllib.request.urlopen(req)
    print('OK:', resp.read()[:200])
except urllib.error.HTTPError as e:
    print('HTTP', e.code, e.read()[:200])
except Exception as e:
    print('ERROR:', e)
"
```

Common failures:
- **Bad request / 400**: `JAMBONZ_ACCOUNT_SID` is wrong — verify it's the UUID from the Accounts page
- **401 Unauthorized**: `JAMBONZ_API_KEY` is wrong
- **Connection refused / timeout**: `JAMBONZ_API_URL` is wrong or the container can't reach the internet

---

## Step 9 — Test a call

Retrigger routing for an existing case (replace the UUID with a real case from `forms.cases`):

```bash
docker exec infra-postgres-1 \
  psql -U emf_forms_admin emf_forms \
  -c "SELECT pg_notify('new_case', '<case-uuid-here>');"
```

Watch the logs in another terminal:

```bash
docker compose -f infra/docker-compose.yml logs -f msg-router jambonz-adapter tts
```

You should see:
1. Router logs: TTS synthesis, Jambonz Calls API returns 201, call SID registered
2. Jambonz adapter logs: `CALL sid=... status=... audio=... case=...`
3. Linphone rings — answer and press **1** to ACK

---

## DTMF digit reference

| Digit | Action |
|-------|--------|
| 1 | Acknowledge — marks the case as acked in the DB, notifies all other channels |
| Anything else / no input | Ignored; router will retry on schedule |

---

## Caddy routing (wolfcraig)

The Caddyfile at `infra/caddy/Caddyfile.wolfcraig` (generated by `scripts/generate_caddyfile.py`) already contains:

```caddyfile
panel.emf.thisparish.org {
    handle /webhook/jambonz* {
        reverse_proxy emf-jambonz:8004
    }
    handle /audio/* {
        reverse_proxy emf-jambonz:8004
    }
    handle {
        reverse_proxy emf-panel:8001
    }
}
```

Both `/webhook/jambonz*` **and** `/audio/*` must proxy to `emf-jambonz`. Jambonz (cloud) fetches the synthesised audio file over the public internet via the `/audio/{filename}` proxy endpoint before playing it on the call. Without the `/audio/*` route, calls connect but play silence.

If you add a new domain, run `uv run scripts/generate_caddyfile.py` and then:

```bash
docker compose -f /opt/ghost-docker/compose.yml restart caddy
```

(Do not use `caddy reload` — it does not re-read bind-mounted files on this server.)

---

## For self-hosted Jambonz

Set `JAMBONZ_API_URL` to your instance's API endpoint (e.g. `https://jambonz.emfcamp.org`). Everything else is the same. The application webhook URLs must be publicly reachable from wherever the Jambonz instance runs.
