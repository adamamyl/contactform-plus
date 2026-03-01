from __future__ import annotations

import asyncio
import logging

from jambonz.adapter import CaseAlert, JambonzAdapter

log = logging.getLogger(__name__)

ESCALATION_SEQUENCE: list[tuple[str, int]] = [
    # (label, delay_seconds_before_this_step)
    ("call_group", 0),
    ("shift_leader", 5 * 60),
    ("escalation_number", 10 * 60),
]


async def wait_for_ack(
    case_id: str,
    check_fn: "object",
    poll_interval: float = 10.0,
    timeout: float = 300.0,
) -> bool:
    """Poll check_fn(case_id) until it returns True or timeout expires."""
    from collections.abc import Awaitable, Callable

    fn: Callable[[str], Awaitable[bool]] = check_fn  # type: ignore[assignment]
    elapsed = 0.0
    while elapsed < timeout:
        if await fn(case_id):
            return True
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    return False


async def escalating_call(
    adapter: JambonzAdapter,
    alert: CaseAlert,
    phone_numbers: dict[str, str],
    is_acked: "object",
) -> None:
    """
    Try each number in ESCALATION_SEQUENCE with increasing delays.
    Stops as soon as the case is acknowledged.
    phone_numbers: mapping of label → E.164 number
    is_acked: async callable(case_id) → bool
    """
    from collections.abc import Awaitable, Callable

    acked_fn: Callable[[str], Awaitable[bool]] = is_acked  # type: ignore[assignment]

    for label, delay_seconds in ESCALATION_SEQUENCE:
        if delay_seconds > 0:
            log.info(
                "Escalation: waiting %ds before calling %s for case %s",
                delay_seconds,
                label,
                alert.case_id,
            )
            await asyncio.sleep(delay_seconds)

        if await acked_fn(alert.case_id):
            log.info("Case %s acknowledged before %s step", alert.case_id, label)
            return

        number = phone_numbers.get(label)
        if not number:
            log.warning("No phone number for escalation step %s", label)
            continue

        log.info("Calling %s (%s) for case %s", label, number, alert.case_id)
        call_sid = await adapter.call(number, alert)

        if call_sid:
            acked = await wait_for_ack(alert.case_id, acked_fn)
            if acked:
                log.info("Case %s acknowledged after %s call", alert.case_id, label)
                return
        else:
            log.warning("Call to %s failed for case %s", label, alert.case_id)

    log.error(
        "🚨 Full escalation sequence exhausted without ACK for case %s", alert.case_id
    )
