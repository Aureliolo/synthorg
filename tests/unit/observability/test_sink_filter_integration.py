"""Integration tests for sink-level filtering (#1666 A-1 / A-2).

The unit tests in ``test_sink_routing.py`` cover the filter classes
in isolation. These tests boot the full handler chain with the new
``SINK_EVENT_EXCLUDES`` and ``SINK_EXACT_LEVELS`` tables wired in,
emit real records via :func:`logging.getLogger`, then read the
on-disk file output to confirm the filters actually drop the
correct records before they hit the disk sink. A regression where
``_attach_formatter_and_routing()`` forgets to attach a filter is
caught here even though the unit tests would still pass.
"""

import logging
from pathlib import Path
from typing import Any

import pytest

from synthorg.observability.config import RotationConfig, SinkConfig
from synthorg.observability.enums import LogLevel, SinkType
from synthorg.observability.sinks import build_handler


def _foreign_pre_chain() -> list[Any]:
    """Minimal processor chain so the JSON renderer has level + event."""
    import structlog

    return [
        structlog.stdlib.add_log_level,
    ]


def _emit_structlog_record(
    handler: logging.Handler,
    *,
    name: str,
    level: int,
    event: str,
    **kwargs: object,
) -> None:
    """Build a LogRecord whose ``msg`` is a structlog-style event_dict.

    Production records reach the handler with ``record.msg`` populated
    by ``structlog.stdlib.ProcessorFormatter.wrap_for_formatter``; this
    test helper bypasses structlog and constructs the dict-shape
    record directly so the filter chain is exercised on the same
    inputs it sees in production.
    """
    msg: dict[str, object] = {
        "event": event,
        "level": logging.getLevelName(level).lower(),
    }
    msg.update(kwargs)
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname="",
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )
    handler.handle(record)


def _read_lines(path: Path) -> list[str]:
    """Read raw lines from a log file. Empty file -> ``[]``.

    The integration test bypasses structlog's formatter chain (the
    handler-builder wires the filters before the formatter ever
    runs), so the on-disk lines are the str-repr of the event_dict
    rather than canonical JSON. Lines are still distinct per record;
    substring matching is enough for the filter-pass / filter-drop
    contract these tests assert.
    """
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    return [line for line in raw.splitlines() if line.strip()]


def _count_event(lines: list[str], event: str) -> int:
    """Count lines whose serialised event_dict carries ``event=<name>``."""
    return sum(1 for line in lines if f"'event': '{event}'" in line)


@pytest.mark.integration
class TestSynthorgLogExcludesRequestLifecycle:
    """Issue #1666 A-1: ``synthorg.log`` does not collect request events.

    ``api.request.started`` and ``api.request.completed`` already land
    in ``access.log`` via the logger-name include filter. Letting them
    flood the catch-all main log buries 96% of every other event under
    request-lifecycle noise. ``SINK_EVENT_EXCLUDES`` tells the
    handler-builder to attach an ``_EventNameFilter`` that drops them
    before they ever reach disk.
    """

    def test_main_log_drops_started_and_completed_events(
        self,
        tmp_path: Path,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        sink = SinkConfig(
            sink_type=SinkType.FILE,
            level=LogLevel.INFO,
            file_path="synthorg.log",
            rotation=RotationConfig(),
            json_format=True,
        )
        handler = build_handler(sink, tmp_path, _foreign_pre_chain())
        handler_cleanup.append(handler)

        # 5 lifecycle records that MUST be dropped + 2 keep-events.
        for _ in range(3):
            _emit_structlog_record(
                handler,
                name="synthorg.api.middleware",
                level=logging.INFO,
                event="api.request.started",
                method="GET",
                path="/api/v1/health",
            )
        for _ in range(2):
            _emit_structlog_record(
                handler,
                name="synthorg.api.middleware",
                level=logging.INFO,
                event="api.request.completed",
                method="GET",
                path="/api/v1/health",
                status_code=200,
                duration_ms=1.4,
            )
        # Same logger but a non-lifecycle event -- this MUST land in
        # synthorg.log (the filter is event-scoped, not logger-scoped).
        _emit_structlog_record(
            handler,
            name="synthorg.api.middleware",
            level=logging.WARNING,
            event="metrics.record.failed",
            component="api_request_duration",
        )
        # Different domain, INFO-level -- must land.
        _emit_structlog_record(
            handler,
            name="synthorg.engine.task",
            level=logging.INFO,
            event="task.run.started",
            task_id="t-1",
        )
        handler.flush()

        lines = _read_lines(tmp_path / "synthorg.log")
        # Lifecycle events dropped.
        assert _count_event(lines, "api.request.started") == 0
        assert _count_event(lines, "api.request.completed") == 0
        # Other events on the same logger pass through.
        assert _count_event(lines, "metrics.record.failed") == 1
        assert _count_event(lines, "task.run.started") == 1

    def test_access_log_keeps_lifecycle_events(
        self,
        tmp_path: Path,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        """Verify the lifecycle events DO land in ``access.log`` -- the
        complementary half of the contract."""
        sink = SinkConfig(
            sink_type=SinkType.FILE,
            level=LogLevel.INFO,
            file_path="access.log",
            rotation=RotationConfig(),
            json_format=True,
        )
        handler = build_handler(sink, tmp_path, _foreign_pre_chain())
        handler_cleanup.append(handler)

        for _ in range(2):
            _emit_structlog_record(
                handler,
                name="synthorg.api.middleware",
                level=logging.INFO,
                event="api.request.started",
                method="GET",
                path="/api/v1/health",
            )
        for _ in range(2):
            _emit_structlog_record(
                handler,
                name="synthorg.api.middleware",
                level=logging.INFO,
                event="api.request.completed",
                method="GET",
                path="/api/v1/health",
                status_code=200,
                duration_ms=1.4,
            )
        # An off-prefix logger MUST be excluded by the include filter.
        _emit_structlog_record(
            handler,
            name="synthorg.engine.task",
            level=logging.INFO,
            event="task.run.started",
        )
        handler.flush()

        lines = _read_lines(tmp_path / "access.log")
        # Both halves of the lifecycle land in access.log; the catch-all
        # main log is the one excluding them, not this sink.
        assert _count_event(lines, "api.request.started") == 2
        assert _count_event(lines, "api.request.completed") == 2
        # Non-api logger excluded by the access.log include filter.
        assert _count_event(lines, "task.run.started") == 0


@pytest.mark.integration
class TestDebugLogExactLevel:
    """Issue #1666 A-2: ``debug.log`` only collects DEBUG-level records.

    Pre-#1666 the sink had ``level=LogLevel.DEBUG`` (meaning "DEBUG and
    above"), so an INFO-quiet system left ``debug.log`` byte-identical
    to ``synthorg.log`` with 33,734 lines of INFO. The
    ``SINK_EXACT_LEVELS`` table now attaches an ``_ExactLevelFilter``
    that drops every level except DEBUG.
    """

    def test_debug_log_keeps_only_debug_records(
        self,
        tmp_path: Path,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        sink = SinkConfig(
            sink_type=SinkType.FILE,
            level=LogLevel.DEBUG,
            file_path="debug.log",
            rotation=RotationConfig(),
            json_format=True,
        )
        handler = build_handler(sink, tmp_path, _foreign_pre_chain())
        handler_cleanup.append(handler)

        for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR):
            _emit_structlog_record(
                handler,
                name="synthorg.engine.task",
                level=level,
                event=f"test.level.{logging.getLevelName(level).lower()}",
            )
        handler.flush()

        lines = _read_lines(tmp_path / "debug.log")
        # Only the DEBUG record landed in debug.log.
        assert _count_event(lines, "test.level.debug") == 1
        assert _count_event(lines, "test.level.info") == 0
        assert _count_event(lines, "test.level.warning") == 0
        assert _count_event(lines, "test.level.error") == 0

    def test_debug_log_empty_when_no_debug_records(
        self,
        tmp_path: Path,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        """With no DEBUG emissions, the file stays empty (no INFO leak)."""
        sink = SinkConfig(
            sink_type=SinkType.FILE,
            level=LogLevel.DEBUG,
            file_path="debug.log",
            rotation=RotationConfig(),
            json_format=True,
        )
        handler = build_handler(sink, tmp_path, _foreign_pre_chain())
        handler_cleanup.append(handler)

        for _ in range(5):
            _emit_structlog_record(
                handler,
                name="synthorg.engine.task",
                level=logging.INFO,
                event="task.run.started",
            )
        handler.flush()

        lines = _read_lines(tmp_path / "debug.log")
        assert lines == []
