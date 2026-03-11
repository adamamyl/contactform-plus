"""Populate .env from .env-example.

Replaces every 'changeme' placeholder with a cryptographically-random
token. Idempotent: existing non-default values are never overwritten.

Usage:
    uv run scripts/generate_secrets.py [--env-file PATH]
"""

import argparse
import secrets
import stat
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate .env secrets from .env-example")
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to write (default: .env)",
    )
    parser.add_argument(
        "--example-file",
        default=".env-example",
        help="Path to read (default: .env-example)",
    )
    args = parser.parse_args()

    example = Path(args.example_file)
    target = Path(args.env_file)

    if not example.exists():
        raise SystemExit(f"Example file not found: {example}")

    existing: dict[str, str] = {}
    if target.exists():
        for line in target.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                existing[key.strip()] = val.strip()

    lines: list[str] = []
    for raw_line in example.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            lines.append(raw_line)
            continue

        key, _, val = line.partition("=")
        key = key.strip()

        if key in existing and existing[key] != "changeme":
            lines.append(f"{key}={existing[key]}")
        elif val.strip() == "changeme":
            lines.append(f"{key}={secrets.token_urlsafe(32)}")
        else:
            lines.append(raw_line)

    target.write_text("\n".join(lines) + "\n")
    target.chmod(stat.S_IRUSR | stat.S_IWUSR)
    print(f"Written: {target} (mode 600)")


if __name__ == "__main__":
    main()
