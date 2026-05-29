"""HTTP batch handler for shipping structured logs via HTTP POST.

Batches log records in a thread-safe queue and POSTs them as JSON
arrays to a configurable URL using a background daemon thread.
Uses ``urllib.request`` (stdlib) to avoid external dependencies.
"""

import logging
import queue
import sys
import threading
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any, Final, override

import structlog
from structlog.stdlib import ProcessorFormatter

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability.redaction import safe_error_description

if TYPE_CHECKING:
    from synthorg.observability.config import SinkConfig


_DEFAULT_BATCH_SIZE: Final[int] = 100
_DEFAULT_FLUSH_INTERVAL_SECONDS: Final[float] = 5.0
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 10.0
_DEFAULT_MAX_RETRIES: Final[int] = 3


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
        self._shutdown = threading.Event()
        self._batch_ready = threading.Event()
        self._flusher = threading.Thread(
            target=self._flush_loop,
            daemon=True,
            name="log-http-flusher",
        )
        self._flusher.start()

    @override
    def emit(self, record: logging.LogRecord) -> None:
        """Queue a record for batched shipping."""
        try:
            self._queue.put_nowait(record)
            with self._pending_lock:
                self._pending_count += 1
                if self._pending_count >= self._batch_size:
                    self._batch_ready.set()
        except Exception:
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
            except Exception as exc:
                reraise_critical(exc)
                print(  # noqa: T201
                    f"ERROR: log-http-flusher encountered unexpected error: {safe_error_description(exc)}",  # noqa: E501
                    file=sys.stderr,
                    flush=True,
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
        for record in records:
            try:
                entries.append(self.format(record))
            except Exception:
                self.handleError(record)
                with self._pending_lock:
                    self._dropped_count += 1

        if not entries:
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
            print(  # noqa: T201
                f"WARNING: HTTP log shipping failed after "
                f"{1 + self._max_retries} attempts to {self._url}: "
                f"{safe_error_description(error)} "
                f"(dropped {len(entries)} records, "
                f"total dropped: {self._dropped_count})",
                file=sys.stderr,
                flush=True,
            )

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
            except Exception as exc:
                reraise_critical(exc)
                # HTTPError wraps a response FP -- close to avoid FD leak.
                if isinstance(exc, urllib.error.HTTPError):
                    exc.close()
                    # 4xx client errors are non-retryable.
                    if 400 <= exc.code < 500:  # noqa: PLR2004
                        return exc
                last_error = exc
                if attempt < self._max_retries:
                    continue
            else:
                return None
        return last_error

    @override
    def close(self) -> None:
        """Signal shutdown, flush remaining records, stop thread."""
        self._shutdown.set()
        self._batch_ready.set()  # Wake the flusher
        # Allow enough time for in-flight retries to finish:
        # worst case is (1 + max_retries) * timeout per batch.
        join_timeout = (1 + self._max_retries) * self._timeout
        self._flusher.join(timeout=join_timeout)
        # Only drain from the calling thread if the flusher has exited.
        # If join() timed out the flusher may still be in _drain_and_flush,
        # and draining concurrently would race on the queue.
        if not self._flusher.is_alive():
            self._drain_and_flush()
        super().close()


def build_http_handler(
    sink: SinkConfig,
    foreign_pre_chain: list[Any],
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

    renderer: Any = structlog.processors.JSONRenderer()
    processors: list[Any] = [
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
