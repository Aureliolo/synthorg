"""Tests for HTTP batch handler."""

import json
import logging
import threading
from typing import Any, override
from unittest.mock import MagicMock, patch

import pytest
from structlog.stdlib import ProcessorFormatter

from synthorg.observability.config import SinkConfig
from synthorg.observability.enums import LogLevel, SinkType
from synthorg.observability.http_handler import HttpBatchHandler, build_http_handler


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

    def test_batch_flushed_on_batch_size(
        self,
        handler_cleanup: list[logging.Handler],
    ) -> None:
        handler = _make_handler(batch_size=3)
        handler_cleanup.append(handler)
        flushed = threading.Event()

        def _mock_urlopen(*args: Any, **kwargs: Any) -> MagicMock:
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
    ) -> None:
        handler = _make_handler(batch_size=100, max_retries=2)

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
    ) -> None:
        handler = _make_handler(batch_size=100, max_retries=1)

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
