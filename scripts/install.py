#!/usr/bin/env python3
"""
EMF Conduct System installer.

Usage:
    python install.py [-q | -v] [-d] [--dry-run] [--help]

-q / --quiet    Suppress progress output
-v / --verbose  Show detailed output
-d / --debug    Show debug output (implies -v)
--dry-run       Print all actions without executing
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.resolve()


def _say(msg: str, verbose: bool = False, quiet: bool = False, is_verbose_msg: bool = False) -> None:
    if quiet:
        return
    if is_verbose_msg and not verbose:
        return
    print(msg)


def _run(cmd: list[str], dry_run: bool = False, capture: bool = False) -> str:
    if dry_run:
        print(f"  [dry-run] {' '.join(cmd)}")
        return ""
    result = subprocess.run(cmd, capture_output=capture, text=True)
    if result.returncode != 0:
        print(f"ERROR: command failed: {' '.join(cmd)}", file=sys.stderr)
        if capture and result.stderr:
            print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip() if capture else ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="EMF Conduct System installer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("-q", "--quiet", action="store_true", help="Suppress progress output")
    verbosity.add_argument("-v", "--verbose", action="store_true", help="Show detailed output")
    verbosity.add_argument("-d", "--debug", action="store_true", help="Show debug output")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without executing")
    return parser.parse_args()


def select_components() -> list[str]:
    print("\nSelect components to install:")
    components = ["form", "panel", "router", "tts", "jambonz"]
    selected = []
    for comp in components:
        answer = input(f"  Install {comp}? [Y/n]: ").strip().lower()
        if answer in ("", "y", "yes"):
            selected.append(comp)
    return selected


def select_proxy() -> str:
    print("\nSelect reverse proxy:")
    print("  1) Caddy (recommended — automatic ACME, HTTP/2)")
    print("  2) nginx (requires manual certbot — note: 47-day ACME expiry)")
    print("  3) Traefik (requires manual certbot — note: 47-day ACME expiry)")
    while True:
        choice = input("  Choice [1]: ").strip() or "1"
        if choice == "1":
            return "caddy"
        if choice == "2":
            print("  ⚠  nginx: remember to set up certbot renewal for 47-day ACME certificates")
            return "nginx"
        if choice == "3":
            print("  ⚠  Traefik: remember to set up certbot renewal for 47-day ACME certificates")
            return "traefik"
        print("  Invalid choice")


def select_tls_method() -> str:
    print("\nTLS certificate method:")
    print("  1) HTTP challenge (requires port 80 public access)")
    print("  2) DNS challenge (requires DNS API credentials)")
    print("  3) Manual (you supply certs)")
    while True:
        choice = input("  Choice [1]: ").strip() or "1"
        if choice in ("1", "2", "3"):
            return {"1": "http", "2": "dns", "3": "manual"}[choice]
        print("  Invalid choice")


def generate_postgres_tls_cert(dry_run: bool = False) -> None:
    cert_dir = REPO_ROOT / "infra" / "postgres" / "certs"
    cert_path = cert_dir / "server.crt"
    key_path = cert_dir / "server.key"

    if cert_path.exists() and key_path.exists():
        print("  PostgreSQL TLS certs already exist, skipping")
        return

    if dry_run:
        print(f"  [dry-run] Generate self-signed cert in {cert_dir}")
        return

    cert_dir.mkdir(parents=True, exist_ok=True)

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        import datetime

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "postgres"),
        ])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
            .not_valid_after(
                datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650)
            )
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("postgres")]), critical=False)
            .sign(key, hashes.SHA256())
        )

        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        os.chmod(key_path, 0o600)
        print(f"  Generated PostgreSQL TLS cert in {cert_dir}")
    except ImportError:
        print("  cryptography package not available; generating cert with openssl")
        _run([
            "openssl", "req", "-new", "-x509", "-days", "3650",
            "-nodes", "-text",
            "-out", str(cert_path),
            "-keyout", str(key_path),
            "-subj", "/CN=postgres",
        ], dry_run=dry_run)
        os.chmod(key_path, 0o600)


def generate_compose(components: list[str], dry_run: bool = False) -> None:
    compose_src = REPO_ROOT / "infra" / "docker-compose.yml"
    if not dry_run:
        print(f"  Using {compose_src} (edit to enable/disable services)")
    else:
        print(f"  [dry-run] Would use {compose_src}")


def generate_caddyfile(proxy: str, tls_method: str, dry_run: bool = False) -> None:
    if proxy != "caddy":
        print(f"  Proxy '{proxy}' selected — configure manually")
        return
    src = REPO_ROOT / "infra" / "caddy" / "Caddyfile.prod"
    if dry_run:
        print(f"  [dry-run] Would use {src}")
    else:
        print(f"  Using {src} — set PROJECT_NAME in .env")


def signal_setup_walkthrough() -> None:
    print("\n  Signal group registration:")
    print("  1. Register your Signal number with signal-cli:")
    print("     docker compose exec signal-api signal-cli -a <phone> register")
    print("     docker compose exec signal-api signal-cli -a <phone> verify <code>")
    print("  2. List groups:")
    print("     docker compose exec signal-api signal-cli -a <phone> listGroups")
    print("  3. Copy the group ID (base64) into config.json → events[0].signal_group_id")
    input("  Press Enter when done (or Ctrl+C to skip)...")


def validate_config(dry_run: bool = False) -> None:
    compose_file = REPO_ROOT / "infra" / "docker-compose.yml"
    _run(["docker", "compose", "-f", str(compose_file), "config", "--quiet"], dry_run=dry_run)

    env_file = REPO_ROOT / ".env"
    if not dry_run and env_file.exists():
        env_text = env_file.read_text()
        changeme_count = env_text.count("changeme")
        if changeme_count > 0:
            print(f"  ⚠  {changeme_count} secret(s) still set to 'changeme' in .env")
            print("     Run: python scripts/generate_secrets.py")
        else:
            print("  .env: no remaining placeholder values ✓")
    elif not dry_run:
        print("  ⚠  .env file not found — run: python scripts/generate_secrets.py")
    else:
        print("  [dry-run] Would check .env for placeholder values")


def start_stack(dry_run: bool = False) -> None:
    compose_file = REPO_ROOT / "infra" / "docker-compose.yml"
    _run(
        ["docker", "compose", "-f", str(compose_file), "up", "-d", "--build"],
        dry_run=dry_run,
    )


def main() -> None:
    args = parse_args()
    verbose = args.verbose or args.debug
    quiet = args.quiet
    dry_run = args.dry_run

    if dry_run:
        print("=== DRY RUN MODE — no changes will be made ===\n")

    _say("EMF Conduct System Installer", quiet=quiet)
    _say("=" * 40, quiet=quiet)

    components = select_components()
    proxy = select_proxy()
    tls_method = select_tls_method()

    _say(f"\nSelected components: {', '.join(components) or 'none'}", quiet=quiet)
    _say(f"Proxy: {proxy}, TLS: {tls_method}", quiet=quiet)

    _say("\n[1/6] Generating secrets...", quiet=quiet)
    _run(
        [sys.executable, str(REPO_ROOT / "scripts" / "generate_secrets.py")],
        dry_run=dry_run,
    )

    _say("[2/6] Generating PostgreSQL TLS certificate...", quiet=quiet)
    generate_postgres_tls_cert(dry_run=dry_run)

    _say("[3/6] Configuring Docker Compose...", quiet=quiet)
    generate_compose(components, dry_run=dry_run)

    _say("[4/6] Configuring Caddy...", quiet=quiet)
    generate_caddyfile(proxy, tls_method, dry_run=dry_run)

    _say("[5/6] Validating configuration...", quiet=quiet)
    validate_config(dry_run=dry_run)

    _say("[6/6] Starting services...", quiet=quiet)
    start_stack(dry_run=dry_run)

    if "router" in components:
        answer = input("\nSet up Signal notifications? [y/N]: ").strip().lower()
        if answer in ("y", "yes"):
            signal_setup_walkthrough()

    _say("\n✅ Installation complete!", quiet=quiet)


if __name__ == "__main__":
    main()
