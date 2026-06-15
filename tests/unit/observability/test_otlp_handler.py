"""Tests for the OTLP log handler."""

import json
import logging
import queue
import urllib.error
from typing import override
from unittest.mock import MagicMock, patch

import pytest
import structlog.testing
from pydantic import JsonValue

from synthorg.observability.config import SinkConfig
from synthorg.observability.enums import OtlpProtocol, SinkType
from synthorg.observability.events.metrics import METRICS_OTLP_FLUSHER_ERROR
from synthorg.observability.otlp_handler import OtlpHandler, build_otlp_handler


def _make_record(
    msg: str = "test message",
    *,
    request_id: str | None = None,
    task_id: str | None = None,
    agent_id: str | None = None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )
    if request_id is not None:
        record.request_id = request_id
    if task_id is not None:
        record.task_id = task_id
    if agent_id is not None:
        record.agent_id = agent_id
    return record


class _JsonFormatter(logging.Formatter):
    """Minimal JSON formatter for test handlers."""

    @override
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, JsonValue] = {"event": record.getMessage()}
        for key in ("request_id", "task_id", "agent_id"):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        return json.dumps(data)


def _make_handler(
    *,
    batch_size: int = 5,
    flush_interval: float = 60.0,
    start_flusher: bool = False,
    timeout: float = 0.1,
) -> OtlpHandler:
    """Create a handler with no background flusher (deterministic tests).

    When ``start_flusher`` is True, a higher timeout floor is
    enforced so the flusher thread has time for a clean shutdown.
    """
    effective_timeout = max(timeout, 1.0) if start_flusher else timeout
    handler = OtlpHandler(
        endpoint="http://localhost:4318",
        batch_size=batch_size,
        flush_interval=flush_interval,
        timeout=effective_timeout,
        _start_flusher=start_flusher,
    )
    handler.setFormatter(_JsonFormatter())
    return handler


@pytest.mark.unit
class TestOtlpHandler:
    """Tests for OtlpHandler core behavior."""

    def test_emit_queues_record(self) -> None:
        handler = _make_handler()
        try:
            handler.emit(_make_record())
            with handler._pending_lock:
                assert handler._pending_count == 1
        finally:
            handler.close()

    def test_batch_ready_signal(self) -> None:
        handler = _make_handler(batch_size=2)
        try:
            handler.emit(_make_record("first"))
            assert not handler._batch_ready.is_set()
            handler.emit(_make_record("second"))
            assert handler._batch_ready.is_set()
        finally:
            handler.close()

    def test_close_signals_shutdown(self) -> None:
        handler = _make_handler(start_flusher=True)
        handler.close()
        assert handler._shutdown.is_set()
        assert not handler._flusher.is_alive()

    def test_negative_max_retries_rejected(self) -> None:
        """A negative ``max_retries`` would run zero attempts and report
        an unsent batch as success; construction must reject it."""
        with pytest.raises(ValueError, match="max_retries"):
            OtlpHandler(
                endpoint="http://localhost:4318",
                max_retries=-1,
                _start_flusher=False,
            )

    def test_drain_collects_records(self) -> None:
        handler = _make_handler()
        try:
            handler.emit(_make_record("one"))
            handler.emit(_make_record("two"))

            records: list[logging.LogRecord] = []
            while True:
                try:
                    records.append(handler._queue.get_nowait())
                except queue.Empty:
                    break
            assert len(records) == 2
        finally:
            handler.close()

    def test_format_record_as_otlp_dict(self) -> None:
        handler = _make_handler()
        try:
            record = _make_record(
                "test event",
                request_id="req-123",
                task_id="task-456",
                agent_id="agent-789",
            )
            handler.setFormatter(_JsonFormatter())
            # Round-trip through JSON so the heterogeneous OTLP dict (typed
            # ``dict[str, object]`` at the source) is navigable in assertions.
            result = json.loads(json.dumps(handler._format_as_otlp_dict(record)))
            # Body is OTLP AnyValue with stringValue from self.format()
            body = json.loads(result["body"]["stringValue"])
            assert body["event"] == "test event"
            # Attributes are OTLP KeyValue array
            attr_map = {
                a["key"]: a["value"]["stringValue"] for a in result["attributes"]
            }
            assert attr_map["request_id"] == "req-123"
            assert attr_map["task_id"] == "task-456"
            assert attr_map["agent_id"] == "agent-789"
            assert result["severityText"] == "INFO"
            assert isinstance(result["timeUnixNano"], str)
        finally:
            handler.close()

    def test_format_record_without_correlation_ids(self) -> None:
        handler = _make_handler()
        try:
            record = _make_record("plain event")
            result = json.loads(json.dumps(handler._format_as_otlp_dict(record)))
            body = json.loads(result["body"]["stringValue"])
            assert body["event"] == "plain event"
            attr_keys = {a["key"] for a in result["attributes"]}
            assert "request_id" not in attr_keys
        finally:
            handler.close()


@pytest.mark.unit
class TestOtlpHandlerProtocol:
    """Tests for OTLP protocol configuration."""

    def test_default_protocol_is_http(self) -> None:
        handler = OtlpHandler(
            endpoint="http://localhost:4318",
        )
        try:
            assert handler._protocol == OtlpProtocol.HTTP_JSON
        finally:
            handler.close()

    def test_grpc_protocol_rejects_with_not_implemented(self) -> None:
        with pytest.raises(
            NotImplementedError,
            match="gRPC transport is not implemented",
        ):
            OtlpHandler(
                endpoint="http://localhost:4317",
                protocol=OtlpProtocol.GRPC,
            )


@pytest.mark.unit
class TestBuildOtlpHandler:
    """Tests for the build_otlp_handler factory function."""

    def test_builds_from_valid_config(self) -> None:
        sink = SinkConfig(
            sink_type=SinkType.OTLP,
            otlp_endpoint="http://localhost:4318",
        )
        handler = build_otlp_handler(sink, [])
        try:
            assert isinstance(handler, OtlpHandler)
            assert handler._endpoint == "http://localhost:4318"
        finally:
            handler.close()

    def test_build_with_grpc_protocol_rejected_at_config(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(
            ValidationError,
            match="gRPC transport is not supported",
        ):
            SinkConfig(
                sink_type=SinkType.OTLP,
                otlp_endpoint="http://localhost:4317",
                otlp_protocol=OtlpProtocol.GRPC,
            )

    def test_uses_config_batch_size_and_timeout(self) -> None:
        sink = SinkConfig(
            sink_type=SinkType.OTLP,
            otlp_endpoint="http://localhost:4318",
            otlp_batch_size=50,
            otlp_timeout_seconds=30.0,
        )
        handler = build_otlp_handler(sink, [])
        try:
            assert handler._batch_size == 50
            assert handler._timeout == 30.0
        finally:
            handler.close()

    def test_rejects_missing_endpoint(self) -> None:
        sink = MagicMock()
        sink.otlp_endpoint = None
        with pytest.raises(ValueError, match="non-empty otlp_endpoint"):
            build_otlp_handler(sink, [])


@pytest.mark.unit
class TestOtlpHandlerExportFailure:
    """Tests for export batch error handling."""

    def test_export_failure_increments_dropped_count(self) -> None:
        handler = _make_handler(batch_size=1)
        try:
            handler.emit(_make_record("will fail"))
            # Manually drain and try to export with a stubbed urlopen
            records: list[logging.LogRecord] = []
            while True:
                try:
                    records.append(handler._queue.get_nowait())
                except queue.Empty:
                    break
            with patch(
                "synthorg.observability.otlp_handler.urllib.request.urlopen",
                side_effect=ConnectionError("stubbed network failure"),
            ):
                if records:
                    handler._export_batch(records)
            with handler._pending_lock:
                assert handler._dropped_count > 0
        finally:
            handler.close()

    def test_close_always_drains_remaining_records(self) -> None:
        handler = _make_handler(batch_size=100, start_flusher=True)
        try:
            handler.emit(_make_record("one"))
            handler.emit(_make_record("two"))
        finally:
            with patch(
                "synthorg.observability.otlp_handler.urllib.request.urlopen",
                side_effect=ConnectionError("stubbed"),
            ):
                handler.close()
        # After close, queue should be empty
        assert handler._queue.empty()


@pytest.mark.unit
class TestOtlpHandlerInternalErrorPaths:
    """Internal failures route through ``handleError`` / are logged
    without propagating out of the logging hot path."""

    def test_enqueue_queue_failure_calls_handle_error(self) -> None:
        handler = _make_handler()
        try:
            failing_queue = MagicMock()
            failing_queue.put_nowait.side_effect = RuntimeError("queue boom")
            handler._queue = failing_queue
            handled: list[logging.LogRecord] = []
            handler.handleError = handled.append  # type: ignore[method-assign,assignment]

            handler._enqueue(_make_record("drop me"))

            assert len(handled) == 1
        finally:
            # Restore a real, empty queue so close()'s drain terminates.
            handler._queue = queue.SimpleQueue()
            handler.close()

    def test_export_batch_format_failure_increments_dropped(self) -> None:
        handler = _make_handler(batch_size=1)
        try:
            handled: list[logging.LogRecord] = []
            handler.handleError = handled.append  # type: ignore[method-assign,assignment]

            def _boom_format(_record: logging.LogRecord) -> dict[str, object]:
                msg = "format boom"
                raise RuntimeError(msg)

            handler._format_as_otlp_dict = _boom_format  # type: ignore[method-assign,assignment]

            handler._export_batch([_make_record("bad")])

            assert len(handled) == 1
            with handler._pending_lock:
                assert handler._dropped_count == 1
        finally:
            handler.close()

    def test_flush_loop_drain_failure_is_logged(self) -> None:
        handler = _make_handler()
        try:
            calls = {"n": 0}

            def _boom_drain() -> None:
                calls["n"] += 1
                # Stop after one iteration so the loop exits cleanly.
                handler._shutdown.set()
                msg = "drain boom"
                raise RuntimeError(msg)

            handler._drain_and_flush = _boom_drain  # type: ignore[method-assign]
            handler._batch_ready.set()

            # One iteration: drain raises, the except logs, and the
            # shutdown flag set above ends the loop.
            with structlog.testing.capture_logs() as logs:
                handler._flush_loop()

            assert calls["n"] == 1
            flusher_errors = [
                rec for rec in logs if rec.get("event") == METRICS_OTLP_FLUSHER_ERROR
            ]
            assert flusher_errors, "flusher drain failure must log redacted context"
            assert flusher_errors[0].get("error_type") == "RuntimeError"
            assert flusher_errors[0].get("error")
            assert "pending_records" in flusher_errors[0]
            # Neutralise the raising drain so close()'s own drain is a
            # no-op rather than re-raising during teardown.
            handler._drain_and_flush = lambda: None  # type: ignore[method-assign]
        finally:
            handler.close()


@pytest.mark.unit
class TestOtlpExportRetry:
    """OtlpHandler retries transient export failures (Pattern C/Sync)."""

    @staticmethod
    def _no_backoff(handler: OtlpHandler) -> None:
        # Avoid real backoff sleeps in the retry loop.
        handler._backoff_delay = lambda attempt: 0.0  # type: ignore[method-assign]

    def test_export_retries_then_succeeds(self) -> None:
        handler = _make_handler(batch_size=100, timeout=1.0)
        self._no_backoff(handler)
        try:
            error = OSError("connection refused")
            with patch(
                "urllib.request.urlopen",
                side_effect=[error, error, MagicMock()],
            ) as mock_urlopen:
                handler._export_batch([_make_record("retry")])
            assert mock_urlopen.call_count == 3  # initial + 2 retries
            with handler._pending_lock:
                assert handler._dropped_count == 0
        finally:
            handler.close()

    def test_export_exhausts_retries_and_drops(self) -> None:
        handler = _make_handler(batch_size=100, timeout=1.0)
        handler._max_retries = 1
        self._no_backoff(handler)
        outcomes: list[tuple[str, int]] = []
        handler.set_export_callback(lambda o, d: outcomes.append((o, d)))
        try:
            with patch(
                "urllib.request.urlopen",
                side_effect=OSError("down"),
            ) as mock_urlopen:
                handler._export_batch([_make_record("boom")])
            assert mock_urlopen.call_count == 2  # initial + 1 retry
            with handler._pending_lock:
                assert handler._dropped_count == 1
            assert ("failure", 1) in outcomes
        finally:
            handler.close()

    def test_export_4xx_not_retried(self) -> None:
        handler = _make_handler(batch_size=100, timeout=1.0)
        self._no_backoff(handler)
        try:
            http_error = urllib.error.HTTPError(
                url="http://localhost:4318/v1/logs",
                code=400,
                msg="Bad Request",
                hdrs=None,  # type: ignore[arg-type]
                fp=None,
            )
            with patch(
                "urllib.request.urlopen",
                side_effect=http_error,
            ) as mock_urlopen:
                handler._export_batch([_make_record("bad")])
            assert mock_urlopen.call_count == 1  # 4xx is non-retryable
        finally:
            handler.close()
