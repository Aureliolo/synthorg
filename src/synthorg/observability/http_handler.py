"""HTTP batch handler for shipping structured logs via HTTP POST.

Batches log records in a thread-safe queue and POSTs them as JSON
arrays to a configurable URL using a background daemon thread.
Uses ``urllib.request`` (stdlib) to avoid external dependencies.
"""

import logging
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Final, override

import structlog
from structlog.stdlib import ProcessorFormatter
from structlog.typing import Processor

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger
from synthorg.observability.config import SinkConfig
from synthorg.observability.events.metrics import (
    METRICS_LOG_SINK_CALLBACK_ERROR,
    METRICS_LOG_SINK_EXPORT_FAILED,
    METRICS_LOG_SINK_FLUSHER_ERROR,
    METRICS_LOG_SINK_INVALID_CALLBACK,
)
from synthorg.observability.redaction import safe_error_description

if TYPE_CHECKING:
    from collections.abc import Callable

    # Stays TYPE_CHECKING: a runtime alias would let ``set_export_callback``
    # accept a non-callable before its own TypeError guard can fire.
    ExportCallback = Callable[[str, int], None]

_internal_logger = get_logger(__name__)

_DEFAULT_BATCH_SIZE: Final[int] = 100
_DEFAULT_FLUSH_INTERVAL_SECONDS: Final[float] = 5.0
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 10.0
_DEFAULT_MAX_RETRIES: Final[int] = 3

# Bounded exponential backoff between send attempts (Pattern C/Sync):
# delay(attempt) = min(base * factor**attempt, cap). The wait is
# non-interruptible so that retries run to completion during shutdown.
_RETRY_BACKOFF_BASE_SECONDS: Final[float] = 0.5
_RETRY_BACKOFF_FACTOR: Final[int] = 2
_RETRY_BACKOFF_CAP_SECONDS: Final[float] = 8.0

# Naming the flusher thread lets emit() drop records produced from the
# handler's own export-failure logging: that diagnostic propagates back
# through the root logger into this sink, and requeuing it would feed an
# unbounded loop under a sustained collector outage. Every record whose
# origin is this thread is dropped before it can re-enter the queue.
_FLUSHER_THREAD_NAME = "log-http-flusher"


class HttpBatchHandler(logging.Handler):
    """Handler that batches log records and POSTs them as JSON arrays.

    A background daemon thread periodically flushes the queue.  Records
    are also flushed when the batch size is reached or when the handler
    is closed.

    Args:
        url: HTTP endpoint to POST log batches to.
        headers: Extra HTTP headers as ``(name, value)`` pairs.
        batch_size: Number of records per POST batch.
        flush_interval: Seconds between automatic flushes.
        timeout: HTTP request timeout in seconds.
        max_retries: Number of retries on HTTP failure.
    """

    def __init__(  # noqa: PLR0913
        self,
        url: str,
        *,
        headers: tuple[tuple[str, str], ...] = (),
        batch_size: int = _DEFAULT_BATCH_SIZE,
        flush_interval: float = _DEFAULT_FLUSH_INTERVAL_SECONDS,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        super().__init__()
        if max_retries < 0:
            msg = "max_retries must be greater than or equal to 0"
            raise ValueError(msg)
        self._url = url
        self._extra_headers = dict(headers)
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._timeout = timeout
        self._max_retries = max_retries
        self._queue: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
        self._pending_count = 0
        self._pending_lock = threading.Lock()
        self._dropped_count = 0
        self._export_callback: ExportCallback | None = None
        self._shutdown = threading.Event()
        self._batch_ready = threading.Event()
        self._flusher = threading.Thread(
            target=self._flush_loop,
            daemon=True,
            name=_FLUSHER_THREAD_NAME,
        )
        self._flusher.start()

    def set_export_callback(self, callback: ExportCallback | None) -> None:
        """Register a callback invoked after every export batch.

        Passed ``(outcome, dropped_records)`` where ``outcome`` is
        ``"success"`` or ``"failure"`` and ``dropped_records`` is the
        number of records the batch failed to deliver (0 on success).
        Used by startup wiring to push
        :meth:`PrometheusCollector.record_log_sink_export` without
        coupling the handler to AppState.

        Thread safety: invoked from the flusher thread; the callback
        must be safe to call concurrently with ``emit``.

        Raises:
            TypeError: When ``callback`` is not callable (and not
                ``None``). Failing fast avoids surfacing the mistake
                only when the flusher thread eventually calls it.
        """
        candidate: object = callback
        if candidate is not None and not callable(candidate):
            _internal_logger.warning(
                METRICS_LOG_SINK_INVALID_CALLBACK,
                sink="http",
                provided_type=type(callback).__name__,
            )
            msg = "export callback must be callable or None"
            raise TypeError(msg)
        self._export_callback = callback

    def _invoke_export_callback(self, outcome: str, dropped: int) -> None:
        """Call the registered export callback, swallowing callback errors.

        A callback failure must never break the export loop; it is
        caught and emitted as a structured warning (with a redacted
        ``error`` description, never a traceback). :class:`MemoryError`
        and :class:`RecursionError` propagate.
        """
        callback = self._export_callback
        if callback is None:
            return
        try:
            callback(outcome, dropped)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            _internal_logger.warning(
                METRICS_LOG_SINK_CALLBACK_ERROR,
                sink="http",
                outcome=outcome,
                dropped_records=dropped,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    @override
    def emit(self, record: logging.LogRecord) -> None:
        """Queue a record for batched shipping.

        Records produced from the handler's own flusher thread are
        dropped to prevent a feedback loop: when the flusher logs an
        export failure, routing that log back through this handler would
        requeue it, growing the queue without bound under outage.
        """
        if threading.current_thread().name == _FLUSHER_THREAD_NAME:
            return
        try:
            self._queue.put_nowait(record)
            with self._pending_lock:
                self._pending_count += 1
                if self._pending_count >= self._batch_size:
                    self._batch_ready.set()
        except Exception as exc:  # noqa: BLE001 -- logging handler emit boundary
            reraise_critical(exc)
            self.handleError(record)

    def _flush_loop(self) -> None:
        """Background loop: flush on interval, batch-ready, or shutdown."""
        while not self._shutdown.is_set():
            # Wait for batch_ready or timeout (flush interval)
            self._batch_ready.wait(timeout=self._flush_interval)
            self._batch_ready.clear()
            if self._shutdown.is_set():
                break
            try:
                self._drain_and_flush()
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                with self._pending_lock:
                    pending = self._pending_count
                _internal_logger.error(
                    METRICS_LOG_SINK_FLUSHER_ERROR,
                    sink="http",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    pending_records=pending,
                )

    def _drain_and_flush(self) -> None:
        """Drain all queued records and POST as JSON batches."""
        records: list[logging.LogRecord] = []
        while True:
            try:
                records.append(self._queue.get_nowait())
            except queue.Empty:
                break

        with self._pending_lock:
            self._pending_count = max(
                0,
                self._pending_count - len(records),
            )

        for start in range(0, len(records), self._batch_size):
            batch = records[start : start + self._batch_size]
            if batch:
                self._post_batch(batch)

    def _post_batch(self, records: list[logging.LogRecord]) -> None:
        """POST a batch of records as a JSON array with retries."""
        entries: list[str] = []
        format_drops = 0
        for record in records:
            try:
                entries.append(self.format(record))
            except Exception:  # noqa: BLE001 -- logging handler boundary
                self.handleError(record)
                with self._pending_lock:
                    self._dropped_count += 1
                format_drops += 1

        if not entries:
            # Pure-formatting failure: still surface the drop so the
            # export-outcome callback reflects every lost record.
            if format_drops:
                self._invoke_export_callback("failure", format_drops)
            return

        body = f"[{','.join(entries)}]".encode()
        request = urllib.request.Request(  # noqa: S310
            self._url,
            data=body,
            method="POST",
        )
        request.add_header("Content-Type", "application/json")
        for name, value in self._extra_headers.items():
            request.add_header(name, value)

        error = self._send_with_retries(request)
        if error is not None:
            with self._pending_lock:
                self._dropped_count += len(entries)
                total_dropped = self._dropped_count
            _internal_logger.warning(
                METRICS_LOG_SINK_EXPORT_FAILED,
                sink="http",
                url=self._url,
                attempts=1 + self._max_retries,
                error_type=type(error).__name__,
                error=safe_error_description(error),
                dropped_records=len(entries),
                total_dropped=total_dropped,
            )
            self._invoke_export_callback("failure", format_drops + len(entries))
            return
        self._invoke_export_callback("success", format_drops)

    def _backoff_delay(self, attempt: int) -> float:
        """Bounded exponential backoff for retry *attempt* (0-indexed).

        Returns:
            Seconds to wait before the next attempt, capped at
            ``_RETRY_BACKOFF_CAP_SECONDS``.
        """
        delay = _RETRY_BACKOFF_BASE_SECONDS * float(_RETRY_BACKOFF_FACTOR**attempt)
        return min(delay, _RETRY_BACKOFF_CAP_SECONDS)

    def _send_with_retries(
        self,
        request: urllib.request.Request,
    ) -> Exception | None:
        """Attempt to send *request*, returning the last error or None.

        Returns:
            ``None`` when the send succeeds, or the last ``Exception``
            instance when every attempt failed.
        """
        last_error: Exception | None = None
        # See docs/reference/retry-patterns.md: Pattern C/Sync -- this
        # method runs inside a stdlib logging-handler thread using
        # synchronous urllib.request, so the async GeneralRetryHandler
        # cannot be awaited from here.
        for attempt in range(1 + self._max_retries):
            try:
                with urllib.request.urlopen(  # noqa: S310
                    request,
                    timeout=self._timeout,
                ):
                    pass  # Response body not needed
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                # HTTPError wraps a response FP -- close to avoid FD leak.
                if isinstance(exc, urllib.error.HTTPError):
                    exc.close()
                    # 4xx client errors are non-retryable.
                    if 400 <= exc.code < 500:  # noqa: PLR2004
                        return exc
                last_error = exc
                if attempt < self._max_retries:
                    # Bounded backoff before the next attempt. close()
                    # budgets backoff_total into its join timeout, so a
                    # single batch's retries run to completion rather than
                    # being dropped mid-flight: shutdown is always set
                    # during the final drain, so an interruptible wait here
                    # would abandon the very logs close() is trying to ship.
                    time.sleep(self._backoff_delay(attempt))
                    continue
            else:
                return None
        return last_error

    @override
    def close(self) -> None:
        """Signal shutdown, flush remaining records, stop thread."""
        self._shutdown.set()
        self._batch_ready.set()  # Wake the flusher
        # Allow enough time for in-flight retries to finish: worst case is
        # (1 + max_retries) attempts each up to ``timeout`` plus the
        # bounded backoff between them. Since the backoff uses time.sleep
        # and is non-interruptible, this is an upper bound.
        backoff_total = sum(
            self._backoff_delay(attempt) for attempt in range(self._max_retries)
        )
        join_timeout = (1 + self._max_retries) * self._timeout + backoff_total
        self._flusher.join(timeout=join_timeout)
        # Only drain from the calling thread if the flusher has exited.
        # If join() timed out the flusher may still be in _drain_and_flush,
        # and draining concurrently would race on the queue.
        if not self._flusher.is_alive():
            self._drain_and_flush()
        super().close()


def build_http_handler(
    sink: SinkConfig,
    foreign_pre_chain: list[Processor],
) -> HttpBatchHandler:
    """Build an HttpBatchHandler from an HTTP sink configuration.

    Args:
        sink: The HTTP sink configuration.
        foreign_pre_chain: Processor chain for stdlib-originated logs.

    Returns:
        A configured ``HttpBatchHandler`` with JSON formatting.

    Raises:
        ValueError: If ``sink.http_url`` is absent or empty.
    """
    if not sink.http_url:
        msg = "HTTP sink requires a non-empty http_url"
        raise ValueError(msg)
    handler = HttpBatchHandler(
        url=sink.http_url,
        headers=sink.http_headers,
        batch_size=sink.http_batch_size,
        flush_interval=sink.http_flush_interval_seconds,
        timeout=sink.http_timeout_seconds,
        max_retries=sink.http_max_retries,
    )
    handler.setLevel(sink.level.value)

    renderer: Processor = structlog.processors.JSONRenderer()
    processors: list[Processor] = [
        ProcessorFormatter.remove_processors_meta,
        structlog.processors.format_exc_info,
        renderer,
    ]
    formatter = ProcessorFormatter(
        processors=processors,
        foreign_pre_chain=foreign_pre_chain,
    )
    handler.setFormatter(formatter)

    return handler
