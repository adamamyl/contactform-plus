# Signal Setup

The `signal-api` container runs [signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api) in native mode. It delivers case notifications to a Signal group and polls for 🤙 emoji reactions to process ACKs.

## Prerequisites

A dedicated Signal-capable phone number (SIM or VoIP). The number must not already be registered as a primary Signal account on a device you need to keep — linking it as a linked device on an existing account is the simpler path.

## 1. Configure `.env`

```
SIGNAL_API_URL=http://signal-api:8080
SIGNAL_SENDER=+447311175546    # E.164 format
```

`SIGNAL_API_URL` uses the container name — the router reaches signal-api over the internal Docker network.

## 2. Link the account

Start the signal-api container, then use the QR link endpoint:

```bash
docker compose -f infra/docker-compose.yml up -d signal-api

# The endpoint returns a URL — open it in a browser to display the QR code
curl "http://localhost:8080/v1/qrcodelink?device_name=emf-conduct"
```

On the primary Signal device: **Linked Devices → Link New Device** → scan the QR code.

Once linked, verify the account is visible to the REST API:

```bash
docker compose -f infra/docker-compose.yml exec signal-api \
  curl -s http://localhost:8080/v1/accounts
# Should return ["+447311175546"]
```

> If the account shows up under `signal-cli listDevices` but `/v1/accounts` returns `[]`, the volume is mounted at the wrong path — see [Troubleshooting](#troubleshooting) below.

## 3. Find the group ID

Create the Signal group on your phone first and add the conduct team. Then fetch the group list via the REST API:

```bash
docker compose -f infra/docker-compose.yml exec signal-api \
  curl -s http://localhost:8080/v1/groups/+447311175546
```

Each group has two IDs:

| Field | Example | Use |
|-------|---------|-----|
| `id` | `group.cy9mdEF...` | **This is what goes in config.json** (strip the `group.` prefix) |
| `internal_id` | `s/ftAD6w...` | signal-cli internal format — do not use this |

The `id` field is the base64 encoding of the `internal_id` string. The router constructs the recipient as `group.<signal_group_id>` when sending, so config.json must store the API-layer ID (without the `group.` prefix).

## 4. Configure `config.json`

```json
{
  "events": [
    {
      "name": "EMF 2026",
      "start_date": "2026-07-12",
      "end_date": "2026-07-20",
      "signal_group_id": "cy9mdEFENncyV0w5VHFBNll2TUV3MHA0OWY3MVdhYWxvMTBKbG9RdWZ5OD0=",
      "signal_mode": "always"
    }
  ]
}
```

### `signal_mode` options

| Value | Behaviour |
|-------|-----------|
| `always` | Send to Signal for every case |
| `fallback_only` | Send to Signal only if telephony (Jambonz) is unavailable (default) |
| `high_priority_and_fallback` | Send to Signal for `high`/`urgent` cases, or when telephony is unavailable |

Signal notifications are only dispatched during event time (between `start_date` and `end_date`). Off-event, only email is sent.

## 5. Test the send

Verify end-to-end by posting directly to the signal-api from inside the container:

```bash
docker compose -f infra/docker-compose.yml exec signal-api \
  curl -s -X POST http://localhost:8080/v2/send \
    -H 'Content-Type: application/json' \
    -d '{
      "message": "EMF Conduct router test — please ignore",
      "number": "+447311175546",
      "recipients": ["group.<your-group-id-from-config>"]
    }'
# Returns {"timestamp":"..."} on success
```

Then submit a test case via the report form and confirm the notification arrives in the Signal group.

## ACK via emoji reaction

Team members acknowledge a case by reacting to the Signal notification with 🤙. The router polls the API every 10 seconds for new reactions and marks the case as acknowledged across all channels.

> Signal does not forward reactions to linked devices, so the router uses polling rather than a webhook.

## Troubleshooting

### `/v1/accounts` returns `[]` but `listDevices` works

The signal-cli-rest-api process runs as the `signal-api` user and looks for its data at `/home/.local/share/signal-cli`. If the Docker volume is mounted at `/root/.local/share/signal-cli` instead, the REST API cannot see the registered account even though the CLI binary can.

Check which path the process is using:

```bash
docker compose -f infra/docker-compose.yml exec signal-api \
  ps aux | grep signal-cli-rest-api
# Look for the -signal-cli-config= argument
```

The `docker-compose.yml` volume entry must match:

```yaml
signal-api:
  volumes:
    - signal_data:/home/.local/share/signal-cli   # correct
    # - signal_data:/root/.local/share/signal-cli  # wrong — REST API won't find the account
```

After fixing the mount path, recreate the container:

```bash
docker compose -f infra/docker-compose.yml up -d signal-api
```

The data in the volume is unaffected — only the mount point changes.

### `Invalid identifier group.<id>`

The recipient format `group.<id>` in the `/v2/send` payload must use the `id` field from `/v1/groups`, not the `internal_id`. The `internal_id` has the form `s/ftAD6w...` or `BcyswaI7...` and will be rejected.

### `User +44... is not registered`

The REST API cannot find the account. See the `/v1/accounts` troubleshooting above.
