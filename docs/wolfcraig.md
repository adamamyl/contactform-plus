# wolfcraig staging environment

wolfcraig (`wolfcraig.amyl.org.uk`, `176.126.244.197`) is a persistent staging server used to test the EMF Conduct System before events. It runs alongside an existing Ghost blog instance managed by [ghost-docker](https://github.com/adamamyl/ghost-docker).

## Architecture

```
Internet
  └── ghost-docker Caddy (ports 80/443, TLS termination)
        ├── securitysaysyes.com / amyl.org.uk  → Ghost
        └── *.emf.thisparish.org               → EMF Conduct containers
              report.emf.thisparish.org  → emf-form:8000
              panel.emf.thisparish.org   → emf-panel:8001
              auth.emf.thisparish.org    → mock-oidc:8080
              map.emf.thisparish.org     → emf-map:8080
```

The EMF Conduct stack uses the **ghost-docker Caddy** for TLS termination rather than its own Caddy instance (which is disabled via the wolfcraig compose override). All four EMF service containers are attached to the shared `caddy-proxy` Docker network so ghost-docker's Caddy can reach them.

## DNS

All four subdomains are CNAME/A records pointing to `wolfcraig.amyl.org.uk`. Certificates are obtained automatically by Caddy via Let's Encrypt HTTP-01 challenge.

| Hostname | Purpose |
|----------|---------|
| `report.emf.thisparish.org` | Public incident report form |
| `panel.emf.thisparish.org` | Conduct team case management panel |
| `auth.emf.thisparish.org` | Mock OIDC provider (staging only) |
| `map.emf.thisparish.org` | EMF site map (embedded in report form) |
| `swagger.emf.thisparish.org` | OpenAPI docs for all services |
| `mattermost.emf.thisparish.org` | Mattermost team chat (optional notifications) |

## Repositories

| Path | Purpose |
|------|---------|
| `/opt/emf-conduct` | This repo — conduct system code and config |
| `/opt/ghost-docker` | Ghost blog stack — owns Caddy |

## Initial setup (once per machine)

```bash
# 1. Create the shared Docker network
docker network create caddy-proxy

# 2. Create the .env symlink so docker compose finds it for variable interpolation
ln -s ../.env /opt/emf-conduct/infra/.env

# 3. Configure the conduct system (secrets, config.json)
cd /opt/emf-conduct
uv run scripts/generate_secrets.py   # writes .env
cp config.json-example config.json   # then edit domains, events, etc.

# 4. Create the Mattermost database (once only)
docker compose -f infra/docker-compose.yml -f infra/docker-compose.wolfcraig.yml up -d postgres
docker exec infra-postgres-1 psql -U emf_forms_admin -d emf_forms -c "CREATE DATABASE mattermost;"

# 5. Generate the Caddyfile from config.json
uv run scripts/generate_caddyfile.py

# 6. Start the conduct stack
docker compose -f infra/docker-compose.yml -f infra/docker-compose.wolfcraig.yml up -d

# 7. Restart ghost-docker's Caddy to pick up the new vhosts
docker compose -f /opt/ghost-docker/compose.yml \
               -f /opt/ghost-docker/compose.wolfcraig.yml restart caddy
```

> **Note**: The `infra/.env` symlink itself is committed to the repo (`infra/.env -> ../.env`). The real `.env` is gitignored. The symlink is needed because `docker compose -f infra/docker-compose.yml` sets the project directory to `infra/`, so compose looks for `.env` there rather than the repo root.

## Day-to-day operations

### Start / stop the conduct stack

```bash
cd /opt/emf-conduct

# Start (or recreate changed services)
docker compose -f infra/docker-compose.yml -f infra/docker-compose.wolfcraig.yml up -d

# Stop
docker compose -f infra/docker-compose.yml -f infra/docker-compose.wolfcraig.yml down
```

### After changing config.json domains

```bash
uv run scripts/generate_caddyfile.py
docker compose -f /opt/ghost-docker/compose.yml \
               -f /opt/ghost-docker/compose.wolfcraig.yml restart caddy
```

### After changing Caddyfile headers (CSP, etc.)

Caddy `reload` doesn't see bind-mount changes — a **restart** is required:

```bash
docker compose -f /opt/ghost-docker/compose.yml \
               -f /opt/ghost-docker/compose.wolfcraig.yml restart caddy
```

### Rebuild and restart a single service

```bash
docker compose -f infra/docker-compose.yml -f infra/docker-compose.wolfcraig.yml \
  up -d --build --force-recreate panel
```

## OIDC (authentication)

wolfcraig uses `mock-oidc` (navikt mock-oauth2-server) instead of the production UFFD. It is exposed at `https://auth.emf.thisparish.org`.

To log in to the panel:

1. Browse to `https://panel.emf.thisparish.org/login`
2. You will be redirected to the mock-oidc login screen
3. Enter a username (anything) and set the claims to:
   ```json
   {"groups": ["team_conduct"]}
   ```
4. Submit — you will be redirected back to the panel as an authenticated user

The OIDC issuer is configured in `docker-compose.wolfcraig.yml`:
```
OIDC_ISSUER: https://auth.emf.thisparish.org/default
```

## Key differences from production

| | wolfcraig (staging) | Production |
|-|---------------------|------------|
| OIDC | mock-oidc at `auth.emf.thisparish.org` | UFFD at `auth.emfcamp.org` |
| Caddy | ghost-docker's Caddy (shared) | Dedicated Caddy container |
| Domains | `*.emf.thisparish.org` | `*.emfcamp.org` |
| Emails | `hello@wolfmail.amyl.org.uk` | `conduct@emfcamp.org` |
| Signal | Dummy sender (`+440000000000`) | Real number |

## Troubleshooting

### Panel 500 on login

Check panel logs: `docker logs emf-panel --tail 50`

Common causes:
- **DNS not propagated**: OIDC issuer hostname not resolving inside container. Check with `docker exec emf-panel python3 -c "import socket; print(socket.getaddrinfo('auth.emf.thisparish.org', 443))"`
- **Wrong DB password**: `DATABASE_URL` uses `localdev` default — ensure `infra/.env` symlink exists and restart the panel container
- **mock-oidc not on caddy-proxy network**: verify with `docker inspect infra-mock-oidc-1 --format '{{range $n,$v := .NetworkSettings.Networks}}{{$n}} {{end}}'`

### Map not showing in report form

1. Check browser console for CSP violations
2. Regenerate the Caddyfile and restart Caddy: `uv run scripts/generate_caddyfile.py && docker compose ... restart caddy`
3. Check `map.emf.thisparish.org` is reachable: `curl -sI https://map.emf.thisparish.org/`
4. Verify `emf-map` container is on `caddy-proxy` network

### Caddy not picking up Caddyfile changes

`caddy reload` does not re-read bind-mounted files. Always use `restart`:
```bash
docker compose -f /opt/ghost-docker/compose.yml \
               -f /opt/ghost-docker/compose.wolfcraig.yml restart caddy
```
