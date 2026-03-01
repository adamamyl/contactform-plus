# tts — Text-to-Speech Service

Serves synthesised audio for telephone escalation via Jambonz, using [Piper](https://github.com/rhasspy/piper).

## Model

The default Piper model is `en_GB-alan-medium` — an English-language neural TTS model.

**Accented characters and diacritical marks:** non-ASCII characters in location hints (e.g. `café`, `Héloïse`) are passed through to Piper without modification, but pronunciation quality varies. The English model has no phoneme rules for accented Latin characters; most are pronounced as their ASCII base letter. This is acceptable for the use case (brief radio-style location descriptions), but operators should keep location names simple where possible.

## Development

```bash
uv sync
uv run mypy src/ --strict
```
