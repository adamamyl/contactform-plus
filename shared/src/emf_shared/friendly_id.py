from __future__ import annotations

import logging
import secrets
from importlib.resources import files

_WORDLIST: list[str] = (
    files("emf_shared").joinpath("wordlist.txt").read_text().splitlines()
)

_log = logging.getLogger(__name__)


def generate() -> str:
    return "-".join(secrets.choice(_WORDLIST) for _ in range(4))


def generate_unique(existing: set[str], uuid_fallback: str = "") -> str:
    for _ in range(10):
        candidate = generate()
        if candidate not in existing:
            return candidate
    _log.warning(
        "Could not generate unique friendly ID in 10 attempts; using UUID fallback"
    )
    return uuid_fallback[:8] if uuid_fallback else generate()
