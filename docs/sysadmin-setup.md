# Sysadmin Setup Guide

This document covers deploying the EMF Conduct System on a fresh server, including all third-party service sign-ups.

## Table of contents

1. [Prerequisites](#1-prerequisites)
2. [Third-party service sign-ups](#2-third-party-service-sign-ups)
3. [Server setup](#3-server-setup)
4. [Clone and configure](#4-clone-and-configure)
5. [Generate secrets](#5-generate-secrets)
6. [Configure config.json](#6-configure-configjson)
7. [Signal device linking](#7-signal-device-linking)
8. [First start](#8-first-start)
9. [DNS and TLS](#9-dns-and-tls)
10. [OIDC integration](#10-oidc-integration)
11. [Post-deployment checks](#11-post-deployment-checks)
12. [Monitoring](#12-monitoring)
13. [Backups](#13-backups)

---

## 1. Prerequisites

### Server

- Linux (Debian 12 / Ubuntu 24.04 LTS recommended)
- 2 vCPU, 4 GB RAM minimum (8 GB recommended for full stack with Mattermost)
- 20 GB disk (more if storing TTS audio or attachments)
- Docker ≥ 26 and Docker Compose ≥ 2.24
- Ports 80 and 443 open inbound (Caddy handles TLS termination)

### DNS

All hostnames must resolve to the server's public IP before Caddy can obtain TLS certificates. Caddy uses Let's Encrypt by default. Required records:

| Hostname | Notes |
|----------|-------|
| `report.emfcamp.org` | Public incident report form |
| `panel.emfcamp.org` | Conduct team case management |

If running the EMF site map service, also add:

| Hostname | Notes |
|----------|-------|
| `map.emfcamp.org` | (or wherever the map is hosted) |

### Domain email

You will need a verified sending domain for Resend (see below). Typically `emfcamp.org`.

---

## 2. Third-party service sign-ups

### 2a. Resend (transactional email) — required

All incident notifications and ACK confirmations are sent via [Resend](https://resend.com).

1. Sign up at https://resend.com
2. **Add a domain**: Resend → Domains → Add → enter `emfcamp.org` (or your sending domain)
3. Add the DNS records Resend shows you (SPF, DKIM, DMARC)
4. **Create an API key**: Resend → API Keys → Create → name it `emf-conduct`, scope: *Sending access*
5. Copy the key — it starts with `re_`. This goes in `.env` as `RESEND_API_KEY`

> **SMTP fallback**: If you prefer SMTP (e.g. a self-hosted mail server), you can skip Resend and set `smtp.*` values in `config.json` plus `SMTP_PASSWORD` in `.env`. Resend takes priority if `RESEND_API_KEY` is set.

### 2b. Jambonz (telephony) — optional but recommended for urgent cases

Jambonz handles outbound phone calls when a case is marked `urgent` or `high`. See also: [docs/jambonz-setup.md](jambonz-setup.md).

**Option A — jambonz.cloud (for testing / small events)**

1. Sign up at https://jambonz.cloud
2. **Account SID**: Accounts → your account → note the SID
3. **Application**: Applications → New Application
   - Calling Webhook: `https://panel.emfcamp.org/webhook/jambonz/call`
   - Leave speech/recording settings blank
   - Note the **Application SID**
4. **API Key**: API Keys → Create → note the key
5. **Phone number**: Phone Numbers → provision a DID (outbound-only, any number is fine)
   - Note the number in E.164 format e.g. `+441234567890`

Set in `.env`:
```
JAMBONZ_API_URL=https://api.jambonz.cloud
JAMBONZ_API_KEY=<key>
JAMBONZ_ACCOUNT_SID=<sid>
JAMBONZ_APPLICATION_SID=<app-sid>
JAMBONZ_FROM_NUMBER=+441234567890
```

**Option B — self-hosted Jambonz (for production events)**

Speak to the EMF infra team. Set `JAMBONZ_API_URL` to the self-hosted instance endpoint.

**Skipping telephony**: Leave all `JAMBONZ_*` vars unset. The telephony adapter will start but remain inactive.

### 2c. Signal (messaging) — optional

Signal delivers case notifications and supports emoji-reaction ACKs. Requires a dedicated phone number (SIM or VoIP).

No sign-up needed beyond having a Signal-capable phone number. Setup is done post-deployment via QR code — see [§7 Signal device linking](#7-signal-device-linking).

### 2d. Mattermost (team chat) — optional

Delivers richly-formatted case notifications with an Acknowledge button.

**Option A — self-hosted (included in `--profile local`)**

Starts automatically with `docker compose --profile local`. Complete the setup wizard at `http://<host>:8065` on first boot.

**Option B — existing Mattermost instance**

Set `MATTERMOST_URL` in `.env` and configure the bot token — see [apps/router/README.md](../apps/router/README.md#mattermost-posts-api).

**Skipping Mattermost**: Leave `MATTERMOST_*` vars unset. Notifications fall back to email and Signal.

### 2e. OIDC provider — required for panel access

The conduct panel authenticates users via OIDC. For EMF events, this is UFFD at `auth.emfcamp.org`.

1. Register a client with the OIDC provider:
   - **Redirect URI**: `https://panel.emfcamp.org/auth/callback`
   - **Scope**: `openid profile email groups` (needs a `groups` claim to identify `team_conduct` members)
   - Note the **Client ID** and **Client Secret**
2. Set in `.env`:
   ```
   OIDC_ISSUER=https://auth.emfcamp.org
   OIDC_CLIENT_ID=emf-forms
   OIDC_CLIENT_SECRET=<secret>
   ```

Users must be members of the `team_conduct` group in the OIDC provider to access the panel.

---

## 3. Server setup

```bash
# Install Docker (official method)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# Verify
docker compose version
```

Create a service user (recommended):

```bash
sudo useradd -m -s /bin/bash conduct
sudo usermod -aG docker conduct
sudo -u conduct -i
```

---

## 4. Clone and configure

```bash
git clone https://github.com/adamamyl/contactform-plus.git /opt/emf-conduct
cd /opt/emf-conduct
```

---

## 5. Generate secrets

The `generate_secrets.py` script reads `.env-example`, generates cryptographically random values for all `changeme` placeholders, and writes `.env` (mode 600).

```bash
uv run scripts/generate_secrets.py
```

If you don't have `uv` installed:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv run scripts/generate_secrets.py
```

Then open `.env` and fill in the values that can't be auto-generated:

| Variable | Where to get it |
|----------|----------------|
| `OIDC_CLIENT_SECRET` | OIDC provider registration |
| `RESEND_API_KEY` | Resend dashboard (§2a) |
| `SMTP_PASSWORD` | Your SMTP provider (if not using Resend) |
| `SIGNAL_SENDER` | The phone number registered with Signal (E.164) |
| `JAMBONZ_API_KEY` | Jambonz dashboard (§2b) |
| `JAMBONZ_ACCOUNT_SID` | Jambonz dashboard (§2b) |
| `JAMBONZ_APPLICATION_SID` | Jambonz dashboard (§2b) |
| `JAMBONZ_FROM_NUMBER` | Jambonz phone number (§2b) |
| `MATTERMOST_TOKEN` | Mattermost bot account (§2d) |
| `MATTERMOST_CHANNEL_ID` | Mattermost channel API (§2d) |

---

## 6. Configure config.json

Copy the example and edit:

```bash
cp config.json-example config.json
```

Key fields to update:

```json
{
  "events": [
    {
      "name": "EMF 2026",
      "start_date": "2026-07-12",
      "end_date": "2026-07-20",
      "signal_group_id": null,
      "dispatcher_emails": ["dispatcher@emfcamp.org"]
    }
  ],
  "conduct_emails": ["conduct@emfcamp.org"],
  "smtp": {
    "host": "smtp.resend.com",
    "port": 587,
    "from_addr": "conduct@emfcamp.org",
    "use_tls": true,
    "username": "resend"
  },
  "panel_base_url": "https://panel.emfcamp.org"
}
```

> `smtp_password` is never stored in `config.json` — it lives in `.env` as `SMTP_PASSWORD`.

**Signal group ID** (if using Signal):

The `signal_group_id` must be the base64 group ID from the Signal CLI API (not the `internal_id`). Retrieve it after linking the Signal device (§7):

```bash
curl http://localhost:8080/v1/groups/+<SIGNAL_SENDER>
# Look for the "id" field (base64 string), not "internal_id"
```

---

## 7. Signal device linking

The `signal-api` container runs Signal CLI in native mode and needs to be registered as a secondary device on a Signal account.

```bash
# Start only the signal-api service first
docker compose -f infra/docker-compose.yml up -d signal-api

# Get the QR code link (open in browser while logged in to the primary device)
curl "http://localhost:8080/v1/qrcodelink?device_name=emf-conduct"
# Returns a URL — open it in a browser; a QR code will display
```

On the primary Signal device (phone or desktop): **Linked Devices → Link New Device** → scan the QR code.

The container must stay running during the scan. Once linked, the number is registered and the device persists in the `signal_data` volume.

> **Note**: Signal reactions (the 🤙 emoji ACK) are not forwarded to linked devices. The router polls the Signal REST API every 10 seconds instead.

---

## 8. First start

```bash
cd /opt/emf-conduct

# Core stack (form, panel, router, tts, jambonz, postgres, caddy, redis, signal-api)
docker compose -f infra/docker-compose.yml up -d

# Check all services are healthy
docker compose -f infra/docker-compose.yml ps
docker compose -f infra/docker-compose.yml logs --tail=50
```

The database schema is applied automatically on first start via `infra/postgres/00_init.sh`.

### Health checks

```bash
curl https://report.emfcamp.org/health
curl https://panel.emfcamp.org/health
curl https://panel.emfcamp.org/router/health   # proxied through panel → router
```

All should return `{"status": "ok", ...}`.

---

## 9. DNS and TLS

Caddy obtains Let's Encrypt certificates automatically when the DNS records are in place and ports 80/443 are reachable. Check the Caddy logs if certificates aren't issued:

```bash
docker compose -f infra/docker-compose.yml logs caddy
```

The production `Caddyfile.prod` is used by default. It expects the environment variable `$PROJECT_NAME` to match the Docker Compose project name (default: `emf-conduct`).

**If using custom TLS certificates** (e.g. internal CA): mount them into the Caddy container and adjust `Caddyfile.prod` to reference them using the `tls /path/to/cert /path/to/key` directive.

---

## 10. OIDC integration

The panel redirects unauthenticated users to the OIDC provider. On successful login, the provider must return a `groups` claim containing `team_conduct` for access to be granted.

**UFFD (auth.emfcamp.org)**: Group membership is managed in the UFFD admin interface. Add users to the `team_conduct` group.

**Testing OIDC locally**: Use `--profile local` to start `mock-oidc`. At the login screen, enter:
```json
{"groups": ["team_conduct"]}
```
in the claims field to simulate a conduct team member.

---

## 11. Post-deployment checks

Work through this list after the first deployment:

- [ ] `https://report.emfcamp.org` loads the report form
- [ ] Submit a test report; confirm it appears in the panel at `https://panel.emfcamp.org`
- [ ] Panel login works via OIDC; non-`team_conduct` users are rejected
- [ ] Notification email arrives (check spam folder; check Resend dashboard for delivery status)
- [ ] ACK link in email marks the case as acknowledged in the panel
- [ ] Signal message arrives in the configured group (if `signal_group_id` is set)
- [ ] Mattermost Acknowledge button works (if Mattermost is configured)
- [ ] Jambonz call is placed for an `urgent` test case (if Jambonz is configured); pressing 1 ACKs the case
- [ ] Dispatcher page loads at `https://panel.emfcamp.org/dispatcher`
- [ ] `docker compose ps` shows all containers as healthy (no restart loops)

---

## 12. Monitoring

Start the monitoring stack:

```bash
docker compose -f infra/docker-compose.yml --profile monitoring up -d
```

- **Prometheus**: `http://<host>:9090` — scrapes all services at `/metrics` every 15 seconds
- **Grafana**: `http://<host>:3000` — pre-provisioned dashboards for form, panel, router, and TTS

Set `GRAFANA_ADMIN_PASSWORD` in `.env` before starting.

---

## 13. Backups

The `backup.py` script dumps the database and encrypts it with `age`:

```bash
# One-off backup (prints encrypted output to stdout)
uv run scripts/backup.py --recipient <age-public-key>

# Example: backup to file
uv run scripts/backup.py --recipient age1xyz... > backup-$(date +%Y%m%d).age
```

The backup user (`BACKUP_DB_PASSWORD` in `.env`) has read-only access to all tables.

**What is backed up**: the entire `emf_forms` database schema — all cases, notifications, history, and idempotency tokens.

**Restore**:

```bash
age -d -i <private-key-file> backup-20260712.age | \
  docker exec -i emf-conduct-postgres-1 psql -U emf_forms_admin emf_forms
```

Schedule regular backups via cron or a systemd timer.
