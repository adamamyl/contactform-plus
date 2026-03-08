# EMF Conduct — Jambonz Telephony Adapter

This service bridges the EMF Conduct notification router with Jambonz for outbound voice calls. When a high-priority case arrives, the router calls this adapter to speak the alert via text-to-speech and dial the on-call phone number(s).

## Self-hosted vs cloud Jambonz

**Cloud (jambonz.cloud):** sign up at https://jambonz.cloud. The API URL is `https://api.jambonz.cloud`. Your account SID and API key are in the dashboard.

**Self-hosted:** deploy using the official Docker Compose or Kubernetes manifests from https://github.com/jambonz/jambonz-infrastructure. The API URL is `http://<your-host>:3000`.

## Environment variables

Set these in `.env` at the repo root:

| Variable | Description |
|---|---|
| `JAMBONZ_API_URL` | Base URL for the Jambonz REST API (e.g. `https://api.jambonz.cloud`) |
| `JAMBONZ_API_KEY` | API key from the Jambonz dashboard |
| `JAMBONZ_ACCOUNT_SID` | Account SID from the Jambonz dashboard |
| `JAMBONZ_APPLICATION_SID` | Application SID — create one in the Jambonz dashboard pointing the webhook at this service's `/webhook/jambonz` endpoint |
| `JAMBONZ_FROM_NUMBER` | Caller ID in E.164 format (e.g. `+441234567890`) |
| `TTS_SERVICE_URL` | URL of the TTS service (default `http://tts:8003`) |
| `ROUTER_INTERNAL_URL` | URL of the notification router for DTMF ACK callbacks (default `http://msg-router:8002`) |
| `ROUTER_INTERNAL_SECRET` | Shared secret for the router internal endpoint (`X-Internal-Secret` header) |

## DTMF webhook

The Jambonz application must be configured to POST DTMF events to:

```
https://<your-domain>/webhook/jambonz
```

with a JSON body containing `call_sid`, `digit`, and `case_id`.

- **Digit `1`:** acknowledge the case — posts to the router internal ACK endpoint
- **Digit `2`:** pass to next responder in escalation sequence

### ngrok for cloud Jambonz

Cloud Jambonz needs a publicly-accessible webhook URL. In development, expose the service with ngrok:

```bash
ngrok http 8004
```

Copy the HTTPS URL (e.g. `https://abc123.ngrok.io`) and set it as the webhook URL in your Jambonz application. Update it whenever the ngrok session restarts.

Self-hosted Jambonz inside the same Docker network can reach this service directly at `http://jambonz-adapter:8004`.

## Running tests

```bash
cd apps/jambonz
uv run pytest tests/ -q
uv run python -m mypy src/ --strict
```
