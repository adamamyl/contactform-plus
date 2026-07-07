# Deploying PR #82 — host env vars

PR #82 moves Traefik host rules out of docker-compose.yml and into `.env` via
`${VAR:-default}` substitution. Each deployment needs the correct hostnames set.

## Per-deployment actions

### Production (vm-conduct01)

Add to `/opt/conduct/.env`:

```bash
FORM_HOST=report.emf.camp
PANEL_HOST=panel.emf.camp
MAP_HOST=conductmap.emf.camp
SWAGGER_HOST=api.conduct.emf.camp
LOCAL_DEV=false
```

`LOCAL_DEV` defaults to `true` if unset (local-dev convenience), so every real
deployment must set it to `false` explicitly — otherwise the form shows all
fields regardless of event dates and the router forces event-time routing
year-round.

Then deploy:

```bash
scripts/prod-update
```

The `prod` branch docker-compose.yml host rule overrides are now redundant.
If the only remaining `prod`-branch difference is `signal-api` port exposure, move
that to a host-local `docker-compose.override.yml` (not committed):

```yaml
# /opt/conduct/infra/docker-compose.override.yml
services:
  signal-api:
    ports:
      - "127.0.0.1:8888:8080"
```

### wolfcraig (*.emf.thisparish.org)

Add to `~/.../emf-conduct/.env` on wolfcraig (actual paths may differ):

```bash
FORM_HOST=report.emf.thisparish.org
PANEL_HOST=panel.emf.thisparish.org
MAP_HOST=map.emf.thisparish.org
SWAGGER_HOST=swagger.emf.thisparish.org
LOCAL_DEV=false
```

Adjust to whichever subdomains are actually configured in DNS.

### Local dev (any machine)

No changes needed — `.env-example` defaults are `*.emf-forms.internal`
(the Caddy local dev hostnames). Copy `.env-example` → `.env` and leave
the `*_HOST` vars at their defaults.

### Any new deployment

Copy `.env-example` → `.env`. The `*_HOST` vars show prod values as reference;
override for the target environment before starting the stack.
