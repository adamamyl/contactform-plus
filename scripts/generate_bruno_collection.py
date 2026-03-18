#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""
Generate a Bruno OpenCollection from docs/swagger-spec.json.

Usage:
    uv run scripts/generate_bruno_collection.py

The script reads docs/swagger-spec.json (Postman v2.1 format) and writes the
complete Bruno collection to ~/projects/bruno/emf/conduct-api/.

Environments generated: Local, Staging, Production.
All URLs and secrets use {{variables}} so you can switch targets with a single
environment flip in Bruno.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent
SPEC_FILE = REPO_ROOT / "docs" / "swagger-spec.json"
COLLECTION_DIR = Path.home() / "projects/bruno/emf/conduct-api"

# ---------------------------------------------------------------------------
# YAML helpers — hand-rolled to avoid adding a dependency
# ---------------------------------------------------------------------------

SETTINGS_BLOCK = """\
settings:
  encodeUrl: true
  timeout: 0
  followRedirects: true
  maxRedirects: 5
"""


def _yaml_str(value: str) -> str:
    """Return value quoted if it contains special YAML characters."""
    if any(c in value for c in (":", "{", "}", "[", "]", "#", '"', "'")):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _block_scalar(text: str, indent_size: int = 4) -> str:
    """Return text as an indented YAML block scalar (|)."""
    pad = " " * indent_size
    lines = text.splitlines()
    return "|\n" + "\n".join(pad + ln for ln in lines) + "\n"


# ---------------------------------------------------------------------------
# Postman → Bruno translation helpers
# ---------------------------------------------------------------------------


def _pm_script_to_bru(lines: list[str]) -> str:
    """Translate Postman test script lines to Bruno res-script lines."""
    out: list[str] = []
    for line in lines:
        line = re.sub(r"pm\.environment\.set\(", "bru.setEnvVar(", line)
        line = re.sub(r"pm\.environment\.get\(", "bru.getEnvVar(", line)
        line = line.replace("pm.response.json()", "res.body")
        out.append(line)
    return "\n".join(out)


def _build_auth(auth_obj: dict | None, default: str = "inherit") -> str:
    """Return the Bruno YAML snippet for auth."""
    if auth_obj is None:
        return f"  auth: {default}"
    t = auth_obj.get("type", "")
    if t == "noauth":
        return "  auth:\n    type: none"
    if t == "basic":
        kvs = {kv["key"]: kv["value"] for kv in auth_obj.get("basic", [])}
        return (
            f"  auth:\n    type: basic\n"
            f"    username: {kvs.get('username', '')}\n"
            f"    password: {kvs.get('password', '')}"
        )
    if t == "bearer":
        kvs = {kv["key"]: kv["value"] for kv in auth_obj.get("bearer", [])}
        return f"  auth:\n    type: bearer\n    token: {kvs.get('token', '')}"
    return f"  auth: {default}"


def _build_params(url_obj: dict) -> str:
    """Build Bruno params block from Postman URL object."""
    parts: list[str] = []

    # Path variables
    for v in url_obj.get("variable", []):
        key = v.get("key", "")
        val = v.get("value", "")
        parts.append(
            f"    - name: {key}\n      value: {_yaml_str(val)}\n      type: path"
        )

    # Query params
    for q in url_obj.get("query", []):
        key = q.get("key", "")
        val = q.get("value", "")
        disabled = q.get("disabled", False)
        entry = f"    - name: {key}\n      value: {_yaml_str(val)}"
        if disabled:
            entry += "\n      disabled: true"
        parts.append(entry)

    if not parts:
        return ""
    return "  params:\n" + "\n".join(parts) + "\n"


def _build_headers(req: dict) -> str:
    """Build Bruno headers block, skipping Content-Type (handled by body type)."""
    headers = [
        h for h in req.get("header", []) if h.get("key", "").lower() != "content-type"
    ]
    if not headers:
        return ""
    lines = ["  headers:"]
    for h in headers:
        lines.append(f"    - name: {h['key']}")
        lines.append(f"      value: {_yaml_str(str(h.get('value', '')))}")
    return "\n".join(lines) + "\n"


def _build_body(req: dict) -> str:
    """Build Bruno body block from Postman request."""
    body = req.get("body")
    if not body:
        return ""
    mode = body.get("mode", "")
    if mode == "raw":
        raw = body.get("raw", "")
        # data: is at 4 spaces; block content must be indented further (6 spaces)
        data_block = _block_scalar(raw, indent_size=6)
        return f"  body:\n    type: json\n    data: {data_block}"
    if mode == "urlencoded":
        items = body.get("urlencoded", [])
        lines = ["  body:", "    type: form-urlencoded", "    data:"]
        for kv in items:
            val = str(kv.get("value", ""))
            # Single-quote the value unless it already contains single-quotes
            if "'" in val:
                val_str = f'"{val}"'
            else:
                val_str = f"'{val}'"
            lines.append(f"      - name: {kv['key']}")
            lines.append(f"        value: {val_str}")
        return "\n".join(lines) + "\n"
    return ""


def _build_script(item: dict) -> str:
    """Build Bruno post-response script from Postman test events."""
    for event in item.get("event", []):
        if event.get("listen") == "test":
            lines = event.get("script", {}).get("exec", [])
            if lines:
                translated = _pm_script_to_bru(lines)
                block = _block_scalar(translated, indent_size=4)
                return f"script:\n  res: {block}"
    return ""


def _build_description(req: dict) -> str:
    desc = req.get("description", "")
    if not desc:
        return ""
    # Use block scalar so line breaks are preserved
    block = _block_scalar(desc, indent_size=4)
    return f"  description: {block}"


def _request_to_bru(item: dict, seq: int, default_auth: str = "inherit") -> str:
    """Convert a Postman request item dict to a Bruno YAML string."""
    req = item["request"]
    name = item["name"]
    method = req["method"].upper()
    url_obj = req.get("url", {})
    url = url_obj.get("raw", "") if isinstance(url_obj, dict) else url_obj

    lines: list[str] = [
        "info:",
        f"  name: {name}",
        "  type: http",
        f"  seq: {seq}",
        "",
        "http:",
        f"  method: {method}",
        f"  url: {_yaml_str(url)}",
    ]

    params = _build_params(url_obj if isinstance(url_obj, dict) else {})
    if params:
        lines.append(params.rstrip())

    headers = _build_headers(req)
    if headers:
        lines.append(headers.rstrip())

    body = _build_body(req)
    if body:
        lines.append(body.rstrip())

    auth = _build_auth(req.get("auth"), default_auth)
    lines.append(auth)

    desc = _build_description(req)
    if desc:
        lines.append(desc.rstrip())

    script = _build_script(item)
    if script:
        lines.append("")
        lines.append(script.rstrip())

    lines.append("")
    lines.append(SETTINGS_BLOCK)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File / folder writers
# ---------------------------------------------------------------------------


def _safe_filename(name: str) -> str:
    """Convert a request name to a safe .yml filename."""
    name = re.sub(r"[^\w\s\-]", "", name).strip()
    name = re.sub(r"\s+", " ", name)
    return name + ".yml"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    print(f"  wrote  {path.relative_to(COLLECTION_DIR)}")


def _folder_yml(name: str, seq: int) -> str:
    return (
        f"info:\n"
        f"  name: {name}\n"
        f"  type: folder\n"
        f"  seq: {seq}\n"
        f"\n"
        f"request:\n"
        f"  auth: inherit\n"
    )


# ---------------------------------------------------------------------------
# Environment writers
# ---------------------------------------------------------------------------


def _env_yml(variables: list[tuple[str, str]]) -> str:
    lines = ["variables:"]
    for name, value in variables:
        if value:
            lines.append(f"  - name: {name}")
            lines.append(f"    value: {_yaml_str(value)}")
        else:
            lines.append(f"  - name: {name}")
            lines.append('    value: ""')
    return "\n".join(lines) + "\n"


COMMON_VARS = [
    ("panel_url", None),
    ("router_url", None),
    ("report_url", None),
    ("oidc_url", None),
    ("oidc_client_secret", None),
    ("access_token", ""),
    ("dispatcher_token", ""),
]

ENVIRONMENTS: dict[str, list[tuple[str, str]]] = {
    "Local": [
        ("panel_url", "https://panel.emf-forms.internal"),
        ("router_url", "https://router.emf-forms.internal"),
        ("report_url", "https://report.emf-forms.internal"),
        ("oidc_url", "https://oidc.emf-forms.internal"),
        ("oidc_client_secret", ""),  # paste from .env OIDC_CLIENT_SECRET
        ("access_token", ""),
        ("dispatcher_token", ""),
    ],
    "Staging": [
        ("panel_url", "https://panel.staging.emf-forms.internal"),
        ("router_url", "https://router.staging.emf-forms.internal"),
        ("report_url", "https://report.staging.emf-forms.internal"),
        ("oidc_url", "https://oidc.staging.emf-forms.internal"),
        ("oidc_client_secret", ""),
        ("access_token", ""),
        ("dispatcher_token", ""),
    ],
    "Production": [
        ("panel_url", "https://panel.emfcamp.org"),
        ("router_url", "https://router.emfcamp.org"),
        ("report_url", "https://report.emfcamp.org"),
        ("oidc_url", "https://oidc.emfcamp.org"),
        ("oidc_client_secret", ""),
        ("access_token", ""),
        ("dispatcher_token", ""),
    ],
}


# ---------------------------------------------------------------------------
# Postman folder → Bruno subfolder mapping
# ---------------------------------------------------------------------------

# Maps Postman folder name → (collection subdirectory, folder display name, seq)
FOLDER_MAP: dict[str, tuple[str, str, int]] = {
    "Panel \u2014 Cases": ("Panel/Cases", "Cases", 1),
    "Panel \u2014 Lookup Lists": ("Panel/Lists", "Lists", 2),
    "Panel \u2014 Dispatcher Sessions": (
        "Panel/Dispatcher Sessions",
        "Dispatcher Sessions",
        3,
    ),
    "Panel \u2014 Dispatcher (token auth)": ("Panel/Dispatcher", "Dispatcher", 4),
    "Report Form": ("Report Form", "Report Form", 3),
    "Message Router": ("Message Router", "Message Router", 4),
    "Health Checks": ("Health", "Health", 5),
}

# Auth request is top-level and gets special handling (basic auth, script)
AUTH_SEQ = 1
PANEL_SEQ = 2


# ---------------------------------------------------------------------------
# Auth request writer
# ---------------------------------------------------------------------------


def _write_auth(spec: dict) -> None:
    """Find the Auth folder in spec and write Auth.yml using {{variables}}."""
    auth_folder = next((f for f in spec["item"] if f["name"] == "Auth"), None)
    if not auth_folder:
        print("  warn: Auth folder not found in spec")
        return

    # Auth is written verbatim with {{variables}} — no need to parse the spec item
    url = "{{oidc_url}}/default/token"
    content = (
        f"info:\n"
        f"  name: Auth\n"
        f"  type: http\n"
        f"  seq: {AUTH_SEQ}\n"
        f"\n"
        f"http:\n"
        f"  method: POST\n"
        f'  url: "{url}"\n'
        f"  body:\n"
        f"    type: form-urlencoded\n"
        f"    data:\n"
        f"      - name: grant_type\n"
        f"        value: client_credentials\n"
        f"      - name: scope\n"
        f"        value: 'openid email profile groups'\n"
        f"      - name: claims\n"
        f'        value: \'{{"groups": ["team_conduct"]}}\'\n'
        f"      - name: audience\n"
        f"        value: panel\n"
        f"      - name: resource\n"
        f"        value: '{{{{panel_url}}}}'\n"
        f"  auth:\n"
        f"    type: basic\n"
        f"    username: panel\n"
        f"    password: {{{{oidc_client_secret}}}}\n"
        f"\n"
        f"script:\n"
        f"  res: |\n"
        f'    bru.setEnvVar("access_token", res.body.access_token);\n'
        f"\n"
        f"settings:\n"
        f"  encodeUrl: true\n"
        f"  timeout: 0\n"
        f"  followRedirects: true\n"
        f"  maxRedirects: 5\n"
    )
    _write(COLLECTION_DIR / "Auth.yml", content)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    if not SPEC_FILE.exists():
        print(f"Error: spec file not found: {SPEC_FILE}", file=sys.stderr)
        return 1

    if not COLLECTION_DIR.exists():
        print(f"Error: collection dir not found: {COLLECTION_DIR}", file=sys.stderr)
        return 1

    spec = json.loads(SPEC_FILE.read_text())
    print(f"Loaded spec: {spec['info']['name']}")

    # ------------------------------------------------------------------
    # Clean up old flat Panel request files (replaced by subfolders)
    # ------------------------------------------------------------------
    stale = [
        COLLECTION_DIR / "Panel" / "Case List.yml",
        COLLECTION_DIR / "Panel" / "Admin Ack.yml",
        COLLECTION_DIR / "Panel" / "List Assignees.yml",
        COLLECTION_DIR / "Panel" / "Cases" / "folder.yml",  # re-written below
    ]
    for p in stale:
        if p.exists():
            p.unlink()
            print(f"  removed {p.relative_to(COLLECTION_DIR)}")

    # ------------------------------------------------------------------
    # Environments
    # ------------------------------------------------------------------
    print("\nEnvironments:")
    env_dir = COLLECTION_DIR / "environments"
    for env_name, variables in ENVIRONMENTS.items():
        # Bruno environment files include the name as a header
        content = f"name: {env_name}\n" + _env_yml(variables)
        _write(env_dir / f"{env_name}.yml", content)

    # ------------------------------------------------------------------
    # Auth (top-level)
    # ------------------------------------------------------------------
    print("\nAuth:")
    _write_auth(spec)

    # ------------------------------------------------------------------
    # Panel folder.yml
    # ------------------------------------------------------------------
    panel_dir = COLLECTION_DIR / "Panel"
    _write(panel_dir / "folder.yml", _folder_yml("Panel", PANEL_SEQ))

    # ------------------------------------------------------------------
    # All other folders from spec
    # ------------------------------------------------------------------
    for pm_folder in spec["item"]:
        folder_name = pm_folder["name"]
        if folder_name == "Auth":
            continue  # handled above

        mapping = FOLDER_MAP.get(folder_name)
        if mapping is None:
            print(f"  warn: no mapping for folder '{folder_name}' — skipping")
            continue

        rel_path, display_name, folder_seq = mapping
        folder_dir = COLLECTION_DIR / rel_path
        print(f"\n{folder_name} → {rel_path}/")

        # Determine parent folder.yml seq (only needed for top-level dirs)
        # folder.yml for subfolders of Panel:
        _write(folder_dir / "folder.yml", _folder_yml(display_name, folder_seq))

        for seq, item in enumerate(pm_folder.get("item", []), start=1):
            if "request" not in item:
                continue
            bru = _request_to_bru(item, seq)
            fname = _safe_filename(item["name"])
            _write(folder_dir / fname, bru)

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
