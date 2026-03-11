# EMF Conduct — Notification Router

The router listens for `pg_notify('new_case', ...)` events and dispatches alerts over configured channels: email, Signal, Mattermost, Slack, and telephony (Jambonz).

## Channel adapters

### Email

Always active. Sends an HTML+plain-text email with a one-click ACK link.

Env vars (in `.env`): none beyond `SMTP_PASSWORD` in the root `.env-example`. SMTP host/port/from address live in `config.json`.

### Signal

Active when `signal_mode` in the event config is not `"disabled"`. Requires a running `signal-api` container and a registered Signal number.

### Mattermost Posts API

Sends a richly-formatted post with an interactive **Acknowledge** button. Requires a Mattermost bot with `create_post` permission.

**Local dev setup:**

1. Start the stack with the `local` profile:
   ```
   docker compose -f infra/docker-compose.yml --profile local up -d
   ```
   This starts a `mattermost-team-edition` container on port 8065.

2. Navigate to `http://localhost:8065` and complete the setup wizard. Create an admin account and a team.

3. Create a bot account: **System Console → Integrations → Bot Accounts → Add Bot Account**. Copy the generated token — this is `MATTERMOST_TOKEN`.

4. Find the channel ID: open the target channel, go to **Channel Settings → Edit Channel**, or use the API:
   ```
   curl -H "Authorization: Bearer $MATTERMOST_TOKEN" \
     http://localhost:8065/api/v4/teams/<team-id>/channels/name/<channel-name>
   ```
   Copy the `id` field — this is `MATTERMOST_CHANNEL_ID`.

5. Set env vars in `.env`:
   ```
   MATTERMOST_URL=http://mattermost:8065
   MATTERMOST_CHANNEL_ID=<id from step 4>
   MATTERMOST_TOKEN=<token from step 3>
   MATTERMOST_WEBHOOK_SECRET=<random string>
   ```

6. Set the corresponding `config.json` fields:
   ```json
   "mattermost_url": "http://mattermost:8065",
   "mattermost_channel_id": "<id>"
   ```

7. Expose the action webhook URL to Mattermost. In local dev Mattermost will call back to the router at `http://msg-router:8002/webhook/mattermost/action` (Docker network name). No Caddy proxy needed for this endpoint.

**Fallback:** if only `mattermost_webhook` is set in `config.json` (legacy), the adapter uses a simple incoming webhook instead of Posts API.

### Slack

Set `slack_webhook` in `config.json` to activate. Uses an incoming webhook URL.

## Internal ACK endpoint

`POST /internal/ack/{case_id}` — called by the panel and Jambonz adapter after a user ACKs a case. Protected by `X-Internal-Secret` header (set `ROUTER_INTERNAL_SECRET` in `.env`). Not exposed via Caddy.

## Running tests

```bash
cd apps/router
uv run pytest tests/ -q
uv run python -m mypy src/ --strict
```
