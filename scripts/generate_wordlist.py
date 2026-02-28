"""Generate the friendly-ID wordlist.

Requires: uv run --with wordfreq --with better-profanity generate_wordlist.py

Filters applied:
- 4–8 letters, alpha only
- No proper nouns (words starting with uppercase in the source)
- Profanity filter via better-profanity
- Exclude words with ambiguous spellings / homographs (manual block list)
- Target ~10,000 words

Output: shared/src/emf_shared/wordlist.txt
"""

import re
import sys
from pathlib import Path

AMBIGUOUS = frozenset(
    {
        "lead",
        "read",
        "live",
        "wound",
        "tear",
        "close",
        "minute",
        "object",
        "permit",
        "present",
        "protest",
        "record",
        "refuse",
        "subject",
        "use",
        "invalid",
        "row",
        "bass",
        "bow",
        "does",
        "dove",
        "evening",
        "excuse",
        "house",
        "moped",
        "mouth",
        "number",
        "sake",
        "sewer",
        "shower",
        "sow",
        "supply",
        "tear",
        "wind",
    }
)

TARGET = 10_000
OUTPUT = Path(__file__).parent.parent / "shared" / "src" / "emf_shared" / "wordlist.txt"


def main() -> None:
    try:
        from better_profanity import profanity  # type: ignore[import-untyped]
        from wordfreq import top_n_list  # type: ignore[import-untyped]
    except ImportError:
        print(
            "Run with: uv run --with wordfreq --with better-profanity scripts/generate_wordlist.py",
            file=sys.stderr,
        )
        sys.exit(1)

    profanity.load_censor_words()
    words: list[str] = []

    for word in top_n_list("en", 40_000):
        if not re.fullmatch(r"[a-z]{4,8}", word):
            continue
        if word in AMBIGUOUS:
            continue
        if profanity.contains_profanity(word):
            continue
        words.append(word)
        if len(words) >= TARGET:
            break

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(words) + "\n")
    print(f"Wrote {len(words)} words to {OUTPUT}")


if __name__ == "__main__":
    main()
