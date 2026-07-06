# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Generate infra/caddy/Caddyfile.wolfcraig from the domains in config.json.

Reads config.domains (report, panel, map, auth) and writes a Caddyfile with
correct Content-Security-Policy headers for each vhost.  Run whenever you
change the domains in config.json, then restart Caddy to apply.

Usage:
    uv run scripts/generate_caddyfile.py
    uv run scripts/generate_caddyfile.py --output -          # stdout
    uv run scripts/generate_caddyfile.py --config /path/to/config.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.resolve()
DEFAULT_OUTPUT = REPO_ROOT / "infra" / "caddy" / "Caddyfile.wolfcraig"

# External tile/data services the map app needs to reach — not deployment-specific.
_MAP_EXTERNAL_CONNECT = " ".join(
    [
        "https://map.emfcamp.org",
        "https://www.emfcamp.org",
        "https://tracking.tfemf.uk",
        "https://geojson.thinkl33t.co.uk",
    ]
)
_MAP_EXTERNAL_FONT = "https://map.emfcamp.org"


def load_domains(config_path: Path) -> dict[str, str]:
    cfg = json.loads(config_path.read_text())
    raw: dict[str, object] = cfg.get("domains") or {}
    for required in ("report", "panel"):
        if not raw.get(required):
            print(f"ERROR: config.json missing domains.{required}", file=sys.stderr)
            sys.exit(1)
    return {k: str(v) for k, v in raw.items() if v}


def _hsts() -> str:
    return 'Strict-Transport-Security "max-age=63072000; includeSubDomains; preload"'


def _report_block(report: str, map_domain: str | None) -> str:
    # emf-map is now a same-origin web component (script-src/worker-src/connect-src),
    # not a sandboxed iframe — only wire these up when the map is actually configured.
    map_script = f" https://{map_domain}" if map_domain else ""
    worker_src = " worker-src blob:;" if map_domain else ""
    map_font = f" {_MAP_EXTERNAL_FONT}" if map_domain else ""
    map_connect = f" {_MAP_EXTERNAL_CONNECT}" if map_domain else ""
    csp = (
        f"default-src 'self'; script-src 'self'{map_script} blob:;{worker_src} "
        "style-src 'self' 'unsafe-inline'; "
        f"img-src 'self' blob: data: https://*.tile.openstreetmap.org; font-src 'self'{map_font}; "
        f"connect-src 'self'{map_connect}"
    )
    return f"""{report} {{
\theader {{
\t\t{_hsts()}
\t\tX-Content-Type-Options "nosniff"
\t\tX-Frame-Options "DENY"
\t\tContent-Security-Policy "{csp}"
\t\tReferrer-Policy "strict-origin-when-cross-origin"
\t\tPermissions-Policy "geolocation=(self), camera=(), microphone=()"
\t\t-Server
\t}}
\tencode gzip
\treverse_proxy emf-form:8000
}}"""


def _panel_block(panel: str, map_domain: str | None) -> str:
    map_script = f" https://{map_domain}" if map_domain else ""
    worker_src = " worker-src blob:;" if map_domain else ""
    map_font = f" {_MAP_EXTERNAL_FONT}" if map_domain else ""
    map_connect = f" {_MAP_EXTERNAL_CONNECT}" if map_domain else ""
    csp = (
        f"default-src 'self'; script-src 'self'{map_script} blob:;{worker_src} "
        "style-src 'self' 'unsafe-inline'; "
        f"img-src 'self' blob: data:; font-src 'self'{map_font}; "
        f"connect-src 'self'{map_connect}"
    )
    return f"""{panel} {{
\theader {{
\t\t{_hsts()}
\t\tX-Content-Type-Options "nosniff"
\t\tX-Frame-Options "DENY"
\t\tContent-Security-Policy "{csp}"
\t\tReferrer-Policy "strict-origin-when-cross-origin"
\t\tPermissions-Policy "geolocation=(self), camera=(), microphone=()"
\t\t-Server
\t}}
\tencode gzip
\treverse_proxy emf-panel:8001
}}"""


def _auth_block(auth: str) -> str:
    return f"""{auth} {{
\treverse_proxy mock-oidc:8080
}}"""


def _map_block(map_domain: str) -> str:
    # No longer embedded as an iframe by report/panel — served standalone only.
    csp = (
        "default-src 'self'; script-src 'self' blob:; worker-src blob:; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; "
        f"font-src 'self' {_MAP_EXTERNAL_FONT}; "
        f"connect-src 'self' {_MAP_EXTERNAL_CONNECT}; "
        "frame-ancestors 'none'"
    )
    return f"""{map_domain} {{
\theader {{
\t\t{_hsts()}
\t\tX-Content-Type-Options "nosniff"
\t\tContent-Security-Policy "{csp}"
\t\tReferrer-Policy "strict-origin-when-cross-origin"
\t\t-Server
\t}}
\treverse_proxy emf-map:8080
}}"""


def _swagger_block(swagger: str) -> str:
    return f"""{swagger} {{
\treverse_proxy swagger:8080
}}"""


def _mattermost_block(mattermost: str) -> str:
    return f"""{mattermost} {{
\treverse_proxy mattermost:8065
}}"""


def generate(domains: dict[str, str]) -> str:
    report = domains["report"]
    panel = domains["panel"]
    map_domain: str | None = domains.get("map")
    auth_domain: str | None = domains.get("auth")
    swagger_domain: str | None = domains.get("swagger")
    mattermost_domain: str | None = domains.get("mattermost")

    blocks: list[str] = [
        "# EMF Conduct vhosts.",
        "# Generated by scripts/generate_caddyfile.py — edit config.json domains to regenerate.",
        "# This file is imported by ghost-docker's Caddy via a volume mount.",
        "# Do not add a global block here.",
        "",
        _report_block(report, map_domain),
        "",
        _panel_block(panel, map_domain),
    ]

    if auth_domain:
        blocks += ["", _auth_block(auth_domain)]

    if map_domain:
        blocks += ["", _map_block(map_domain)]

    if swagger_domain:
        blocks += ["", _swagger_block(swagger_domain)]

    if mattermost_domain:
        blocks += ["", _mattermost_block(mattermost_domain)]

    return "\n".join(blocks) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "config.json",
        help="Path to config.json (default: repo-root/config.json)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(DEFAULT_OUTPUT),
        help="Output path, or - for stdout (default: infra/caddy/Caddyfile.wolfcraig)",
    )
    args = parser.parse_args()

    domains = load_domains(args.config)
    content = generate(domains)

    if args.output == "-":
        sys.stdout.write(content)
    else:
        out = Path(args.output)
        out.write_text(content)
        print(f"Written to {out}")


if __name__ == "__main__":
    main()
