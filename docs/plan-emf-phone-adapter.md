# Plan: EMF Phone System Adapter

Replace Jambonz telephony with the EMF phone system API (`sip2.ix1.inferno.tel`).

## What the new API does

```
POST {EMF_PHONE_API_URL}/api/conduct/alert
Authorization: Bearer {EMF_PHONE_API_KEY}
Content-Type: application/json

{ "number": 3002, "message": "New urgent conduct case. Case reference: ECHO-3." }
```

- `number` is an integer SIP extension (not E.164)
- `message` is plain text — service appends DTMF prompt automatically
- Response is **synchronous** (call completes before HTTP returns):

```json
{ "number": 3002, "result": "ACKNOWLEDGE" | "SKIP" | "NO-ANSWER" | "HANGUP" | "NO-INPUT" }
```

## Key differences from Jambonz

| | Jambonz | EMF Phone |
|---|---|---|
| Audio | Piper TTS → file URL → Jambonz fetches | Plain text message (service does TTS) |
| Result | Async via webhook/adapter | Synchronous HTTP response |
| Number format | E.164 / SIP URI / user | Integer extension |
| Internal service needed? | Yes (`apps/jambonz`, port 8004) | No |
| DTMF prompt | We include it in TTS message | Service appends it for us |

## Decisions

- **SKIP / NO-ANSWER / HANGUP / NO-INPUT**: return `None` → use existing `_send_with_retry` schedule (0/5/10/15 min). No immediate next-number escalation.
- **ACKNOWLEDGE**: return non-None message_id and trigger auto-ack via `/internal/ack`.
- **Escalation**: defined in `config.json` per-event as an ordered list of call targets with delay. First target is always tried; subsequent targets are tried only if no ack after their delay.
- **HTTP timeout**: bump to 90s (ring timeout can be ~60s).
- **EventConfig**: add `emf_phone_mode` field (analogous to `jambonz_mode`).
- **Jambonz retirement**: separate branch/PR — not touched here.

---

## Changes required

### 1. `shared/src/emf_shared/config.py`

Add a model for per-target escalation config, and fields to `EventConfig`:

```python
class EMFPhoneTarget(BaseModel):
    number: int                  # SIP extension
    description: str             # e.g. "site", "adam"
    order: int                   # sort key; lower = called first
    delay_seconds: int = 0       # seconds after previous target before calling this one
```

Add to `EventConfig`:
```python
emf_phone_mode: str = "disabled"          # "disabled" | "always" | "high_priority_only"
emf_phone_targets: list[EMFPhoneTarget] = []
```

The targets list replaces the old `call_group_number` for the EMF phone path. Example `config.json` snippet:

```json
"emf_phone_mode": "high_priority_only",
"emf_phone_targets": [
  {"number": 7483, "description": "site", "order": 1, "delay_seconds": 0},
  {"number": 2326, "description": "adam", "order": 2, "delay_seconds": 120},
  {"number": 9999, "description": "backup", "order": 3, "delay_seconds": 300}
]
```

### 2. New file: `apps/router/src/router/channels/emf_phone.py`

`EMFPhoneAdapter(ChannelAdapter)`

```python
class EMFPhoneAdapter(ChannelAdapter):
    def __init__(
        self,
        api_url: str,
        api_key: str,
        targets: list[EMFPhoneTarget],   # from shared config, already sorted by order
        router_self_url: str,
        router_internal_secret: str,
        timeout: float = 90.0,
    ) -> None: ...
```

**`is_available()`** — returns `True` if `api_url`, `api_key`, and at least one target are set. No live health check (no health endpoint on the remote API). Use standard HTTP status codes to determine the answer of available or not.

**`send(alert)`**:
1. Build message: `"New {urgency_word} conduct case. Case reference: {spoken_id}. [Location: {hint}.]"` — no DTMF suffix (service appends it).
2. Iterate `self._targets` in `order` order:
   a. If not the first target and `delay_seconds > 0`, `await asyncio.sleep(delay_seconds)`.
   b. POST `{api_url}/api/conduct/alert` with `{"number": target.number, "message": text}`, timeout 90s.
   c. On 200 + result `"ACKNOWLEDGE"`: call `_trigger_ack(alert.case_id)` and return `f"ACKNOWLEDGE:{target.number}"` (non-None → `_send_with_retry` marks SENT).
   d. On 200 + result `"SKIP"` / `"NO-ANSWER"` / `"HANGUP"` / `"NO-INPUT"`: log, continue to next target.
   e. On HTTP error / exception: log, continue to next target.
3. If all targets exhausted without ACKNOWLEDGE: return `None` (triggers retry).

**`_trigger_ack(case_id)`** — async helper:
- POST `{router_self_url}/internal/ack/{case_id}` with `{"acked_by": "emf_phone"}` and `X-Internal-Secret` header.
- Errors logged but not re-raised.

**`send_ack_confirmation()`** — no-op.

### 3. `apps/router/src/router/settings.py`

Add:
```python
emf_phone_api_url: str = ""
emf_phone_api_key: str = ""
```

No `emf_phone_number` or `emf_phone_targets` here — those come from `config.json` via `EventConfig`.

### 4. `apps/router/src/router/main.py`

In `lifespan`, alongside (not replacing) the existing Jambonz block:

```python
emf_phone_adapter: EMFPhoneAdapter | None = None
ev_targets = sorted(ev.emf_phone_targets, key=lambda t: t.order) if ev else []
if settings.emf_phone_api_url and settings.emf_phone_api_key and ev_targets:
    emf_phone_adapter = EMFPhoneAdapter(
        api_url=settings.emf_phone_api_url,
        api_key=settings.emf_phone_api_key,
        targets=ev_targets,
        router_self_url=settings.router_self_url,
        router_internal_secret=settings.router_internal_secret,
    )
```

Adapter selection: if `emf_phone_adapter` is set, use it as `phone_adapter`.

In `_route_event_time` the existing `jambonz_mode` check gates `TelephonyAdapter`; add a parallel check for `emf_phone_mode` gating `EMFPhoneAdapter`: Jambonz support will be retired.

```python
if self._phone is not None and await self._phone.is_available():
    if isinstance(self._phone, EMFPhoneAdapter):
        mode = ev.emf_phone_mode if ev else "disabled"
    else:
        mode = ev.jambonz_mode if ev else "disabled"
    if mode == "always" or (mode == "high_priority_only" and alert.urgency in ("high", "urgent")):
        channels.append(("telephony", self._phone))
```

### 5. `.env-example`

Add section:
```
# EMF Phone System
EMF_PHONE_API_URL=http://sip2.ix1.inferno.tel:3000
EMF_PHONE_API_KEY=
```

---

## Message text

Inline in adapter (no shared lib change):

```python
URGENCY_WORDS = {"low": "low priority", "medium": "medium priority", "high": "high priority", "urgent": "urgent"}

def _build_message(alert: CaseAlert) -> str:
    urgency = URGENCY_WORDS.get(alert.urgency, alert.urgency)
    spoken_id = alert.friendly_id.replace("-", " ")
    location = f" Location: {alert.location_hint}." if alert.location_hint else ""
    return f"New {urgency} conduct case. Case reference: {spoken_id}.{location}"
```

---

## Result handling summary

| API result | Adapter action |
|---|---|
| `ACKNOWLEDGE` | `_trigger_ack()` then return `"ACKNOWLEDGE:{number}"` |
| `SKIP` | Log, try next target |
| `NO-ANSWER` | Log, try next target |
| `HANGUP` | Log, try next target |
| `NO-INPUT` | Log, try next target |
| All targets exhausted | Return `None` → `_send_with_retry` retries at 5/10/15 min |

---

## Out of scope (this PR)

- Jambonz retirement (`apps/jambonz/` + compose service) — separate branch
- Multi-event target lists (current impl uses `cfg.events[0]` like existing code)
- Outbound ACK confirmation on phone channel (no mechanism for it)
