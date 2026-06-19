"""Tests for HTTP batch handler."""

import json
import logging
import threading
import urllib.request
from typing import override
from unittest.mock import MagicMock, patch

import pytest
import structlog.testing
from structlog.stdlib import ProcessorFormatter

from synthorg.observability._sync_backoff import backoff_delay
from synthorg.observability.config import SinkConfig
from synthorg.observability.enums import LogLevel, SinkType
from synthorg.observability.events.metrics import METRICS_LOG_SINK_CALLBACK_ERROR
from synthorg.observability.http_handler import HttpBatchHandler, build_http_handler


class _RaisingFormatter(logging.Formatter):
    """Formatter whose format() always raises (forces a format-drop)."""

    @override
    def format(self, record: logging.LogRecord) -> str:
        msg = "format boom"
        raise RuntimeError(msg)


def _make_record(msg: str = "test message") -> logging.LogRecord:
    return logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )


class _JsonFormatter(logging.Formatter):
    """Minimal JSON formatter for test handlers."""

    @override
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({"event": record.getMessage()})


def _make_handler(
    *,
    batch_size: int = 5,
    flush_interval: float = 60.0,
    timeout: float = 5.0,
    max_retries: int = 3,
) -> HttpBatchHandler:
    """Create a handler with a long flush interval (manual flush only)."""
    handler = HttpBatchHandler(
        url="https://logs.example.com/ingest",
        batch_size=batch_size,
        flush_interval=flush_interval,
        timeout=timeout,
        max_retries=max_retries,
    )
    handler.setFormatter(_JsonFormatter())
    return handler


@pytest.mark.unit
class TestHttpBatchHandler:
    """Tests for HttpBatchHandler core behavior."""

    def test_emit_queues_record(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        handler = _make_handler()

        with patch("urllib.request.urlopen"):
            handler.emit(_make_record())
            # Record is queued (not yet flushed since batch_size=5)
            assert handler._queue.qsize() >= 1
            handler.close()  # Close inside patch to avoid real network calls

    def test_negative_max_retries_rejected(self) -> None:
        """A negative ``max_retries`` would run zero send attempts and
        report an unsent batch as success; construction must reject it."""
        with pytest.raises(ValueError, match="max_retries"):
            HttpBatchHandler(url="https://logs.example.com/ingest", max_retries=-1)

    def test_emit_from_flusher_thread_is_dropped(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        """A record produced from the flusher thread (the handler's own
        export-failure diagnostic) is dropped rather than requeued, so a
        sustained collector outage cannot feed an unbounded loop."""
        handler = _make_handler()
        handler_cleanup.append(handler)

        captured: dict[str, int] = {}

        def _emit_on_flusher_thread() -> None:
            handler.emit(_make_record("self-generated diagnostic"))
            captured["qsize"] = handler._queue.qsize()
            captured["pending"] = handler._pending_count

        worker = threading.Thread(
            target=_emit_on_flusher_thread,
            name="log-http-flusher",
        )
        worker.start()
        worker.join()

        assert captured["qsize"] == 0
        assert captured["pending"] == 0

    def test_batch_flushed_on_batch_size(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        handler = _make_handler(batch_size=3)
        handler_cleanup.append(handler)
        flushed = threading.Event()

        def _mock_urlopen(*args: object, **kwargs: object) -> MagicMock:
            flushed.set()
            return MagicMock()

        with patch(
            "urllib.request.urlopen",
            side_effect=_mock_urlopen,
        ) as mock_urlopen:
            for _ in range(3):
                handler.emit(_make_record())
            assert flushed.wait(timeout=2.0), "Flusher did not fire"

        assert mock_urlopen.call_count >= 1

    def test_flush_on_close(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        handler = _make_handler(batch_size=100)
        # Don't add to cleanup since we're closing manually

        with patch("urllib.request.urlopen") as mock_urlopen:
            handler.emit(_make_record("close-test"))
            handler.close()

        # close() should flush remaining records
        assert mock_urlopen.call_count >= 1

    def test_flush_sends_json_array(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        handler = _make_handler(batch_size=100)

        with patch("urllib.request.urlopen") as mock_urlopen:
            handler.emit(_make_record("json-test"))
            handler.close()

        assert mock_urlopen.call_count >= 1
        request = mock_urlopen.call_args[0][0]
        body = json.loads(request.data.decode("utf-8"))
        assert isinstance(body, list)
        assert len(body) >= 1
        for item in body:
            assert isinstance(item, dict), f"Expected dict, got {type(item)}"
            assert "event" in item

    def test_timeout_applied(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        handler = _make_handler(batch_size=100, timeout=7.5)

        with patch("urllib.request.urlopen") as mock_urlopen:
            handler.emit(_make_record())
            handler.close()

        assert mock_urlopen.call_count >= 1
        call_kwargs = mock_urlopen.call_args
        assert call_kwargs[1].get("timeout") == 7.5

    def test_custom_headers_applied(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        handler = HttpBatchHandler(
            url="https://example.com/logs",
            headers=(("Authorization", "Bearer test-token"),),
            batch_size=100,
            flush_interval=60.0,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler_cleanup.append(handler)

        with patch("urllib.request.urlopen") as mock_urlopen:
            handler.emit(_make_record())
            handler.close()

        assert mock_urlopen.call_count >= 1
        request = mock_urlopen.call_args[0][0]
        assert request.get_header("Authorization") == "Bearer test-token"
        assert request.get_header("Content-type") == "application/json"

    def test_retry_on_failure(
        self,
        handler_cleanup: list[logging.Handler],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        handler = _make_handler(batch_size=100, max_retries=2)
        monkeypatch.setattr(
            "synthorg.observability.http_handler.backoff_delay",
            lambda _attempt: 0.0,
        )

        error = OSError("connection refused")
        with patch(
            "urllib.request.urlopen",
            side_effect=[error, error, MagicMock()],
        ) as mock_urlopen:
            handler.emit(_make_record())
            handler.close()

        # Should have retried: initial + 2 retries = 3 calls
        assert mock_urlopen.call_count == 3

    def test_max_retries_exhausted_drops_batch(
        self,
        handler_cleanup: list[logging.Handler],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        handler = _make_handler(batch_size=100, max_retries=1)
        monkeypatch.setattr(
            "synthorg.observability.http_handler.backoff_delay",
            lambda _attempt: 0.0,
        )

        error = OSError("connection refused")
        with patch(
            "urllib.request.urlopen",
            side_effect=error,
        ):
            handler.emit(_make_record())
            # Should not raise even after exhausting retries
            handler.close()

    def test_daemon_thread(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        handler = _make_handler()
        handler_cleanup.append(handler)
        assert handler._flusher.daemon is True

    def test_empty_queue_no_http_call(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        handler = _make_handler(batch_size=100)

        with patch("urllib.request.urlopen") as mock_urlopen:
            handler.close()

        # No records emitted, no HTTP call
        assert mock_urlopen.call_count == 0

    def test_max_retries_tracks_dropped_count(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        handler = _make_handler(batch_size=100, max_retries=0)

        error = OSError("connection refused")
        with patch("urllib.request.urlopen", side_effect=error):
            handler.emit(_make_record())
            handler.close()

        assert handler._dropped_count >= 1


@pytest.mark.unit
class TestBuildHttpHandler:
    """Tests for build_http_handler factory."""

    def test_returns_http_batch_handler(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        sink = SinkConfig(
            sink_type=SinkType.HTTP,
            http_url="https://logs.example.com/ingest",
        )
        handler = build_http_handler(sink, foreign_pre_chain=[])
        handler_cleanup.append(handler)
        assert isinstance(handler, HttpBatchHandler)

    def test_handler_level_set(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        sink = SinkConfig(
            sink_type=SinkType.HTTP,
            http_url="https://logs.example.com/ingest",
            level=LogLevel.ERROR,
        )
        handler = build_http_handler(sink, foreign_pre_chain=[])
        handler_cleanup.append(handler)
        assert handler.level == logging.ERROR

    def test_formatter_attached(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        sink = SinkConfig(
            sink_type=SinkType.HTTP,
            http_url="https://logs.example.com/ingest",
        )
        handler = build_http_handler(sink, foreign_pre_chain=[])
        handler_cleanup.append(handler)
        assert isinstance(handler.formatter, ProcessorFormatter)

    def test_missing_url_raises(self) -> None:
        """build_http_handler rejects empty http_url."""
        sink = SinkConfig(
            sink_type=SinkType.HTTP,
            http_url="https://placeholder.example.com",
        )
        # Bypass SinkConfig validation to force empty url
        object.__setattr__(sink, "http_url", "")
        with pytest.raises(ValueError, match="non-empty http_url"):
            build_http_handler(sink, foreign_pre_chain=[])


@pytest.mark.unit
class TestHttpExportCallback:
    """The export-outcome callback drives the Prometheus drop counter."""

    def test_success_invokes_callback(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        handler = _make_handler(batch_size=100)
        outcomes: list[tuple[str, int]] = []
        handler.set_export_callback(lambda o, d: outcomes.append((o, d)))

        with patch("urllib.request.urlopen"):
            handler.emit(_make_record("ok"))
            handler.close()

        assert ("success", 0) in outcomes

    def test_failure_invokes_callback_with_drop_count(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        handler = _make_handler(batch_size=100, max_retries=0)
        outcomes: list[tuple[str, int]] = []
        handler.set_export_callback(lambda o, d: outcomes.append((o, d)))

        with patch("urllib.request.urlopen", side_effect=OSError("refused")):
            handler.emit(_make_record("boom"))
            handler.close()

        assert ("failure", 1) in outcomes

    def test_set_export_callback_rejects_non_callable(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        handler = _make_handler()
        handler_cleanup.append(handler)
        with pytest.raises(TypeError, match="callable or None"):
            handler.set_export_callback(42)  # type: ignore[arg-type]

    def test_pure_format_failure_invokes_failure_callback_with_drop_count(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        """A batch where every record fails to format reports the drop count."""
        handler = _make_handler(batch_size=100)
        handler.setFormatter(_RaisingFormatter())
        handler_cleanup.append(handler)
        outcomes: list[tuple[str, int]] = []
        handler.set_export_callback(lambda o, d: outcomes.append((o, d)))

        with patch("urllib.request.urlopen") as mock_urlopen:
            handler._post_batch([_make_record("a"), _make_record("b")])

        assert mock_urlopen.call_count == 0  # nothing formatted, no POST
        assert outcomes == [("failure", 2)]

    def test_invoke_export_callback_swallows_non_critical_exception(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        """A throwing callback is logged, not propagated."""
        handler = _make_handler()
        handler_cleanup.append(handler)

        def _boom(_outcome: str, _dropped: int) -> None:
            msg = "callback boom"
            raise RuntimeError(msg)

        handler.set_export_callback(_boom)
        with structlog.testing.capture_logs() as logs:
            handler._invoke_export_callback("success", 0)

        errors = [
            rec
            for rec in logs
            if rec.get("event") == METRICS_LOG_SINK_CALLBACK_ERROR
            and rec.get("sink") == "http"
        ]
        assert errors, "a throwing callback must log METRICS_LOG_SINK_CALLBACK_ERROR"
        assert errors[0].get("error_type") == "RuntimeError"


@pytest.mark.unit
class TestHttpBackoff:
    """Inter-attempt backoff is bounded exponential (Pattern C/Sync)."""

    def test_backoff_delay_is_bounded_exponential(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        handler = _make_handler()
        handler_cleanup.append(handler)
        # base 0.5, factor 2, cap 8.0: 0.5, 1, 2, 4, 8, then capped.
        delays = [backoff_delay(attempt) for attempt in range(6)]
        assert delays == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0]

    def test_retries_complete_during_shutdown_drain(
        self,
        handler_cleanup: list[logging.Handler],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Retries run to completion even with shutdown set.

        ``close()`` sets ``_shutdown`` before its final drain, so an
        interruptible backoff would abandon the very logs close() is
        trying to ship. A single batch's retries must complete (bounded
        by close()'s join timeout).
        """
        handler = _make_handler(batch_size=100, max_retries=2)
        handler_cleanup.append(handler)
        monkeypatch.setattr(
            "synthorg.observability.http_handler.backoff_delay",
            lambda _attempt: 0.0,
        )
        handler._shutdown.set()
        with patch(
            "urllib.request.urlopen",
            side_effect=OSError("refused"),
        ) as mock_urlopen:
            error = handler._send_with_retries(
                urllib.request.Request(
                    "https://logs.example.com",
                    data=b"[]",
                    method="POST",
                ),
            )
        # Initial attempt + 2 retries despite shutdown being set.
        assert mock_urlopen.call_count == 3
        assert isinstance(error, OSError)
