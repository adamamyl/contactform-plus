# Jambonz Setup

Jambonz handles outbound phone calls to conduct team members when an urgent case arrives. The `jambonz-adapter` service bridges between the EMF notification router and the Jambonz telephony API.

## Deployment model

For events: self-hosted Jambonz on EMF infra (speak to the EMF infra team).
For testing/development: [jambonz.cloud](https://jambonz.cloud) free tier.

## Account setup (jambonz.cloud)

1. Sign up at https://jambonz.cloud
2. Under **Accounts** → your account → note the **Account SID**
3. Under **Applications** → create a new application:
   - **Calling Webhook**: `https://panel.emf-forms.internal/webhook/jambonz` (where Jambonz will POST DTMF events)
   - Note the **Application SID**
4. Under **API Keys** → create a key → note the **API Key**
5. Under **Phone Numbers** → provision a DID (inbound number not needed for outbound-only use)
   - Note the number you'll use as `JAMBONZ_FROM_NUMBER`

## .env configuration

```
JAMBONZ_API_URL=https://api.jambonz.cloud
JAMBONZ_API_KEY=<your-api-key>
JAMBONZ_ACCOUNT_SID=<your-account-sid>
JAMBONZ_APPLICATION_SID=<your-app-sid>
JAMBONZ_FROM_NUMBER=+441234567890
```

For self-hosted, set `JAMBONZ_API_URL` to your instance's API endpoint (e.g. `https://jambonz.emfcamp.org`).

## How it works

1. A case arrives at urgency `urgent` or `high`
2. The `msg-router` service fires a `pg_notify('new_case', ...)` event
3. The router calls `jambonz-adapter` via internal HTTP
4. The adapter calls `TTS` to synthesise an audio clip for the case
5. The adapter posts to Jambonz `/v1/Accounts/{sid}/Calls` to initiate an outbound call
6. Jambonz plays the audio; the responder presses:
   - **1** → ACK (case acknowledged)
   - **2** → Skip (pass to next responder)
7. Jambonz POSTs the DTMF digit to the adapter's `/webhook/jambonz` endpoint

## Testing with a softphone

1. Install [Linphone](https://www.linphone.org/) or the Jambonz cloud test tool
2. Configure a SIP account pointing at your Jambonz instance
3. Submit a test case with urgency `urgent`
4. The jambonz-adapter health endpoint confirms connectivity: `GET /health`
5. Watch logs: `docker compose logs -f jambonz-adapter`

## DTMF digit reference

| Digit | Action |
|-------|--------|
| 1     | Acknowledge — marks notification as acked in DB |
| 2     | Skip — passes to next on-call responder |
| Any other | Ignored |

## Verifying availability

```bash
curl http://localhost:8004/health
# {"status":"ok","checks":{"jambonz_api":"ok"},"version":"0.1.0"}
```

If `jambonz_api` shows `"error"`, check that `JAMBONZ_API_URL`, `JAMBONZ_API_KEY`, and `JAMBONZ_ACCOUNT_SID` are set correctly and the Jambonz instance is reachable from the Docker network.
