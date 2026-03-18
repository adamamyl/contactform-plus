# Mattermost Setup

Mattermost receives notifications when a new case is submitted and allows team members to acknowledge cases via an in-channel button.

## Overview

**Services involved:**

| Service | Container | Port |
|---------|-----------|------|
| Notification router | `msg-router` | 8002 |
| Mattermost | `mattermost` | 8065 |
| Postgres | `postgres` | 5432 |

The router posts to Mattermost via the Posts API (not incoming webhooks), so a bot account with a personal access token is required.

---

## Step 1 — First-time Mattermost database setup

Mattermost needs its own database. Before starting the stack for the first time:

```bash
docker compose -f infra/docker-compose.yml up -d postgres
docker compose -f infra/docker-compose.yml exec postgres psql -U postgres -c "CREATE DATABASE mattermost;"
```

Then bring up the full stack.

---

## Step 2 — Initial Mattermost admin setup

1. Browse to `http://localhost:8065` and complete the setup wizard.
2. Create an admin account. Note the credentials somewhere safe.
3. Create a team (e.g. `EMF Conduct`). Note the team name — you will need it when inviting the bot.

---

## Step 3 — Enable personal access tokens

Personal access tokens must be enabled before a bot can be created.

1. Go to **System Console → Integrations → Integration Management**.
2. Enable **Personal Access Tokens**.
3. Save.

---

## Step 4 — Create a bot account

1. Go to **System Console → Integrations → Bot Accounts**.
2. Click **Add Bot Account**.
3. Set a username (e.g. `conduct-bot`) and display name.
4. Under **Role**, choose `Member`.
5. Click **Create Bot Account**.
6. Copy the **Access Token** shown on the confirmation screen — this is shown only once. This is your `MATTERMOST_TOKEN`.

If you need to regenerate the token later: **System Console → Integrations → Bot Accounts → (bot) → Create New Token**.

---

## Step 5 — Add the bot to a team

Bots cannot join teams on their own. Add the bot via:

**System Console → User Management → Teams → (your team) → Add Members**

Search for the bot username and add it.

---

## Step 6 — Create a channel and add the bot

1. In the Mattermost UI, create a private or public channel (e.g. `#conduct-alerts`).
2. Add the bot as a member of the channel.

Alternatively via admin console:

**System Console → User Management → Channels → (channel) → Add Members**

To find the channel ID (needed for `config.json`):

**System Console → User Management → Channels → (channel)**

The URL will contain the channel ID, or it is shown in the channel details. It looks like `sxks77j7uiyimccoaiy4nf5qsc`.

---

## Step 7 — Allow the bot to reach the router (outbound connections)

Mattermost blocks outbound POSTs to internal IPs by default. The ACK button posts back to the router, so you must whitelist the router container.

In `infra/docker-compose.yml`, the `mattermost` service should have:

```yaml
environment:
  MM_SERVICESETTINGS_ALLOWEDUNTRUSTEDINTERNALCONNECTIONS: msg-router
```

This is already set in the provided `docker-compose.yml`. If the ACK button silently fails, check this setting first.

---

## Step 8 — Configure `.env` and `config.json`

In `.env`:

```
MATTERMOST_TOKEN=<bot-personal-access-token>
MATTERMOST_WEBHOOK_SECRET=<random-string-used-to-verify-button-callbacks>
```

In `config.json`:

```json
"mattermost_url": "http://mattermost:8065",
"mattermost_channel_id": "<channel-id>",
"mattermost_webhook": null
```

The `mattermost_url` uses the internal Docker hostname. Do not use `localhost` — the router runs in a separate container.

---

## Step 9 — Apply changes and restart the router

After editing `.env` or `config.json`, the router container must be recreated. On macOS, bind-mounted files lose their inode after editing, so a plain `restart` is not sufficient — use `--force-recreate`:

```bash
docker compose -f infra/docker-compose.yml up -d --force-recreate msg-router
```

---

## Verifying the token

Test the bot token directly against the Mattermost API:

```bash
curl -H "Authorization: Bearer <token>" http://localhost:8065/api/v4/users/me
```

A JSON response with the bot's user object means the token is valid. A `401` means the token is wrong or has been revoked.

To check what token the running router container actually has (useful when diagnosing stale-container issues):

```bash
docker compose -f infra/docker-compose.yml exec msg-router printenv MATTERMOST_TOKEN
```

Compare this to `grep MATTERMOST_TOKEN .env`. If they differ, the container is stale — run `--force-recreate` as above.

---

## Troubleshooting

### Logs to watch

Router logs (Mattermost send attempts, 401s, retries):

```bash
docker compose -f infra/docker-compose.yml logs -f msg-router
```

Mattermost logs (auth errors, plugin issues):

```bash
docker compose -f infra/docker-compose.yml logs -f mattermost
```

Filter for just Mattermost-related lines in the router:

```bash
docker compose -f infra/docker-compose.yml logs msg-router 2>&1 | grep -i mattermost
```

### Diagnostic API calls

Verify the token is accepted by Mattermost:

```bash
curl -H "Authorization: Bearer <token>" http://localhost:8065/api/v4/users/me
```

Check the bot can see the target channel:

```bash
curl -H "Authorization: Bearer <token>" http://localhost:8065/api/v4/channels/<channel-id>
```

Check the bot is a member of the channel:

```bash
curl -H "Authorization: Bearer <token>" http://localhost:8065/api/v4/channels/<channel-id>/members/<bot-user-id>
```

Post a test message as the bot (mimics what the router does):

```bash
curl -X POST \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"channel_id":"<channel-id>","message":"test from router bot"}' \
  http://localhost:8065/api/v4/posts
```

Check what token the running router container actually has (to diagnose stale-container issues):

```bash
docker compose -f infra/docker-compose.yml exec msg-router printenv MATTERMOST_TOKEN
```

Compare this to `grep MATTERMOST_TOKEN .env`. If they differ, the container is stale — run `--force-recreate` as above.

### Common failure patterns

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `401` in router logs for Mattermost | Stale token in container | `--force-recreate msg-router` |
| Signal works, Mattermost silent | Token mismatch (container vs `.env`) | `printenv` vs `grep .env` |
| ACK button does nothing | Router not reachable from Mattermost | Check `MM_SERVICESETTINGS_ALLOWEDUNTRUSTEDINTERNALCONNECTIONS` |
| Bot not in channel | Bot not added to team/channel | System Console → User Management → Teams |
| `403` on ACK webhook | Wrong `MATTERMOST_WEBHOOK_SECRET` | Ensure `.env` secret matches and container is recreated |
| Notifications missing after `.env` edit | macOS inode issue on bind mount | Always use `--force-recreate`, never plain `restart` |
