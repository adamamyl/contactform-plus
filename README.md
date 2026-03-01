# EMF Conduct & Accessibility System

A multi-service system for managing conduct and accessibility reports at EMF Festival.

## Services

| Service | Port | Description |
|---------|------|-------------|
| `form` | 8000 | Public incident report form |
| `panel` | 8001 | Conduct team case management panel |
| `msg-router` | 8002 | Notification router (email, Signal, Mattermost) |
| `tts` | 8003 | Text-to-speech synthesis (Piper) |
| `jambonz-adapter` | 8004 | Jambonz telephony escalation adapter |

## Quick Start

```bash
# Generate secrets
python scripts/generate_secrets.py

# Start services (local dev profile)
docker compose -f infra/docker-compose.yml --profile local up -d

# Run interactive installer
python scripts/install.py
```

## Architecture

```
User → Caddy → form (8000)
                  ↓ pg_notify
             msg-router (8002) → email
                               → Signal
                               → Mattermost/Slack
                               → jambonz-adapter (8004) → TTS (8003) → Jambonz
Conduct team → Caddy → panel (8001)
```

## Development

Each service is an independent `uv` project. From any service directory:

```bash
uv sync          # install deps
uv run pytest    # run tests
uv run mypy src/ --strict  # type check
```

Shared library at `shared/` provides: config loading, phase detection, DB session factory, friendly ID generation.

## Configuration

- `.env` — secrets (generate with `scripts/generate_secrets.py`)
- `config.json` — app config (copy from `config.json-example`)

## Security

- PostgreSQL with row-level security and column-level grants
- TLS 1.3 minimum enforced at Caddy
- OIDC auth (UFFD) for the panel
- Time-limited, revocable dispatcher tokens
- OWASP Top 10 test suite in `tests/security/`

## Backup

```bash
python scripts/backup.py --recipient <age-pubkey> [--rsync user@host:/backups/]
# With systemd timer:
python scripts/backup.py --systemd
```
