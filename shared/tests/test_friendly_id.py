from emf_shared.friendly_id import generate, generate_unique


def test_generates_four_word_hyphenated_string() -> None:
    result = generate()
    parts = result.split("-")
    assert len(parts) == 4
    assert all(part.islower() for part in parts)


def test_words_are_from_wordlist() -> None:
    from importlib.resources import files

    wordlist = set(files("emf_shared").joinpath("wordlist.txt").read_text().splitlines())
    result = generate()
    for word in result.split("-"):
        assert word in wordlist


def test_generate_unique_avoids_existing() -> None:
    existing: set[str] = set()
    seen: set[str] = set()
    for _ in range(20):
        fid = generate_unique(existing)
        existing.add(fid)
        seen.add(fid)
    assert len(seen) == 20


def test_uuid_fallback_on_collision(monkeypatch: object) -> None:
    import emf_shared.friendly_id as mod

    monkeypatch.setattr(mod, "generate", lambda: "fixed-id-every-time-always")
    existing = {"fixed-id-every-time-always"}
    result = generate_unique(existing, uuid_fallback="abcdef12-rest")
    assert result == "abcdef12"
