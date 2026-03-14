#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.28.0", "rich>=13.0.0"]
# ///
"""
EMF bad-strings fuzz tester.

Fetches the Big List of Naughty Strings (BLNS), samples a stratified subset,
and POSTs each string to the form API.  Results are written to a JSON file
small enough to paste into a Claude conversation for analysis.

Usage:
    uv run scripts/bad_strings_test.py --url http://localhost:8000

    # Recommended: run against the isolated e2e stack
    docker compose -f infra/docker-compose.yml -f infra/docker-compose.e2e.yml up -d
    uv run scripts/bad_strings_test.py --url http://localhost:8000 --sample 50 --seed 42
    docker compose -f infra/docker-compose.yml -f infra/docker-compose.e2e.yml down -v
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
from datetime import date
from pathlib import Path

import httpx
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TaskID,
)
from rich.table import Table

BLNS_URL = (
    "https://raw.githubusercontent.com/minimaxir/big-list-of-naughty-strings"
    "/master/blns.json"
)
CACHE_PATH = Path.home() / ".cache" / "emf-blns.json"
MIN_WHAT_HAPPENED_LEN = 10
CONSOLE = Console()


def _fetch_blns() -> list[str]:
    if CACHE_PATH.exists():
        raw: list[str] = json.loads(CACHE_PATH.read_text())
        return raw
    CONSOLE.print("[dim]Fetching BLNS from GitHub...[/dim]")
    try:
        resp = httpx.get(BLNS_URL, timeout=15, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        CONSOLE.print(f"[red]Failed to fetch BLNS: {exc}[/red]")
        sys.exit(1)
    raw = resp.json()
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(raw))
    return raw  # type: ignore[no-any-return]


def _parse_categories(raw: list[str]) -> dict[str, list[str]]:
    categories: dict[str, list[str]] = {}
    current_category = "uncategorised"
    for entry in raw:
        if not entry:
            continue
        if entry.startswith("#"):
            current_category = entry.lstrip("# ").strip()
            continue
        categories.setdefault(current_category, []).append(entry)
    return categories


def _stratified_sample(
    categories: dict[str, list[str]], n: int, rng: random.Random
) -> list[str]:
    cat_list = [strings for strings in categories.values() if strings]
    if n >= sum(len(c) for c in cat_list):
        return [s for strings in cat_list for s in strings]

    # Take one from each category first
    picked: list[str] = [rng.choice(strings) for strings in cat_list]
    picked_set = set(picked)

    # Fill remaining slots from the full pool (excluding already picked)
    remaining_n = max(0, n - len(picked))
    pool = [s for strings in cat_list for s in strings if s not in picked_set]
    if remaining_n > 0 and pool:
        extra = rng.sample(pool, min(remaining_n, len(pool)))
        picked.extend(extra)

    return picked[:n]


def _pad(s: str) -> str:
    if len(s) < MIN_WHAT_HAPPENED_LEN:
        return s + " " * (MIN_WHAT_HAPPENED_LEN - len(s))
    return s


def _make_payload(s: str) -> dict[str, object]:
    return {
        "event_name": "EMF 2026",
        "reporter": {
            "name": s[:128] if s else "Test",
            "pronouns": None,
            "email": None,
            "phone": None,
            "camping_with": None,
        },
        "what_happened": _pad(s),
        "incident_date": str(date(2026, 5, 30)),
        "incident_time": "14:00:00",
        "location": {"text": "Test location"},
        "urgency": "low",
        "additional_info": s if s else None,
        "support_needed": None,
        "others_involved": None,
        "why_it_happened": None,
        "can_contact": None,
        "anything_else": None,
        "website": None,
    }


async def _test_string(
    client: httpx.AsyncClient,
    s: str,
    semaphore: asyncio.Semaphore,
    progress: Progress,
    overall_task: TaskID,
    slot_task: TaskID,
    silent: bool,
) -> dict[str, object]:
    async with semaphore:
        if not silent:
            preview = s[:40].replace("\n", "↵").replace("\r", "↵")
            progress.update(slot_task, description=f"[dim]{preview!r}[/dim]")
        start = time.monotonic()
        try:
            resp = await client.post("/api/submit", json=_make_payload(s), timeout=15.0)
            ms = int((time.monotonic() - start) * 1000)
            error: str | None = None
            if resp.status_code not in (200, 201, 422, 429):
                error = resp.text[:200]
        except httpx.HTTPError as exc:
            ms = int((time.monotonic() - start) * 1000)
            resp = None  # type: ignore[assignment]
            error = str(exc)[:200]

        status = resp.status_code if resp is not None else 0
        if not silent:
            progress.update(overall_task, advance=1)
        return {"string": s, "status": status, "ms": ms, "error": error}


async def _run(
    url: str,
    strings: list[str],
    concurrency: int,
    silent: bool,
    output: Path,
) -> int:
    semaphore = asyncio.Semaphore(concurrency)

    progress = Progress(
        SpinnerColumn(),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TextColumn("{task.description}"),
        console=CONSOLE,
        disable=silent,
    )

    overall_task = progress.add_task("[cyan]Testing strings[/cyan]", total=len(strings))
    slot_tasks = [
        progress.add_task(f"[dim]slot {i}[/dim]", total=None)
        for i in range(min(concurrency, len(strings)))
    ]

    results: list[dict[str, object]] = []
    n_slots = len(slot_tasks)
    async with httpx.AsyncClient(base_url=url, follow_redirects=False) as client:
        with progress:
            tasks = [
                _test_string(
                    client, s, semaphore, progress, overall_task,
                    slot_tasks[i % n_slots], silent,
                )
                for i, s in enumerate(strings)
            ]
            results = list(await asyncio.gather(*tasks))

    summary: dict[str, int] = {}
    for r in results:
        key = str(r["status"])
        summary[key] = summary.get(key, 0) + 1

    output.write_text(
        json.dumps(
            {
                "url": url,
                "sample_size": len(strings),
                "results": results,
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if not silent:
        table = Table(title=f"Results — {url}")
        table.add_column("Status")
        table.add_column("Count", justify="right")
        for code, count in sorted(summary.items()):
            style = "green" if code in ("200", "201", "422", "429") else "red bold"
            table.add_row(code, str(count), style=style)
        CONSOLE.print(table)
        CONSOLE.print(f"Wrote details to [bold]{output}[/bold]")

    errors_5xx = sum(v for k, v in summary.items() if k.startswith("5"))
    zero_count = summary.get("0", 0)
    return 1 if errors_5xx or zero_count else 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test the form API against the Big List of Naughty Strings",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--url", default="http://localhost:8000", help="Form service base URL")
    parser.add_argument(
        "--sample", type=int, default=50, metavar="N",
        help="Number of strings to test (default: 50)",
    )
    sampling = parser.add_mutually_exclusive_group()
    sampling.add_argument(
        "--seed", type=int, default=None,
        help="Random seed for reproducible sample (mutually exclusive with --all)",
    )
    sampling.add_argument(
        "--all", dest="use_all", action="store_true",
        help="Test all strings without sampling (mutually exclusive with --seed)",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("bad_strings_results.json"),
        help="Output JSON file (default: bad_strings_results.json)",
    )
    parser.add_argument(
        "--concurrency", type=int, default=3, metavar="N",
        help="Concurrent requests (default: 3; keep well under rate limit)",
    )
    parser.add_argument(
        "--silent", action="store_true",
        help="Suppress progress display; print summary only",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    raw = _fetch_blns()
    categories = _parse_categories(raw)
    total = sum(len(v) for v in categories.values())

    if args.use_all:
        strings = [s for strings in categories.values() for s in strings]
    else:
        seed = args.seed if args.seed is not None else random.randint(0, 2**31)
        rng = random.Random(seed)
        strings = _stratified_sample(categories, args.sample, rng)
        if not args.silent:
            CONSOLE.print(
                f"[dim]Sampled {len(strings)} of {total} strings "
                f"across {len(categories)} categories (seed={seed})[/dim]"
            )

    if not args.silent:
        CONSOLE.print(
            f"[bold]Testing {len(strings)} strings against[/bold] {args.url}"
        )

    exit_code = asyncio.run(
        _run(args.url, strings, args.concurrency, args.silent, args.output)
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
