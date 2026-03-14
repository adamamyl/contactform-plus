# /// script
# requires-python = ">=3.12"
# dependencies = ["rich>=13.0.0"]
# ///
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

console = Console()

_REPO_ROOT = Path(__file__).parent.parent
_COMPOSE_FILE = _REPO_ROOT / "infra" / "docker-compose.yml"
_REPORT_JSON = _REPO_ROOT / "reports" / "zap" / "form-report.json"
_REPORT_HTML = _REPO_ROOT / "reports" / "zap" / "form-report.html"

_RISK: dict[str, tuple[str, str]] = {
    "3": ("HIGH", "red bold"),
    "2": ("MEDIUM", "yellow"),
    "1": ("LOW", "cyan"),
    "0": ("INFO", "dim"),
}


def _check_docker() -> bool:
    result = subprocess.run(["docker", "info"], capture_output=True)
    return result.returncode == 0


def _run_zap() -> int:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(_COMPOSE_FILE),
            "run",
            "--rm",
            "zap",
        ],
        check=False,
    )
    return result.returncode


def _parse_report(path: Path) -> dict[str, int]:
    raw: Any = json.loads(path.read_text())
    counts: dict[str, int] = {"3": 0, "2": 0, "1": 0, "0": 0}
    for site in raw.get("site", []):
        for alert in site.get("alerts", []):
            code = str(alert.get("riskcode", "0"))
            if code in counts:
                counts[code] += int(alert.get("count", "1"))
    return counts


def _print_summary(counts: dict[str, int]) -> None:
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("Risk", min_width=8)
    table.add_column("Count", justify="right", min_width=6)
    for code in ("3", "2", "1", "0"):
        label, style = _RISK[code]
        note = "  ← investigate" if code == "2" and counts[code] else ""
        table.add_row(f"[{style}]{label}[/{style}]", f"{counts[code]}{note}")
    console.print("\n[bold]ZAP scan complete[/bold]")
    console.print(table)
    console.print(f"\nFull report: {_REPORT_HTML}")


def main() -> int:
    if not _check_docker():
        console.print("[red]Cannot reach Docker daemon. Is Docker running?[/red]")
        return 1

    console.print("[bold]Starting ZAP scan…[/bold]")
    console.print(
        "[dim]Ensure the application stack is running before scanning:[/dim]\n"
        "  docker compose -f infra/docker-compose.yml --profile local up -d\n"
    )

    rc = _run_zap()
    if rc != 0:
        console.print(f"[red]ZAP exited with code {rc}[/red]")
        return rc

    if not _REPORT_JSON.exists():
        console.print(f"[red]Report not found: {_REPORT_JSON}[/red]")
        return 1

    counts = _parse_report(_REPORT_JSON)
    _print_summary(counts)

    if sys.stdout.isatty() and _REPORT_HTML.exists():
        try:
            answer = input("\nOpen report in browser? [Y/n]: ").strip().lower()
        except EOFError:
            answer = "n"
        if answer in ("", "y"):
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.run([opener, str(_REPORT_HTML)], check=False)

    return 1 if counts["3"] else 0


if __name__ == "__main__":
    sys.exit(main())
