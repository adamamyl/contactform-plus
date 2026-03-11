#!/usr/bin/env python3
"""
EMF Conduct System database backup script.

Creates an encrypted, compressed backup of the PostgreSQL database.

Usage:
    python backup.py [--recipient <age-pubkey>] [--rsync <dest>]
                     [--systemd] [--dry-run]

Output file format: emf_forms-<ISO8601>.dump.zst.age
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.resolve()
BACKUP_DIR = REPO_ROOT / "backups"


def _run(cmd: list[str], stdin: bytes | None = None, dry_run: bool = False) -> bytes:
    if dry_run:
        print(f"  [dry-run] {' '.join(str(c) for c in cmd)}")
        return b""
    proc = subprocess.run(
        cmd,
        input=stdin,
        capture_output=True,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace")
        print(f"ERROR: {' '.join(str(c) for c in cmd)}\n{err}", file=sys.stderr)
        sys.exit(1)
    return proc.stdout


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="EMF Conduct database backup",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--recipient", "-r",
        help="age public key for encryption (reads AGE_RECIPIENT env var if not set)",
    )
    parser.add_argument(
        "--database-url", "-u",
        default=os.environ.get("DATABASE_URL", ""),
        help="PostgreSQL connection URL (reads DATABASE_URL env var if not set)",
    )
    parser.add_argument(
        "--rsync", metavar="DEST",
        help="rsync destination for the backup file (e.g. user@host:/backups/)",
    )
    parser.add_argument(
        "--systemd", action="store_true",
        help="Generate systemd .service and .timer unit files",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print actions without executing",
    )
    parser.add_argument(
        "--output-dir", default=str(BACKUP_DIR),
        help=f"Directory to write backups to (default: {BACKUP_DIR})",
    )
    return parser.parse_args()


def pg_dump(database_url: str, dry_run: bool = False) -> bytes:
    print("  Running pg_dump...")
    return _run(
        ["pg_dump", "--format=custom", database_url],
        dry_run=dry_run,
    )


def compress(data: bytes, dry_run: bool = False) -> bytes:
    print("  Compressing with zstd...")
    return _run(["zstd", "--stdout", "-"], stdin=data, dry_run=dry_run)


def encrypt(data: bytes, recipient: str, dry_run: bool = False) -> bytes:
    print(f"  Encrypting with age (recipient: {recipient[:20]}...)")
    return _run(
        ["age", "--encrypt", "--recipient", recipient],
        stdin=data,
        dry_run=dry_run,
    )


def write_backup(
    data: bytes,
    output_dir: Path,
    dry_run: bool = False,
) -> Path:
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"emf_forms-{ts}.dump.zst.age"
    out_path = output_dir / filename

    if dry_run:
        print(f"  [dry-run] Would write {out_path}")
        return out_path

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    os.chmod(out_path, 0o600)
    size_mb = len(data) / 1024 / 1024
    print(f"  Written: {out_path} ({size_mb:.1f} MB)")
    return out_path


def rsync_backup(path: Path, dest: str, dry_run: bool = False) -> None:
    print(f"  rsyncing to {dest}...")
    _run(["rsync", "-az", "--progress", str(path), dest], dry_run=dry_run)


def generate_systemd_units(dry_run: bool = False) -> None:
    service_content = f"""\
[Unit]
Description=EMF Conduct database backup
After=network.target

[Service]
Type=oneshot
ExecStart={sys.executable} {Path(__file__).resolve()}
WorkingDirectory={REPO_ROOT}
EnvironmentFile={REPO_ROOT}/.env
"""
    timer_content = """\
[Unit]
Description=EMF Conduct backup timer

[Timer]
OnCalendar=*-*-* 04:00:00
Persistent=true

[Install]
WantedBy=timers.target
"""
    service_path = Path("/etc/systemd/system/emf-backup.service")
    timer_path = Path("/etc/systemd/system/emf-backup.timer")

    if dry_run:
        print(f"  [dry-run] Would write {service_path}")
        print(f"  [dry-run] Would write {timer_path}")
        print("  [dry-run] Would run: systemctl enable --now emf-backup.timer")
        return

    service_path.write_text(service_content)
    timer_path.write_text(timer_content)
    _run(["systemctl", "daemon-reload"])
    _run(["systemctl", "enable", "--now", "emf-backup.timer"])
    print(f"  systemd units installed: {service_path}, {timer_path}")
    print("  Timer enabled and started")


def main() -> None:
    args = parse_args()
    dry_run = args.dry_run

    if args.systemd:
        print("Generating systemd units...")
        generate_systemd_units(dry_run=dry_run)
        return

    recipient = args.recipient or os.environ.get("AGE_RECIPIENT", "")
    if not recipient:
        print(
            "ERROR: --recipient or AGE_RECIPIENT env var required for encryption",
            file=sys.stderr,
        )
        sys.exit(1)

    database_url = args.database_url
    if not database_url:
        print("ERROR: --database-url or DATABASE_URL env var required", file=sys.stderr)
        sys.exit(1)

    print("EMF Conduct backup starting...")

    dump_data = pg_dump(database_url, dry_run=dry_run)
    compressed = compress(dump_data, dry_run=dry_run)
    encrypted = encrypt(compressed, recipient, dry_run=dry_run)

    out_dir = Path(args.output_dir)
    out_path = write_backup(encrypted, out_dir, dry_run=dry_run)

    if args.rsync:
        rsync_backup(out_path, args.rsync, dry_run=dry_run)

    print("Backup complete.")


if __name__ == "__main__":
    main()
