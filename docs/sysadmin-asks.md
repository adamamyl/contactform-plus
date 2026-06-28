# Sysadmin asks

Requests for external sysadmin action to support the EMF Conduct system on `vm-conduct01.emf.camp`.

---

## DNS records

Please create A records pointing to `vm-conduct01.emf.camp` for:

- `report.emf.camp`
- `panel.emf.camp`
- `api.conduct.emf.camp`
- `conductmap.emf.camp`

---

## OIDC client on identity.emfcamp.org

Please create an OIDC client with the following settings:

| Setting | Value |
|---|---|
| Client name | `emf-conduct-panel` |
| Client type | Confidential (server-side, has a secret) |
| Grant type | Authorization Code |
| Redirect URI | `https://panel.emf.camp/auth/callback` |
| Scopes | `openid`, `email`, `profile`, `groups` |
| Groups claim | `groups` must be present in the ID token as a list of strings |

Users must be in the group `team_conduct` on the IdP to be granted access to the panel.

What we need back (to go in `.env` on the server):

- `OIDC_ISSUER` — issuer URL
- `OIDC_CLIENT_ID` — client ID
- `OIDC_CLIENT_SECRET` — client secret
- `JWKS_URI` — only needed if not auto-discoverable from `{issuer}/.well-known/openid-configuration`

---

## Mattermost

### 1. Bot account

Create a bot account (suggested name: `conduct-bot`) and generate a bot access token.

### 2. Channel

Create a private channel for conduct notifications (suggested name: `conduct-alerts`), add the bot to it, and provide the channel ID (the internal ID from Channel Info, not the display name).

### 3. Allow inbound callbacks from our router

In System Console → Environment → Developer, add `msg-router` to **Allowed Untrusted Internal Connections**. This allows Mattermost to POST ACK button callbacks back to our notification router. Without it, interactive buttons will silently fail.

What we need back (to go in `.env` / `config.json` on the server):

- Bot access token → `.env` as `MATTERMOST_TOKEN`
- Channel ID → `config.json` as `mattermost_channel_id`
- Mattermost base URL → `config.json` as `mattermost_url`

---

## After merging PR #82 — host env vars (ops action required per deployment)

Traefik host rules are now read from `.env`. Each deployment needs the following set.

### Production (vm-conduct01)

Add to `/opt/conduct/.env`:

```bash
FORM_HOST=report.emf.camp
PANEL_HOST=panel.emf.camp
MAP_HOST=conductmap.emf.camp
SWAGGER_HOST=api.conduct.emf.camp
```

Confirm these are already set (should be from initial setup):

```bash
OIDC_ISSUER=https://identity.emfcamp.org/default
JWKS_URI=https://identity.emfcamp.org/default/jwks
PANEL_BASE_URL=https://panel.emf.camp
```

Then deploy:

```bash
scripts/prod-update
```

The `prod` branch docker-compose.yml host rule overrides are now redundant.
The `prod` branch can be pruned — or move the `signal-api` port exposure to a
host-local `docker-compose.override.yml` (not committed to the repo):

```yaml
# /opt/conduct/infra/docker-compose.override.yml
services:
  signal-api:
    ports:
      - "127.0.0.1:8888:8080"
```

### Local dev (wolfcraig / other dev machines)

No changes needed — defaults are `*.emf-forms.internal`.

### Any new deployment

Copy `.env-example` → `.env`. The `*_HOST` vars are shown with their prod values
as a reference; override for your environment.
