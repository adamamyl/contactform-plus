from __future__ import annotations

import asyncio
import logging

from emf_shared.tracing import (
    TRACE_HEADER,
    get_trace_id,
    new_trace_id,
    outbound_headers,
    set_trace_id,
)


def test_new_trace_id_is_hex_32_chars() -> None:
    tid = new_trace_id()
    assert len(tid) == 32
    assert all(c in "0123456789abcdef" for c in tid)


def test_new_trace_id_is_unique() -> None:
    assert new_trace_id() != new_trace_id()


def test_set_and_get_trace_id() -> None:
    set_trace_id("abc123")
    assert get_trace_id() == "abc123"


def test_outbound_headers_when_set() -> None:
    set_trace_id("deadbeef")
    h = outbound_headers()
    assert h == {TRACE_HEADER: "deadbeef"}


def test_outbound_headers_empty_when_not_set() -> None:
    set_trace_id("")
    h = outbound_headers()
    assert h == {}


def test_context_var_is_task_local() -> None:
    """Each asyncio task should have its own trace_id."""

    async def _run() -> None:
        results: list[str] = []

        async def task_a() -> None:
            set_trace_id("aaa")
            await asyncio.sleep(0)
            results.append(get_trace_id())

        async def task_b() -> None:
            set_trace_id("bbb")
            await asyncio.sleep(0)
            results.append(get_trace_id())

        set_trace_id("")
        await asyncio.gather(
            asyncio.create_task(task_a()),
            asyncio.create_task(task_b()),
        )
        assert sorted(results) == ["aaa", "bbb"]

    asyncio.run(_run())


def test_child_task_inherits_parent_trace_id() -> None:
    """asyncio.create_task copies the current context, so child inherits parent trace_id."""

    async def _run() -> None:
        set_trace_id("parentid")
        inherited: list[str] = []

        async def child() -> None:
            inherited.append(get_trace_id())

        await asyncio.create_task(child())
        assert inherited == ["parentid"]

    asyncio.run(_run())


class TestTraceFilter:
    def test_injects_trace_id_into_log_record(self) -> None:
        from emf_shared.logging import configure_logging

        configure_logging("test-service")
        set_trace_id("traceme")

        record = logging.LogRecord(
            name="test.tracing",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        root = logging.getLogger()
        for f in root.handlers[0].filters:
            f.filter(record)

        assert record.trace_id == "traceme"
        assert record.service == "test-service"

    def test_trace_id_defaults_to_dash_when_unset(self) -> None:
        from emf_shared.logging import configure_logging

        configure_logging("svc")
        set_trace_id("")
        record = logging.LogRecord(
            name="x",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="",
            args=(),
            exc_info=None,
        )
        root = logging.getLogger()
        for f in root.handlers[0].filters:
            f.filter(record)

        assert record.trace_id == "-"
