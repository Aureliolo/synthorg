"""OTLP handler for shipping structured logs as OpenTelemetry log records.

Batches log records in a thread-safe queue and exports them as OTLP
log records to a configurable endpoint using a background daemon thread.
Uses existing correlation IDs (request_id, task_id, agent_id) as
trace context attributes.
"""

import json
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
from synthorg.core.normalization import strip_trailing_slash
from synthorg.observability import safe_error_description
from synthorg.observability.enums import OtlpProtocol
from synthorg.observability.events.metrics import (
    METRICS_OTLP_CALLBACK_ERROR,
    METRICS_OTLP_EXPORT_FAILED,
    METRICS_OTLP_FLUSHER_ERROR,
    METRICS_OTLP_INVALID_CALLBACK,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from synthorg.observability.config import SinkConfig

    ExportCallback = Callable[[str, int], None]
    # Signature: (outcome: "success"|"failure", dropped_records: int) -> None

_FLUSHER_THREAD_NAME = "log-otlp-flusher"

# Dedicated logger for this module. A module-level structlog logger
# avoids recursion: even if the root logger routes through
# :class:`OtlpHandler`, every record produced from the flusher thread
# is suppressed by the thread-name guard in :meth:`OtlpHandler.emit`.
_internal_logger = structlog.stdlib.get_logger(__name__)

# Correlation ID field names injected by structlog contextvars
_CORRELATION_FIELDS = ("request_id", "task_id", "agent_id")

# Mapping from Python log levels to OTLP severity numbers.
# Python's CRITICAL maps to OTEL's FATAL range (21-24).
# https://opentelemetry.io/docs/specs/otel/logs/data-model/#severity-fields
_SEVERITY_MAP: dict[int, int] = {
    logging.DEBUG: 5,
    logging.INFO: 9,
    logging.WARNING: 13,
    logging.ERROR: 17,
    logging.CRITICAL: 21,
}


_DEFAULT_BATCH_SIZE: Final[int] = 100
_DEFAULT_FLUSH_INTERVAL_SECONDS: Final[float] = 5.0
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 10.0


class OtlpHandler(logging.Handler):
    """Handler that batches log records and exports them as OTLP log records.

    A background daemon thread periodically flushes the queue.  Records
    are also flushed when the batch size is reached or when the handler
    is closed.

    Only HTTP/JSON transport is implemented. gRPC is rejected at
    both config validation (``SinkConfig``) and handler init. The
    implementation uses JSON encoding with
    ``Content-Type: application/json``.

    Args:
        endpoint: OTLP collector endpoint URL.
        protocol: OTLP transport protocol (only ``HTTP_JSON`` supported).
        headers: Extra HTTP headers as ``(name, value)`` pairs.
        batch_size: Number of records per export batch.
        flush_interval: Seconds between automatic flushes.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(  # noqa: PLR0913
        self,
        endpoint: str,
        *,
        protocol: OtlpProtocol = OtlpProtocol.HTTP_JSON,
        headers: tuple[tuple[str, str], ...] = (),
        batch_size: int = _DEFAULT_BATCH_SIZE,
        flush_interval: float = _DEFAULT_FLUSH_INTERVAL_SECONDS,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        _start_flusher: bool = True,
    ) -> None:
        super().__init__()
        if protocol == OtlpProtocol.GRPC:
            msg = "gRPC transport is not implemented; use HTTP_JSON"
            raise NotImplementedError(msg)
        self._endpoint = endpoint
        self._protocol = protocol
        self._extra_headers = dict(headers)
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._timeout = timeout
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
        if _start_flusher:
            self._flusher.start()

    @override
    def emit(self, record: logging.LogRecord) -> None:
        """Queue a record for batched OTLP export.

        Records produced from the handler's own flusher thread are
        dropped to prevent infinite recursion: when the flusher logs
        an export failure, routing that log through this handler
        would requeue it for export, cycling forever.
        """
        if threading.current_thread().name == _FLUSHER_THREAD_NAME:
            return
        self._enqueue(record)

    def _enqueue(self, record: logging.LogRecord) -> None:
        """Enqueue *record* for the background flusher, tracking pending count."""
        try:
            self._queue.put_nowait(record)
            with self._pending_lock:
                self._pending_count += 1
                if self._pending_count >= self._batch_size:
                    self._batch_ready.set()
        except Exception as exc:
            reraise_critical(exc)
            self.handleError(record)

    def set_export_callback(
        self,
        callback: ExportCallback | None,
    ) -> None:
        """Register a callback invoked after every export batch.

        Passed ``(outcome, dropped_records)`` where ``outcome`` is
        ``"success"`` or ``"failure"`` and ``dropped_records`` is the
        number of log records the batch failed to deliver (0 on
        success). Used by startup wiring to push
        :meth:`PrometheusCollector.record_otlp_export` without
        coupling the handler directly to AppState.

        Thread safety: invoked from the flusher thread; the callback
        must be safe to call concurrently with ``emit``.

        Raises:
            TypeError: When ``callback`` is not callable (and not
                ``None``). Failing fast avoids surfacing the
                mistake only when the flusher thread eventually
                calls it.
        """
        # Typed callers satisfy this at check time; the runtime
        # guard catches misuse from untyped code (tests, config
        # loaders, dynamic wiring). Casting to ``object`` keeps
        # mypy from flagging the ``callable`` check as dead under
        # the strict signature.
        candidate: object = callback
        if candidate is not None and not callable(candidate):
            _internal_logger.warning(
                METRICS_OTLP_INVALID_CALLBACK,
                provided_type=type(callback).__name__,
            )
            msg = "export callback must be callable or None"
            raise TypeError(msg)
        self._export_callback = callback

    def _increment_dropped(self, count: int) -> None:
        """Atomically increment the dropped record counter.

        Acquires ``_pending_lock`` to ensure thread-safe updates.
        """
        with self._pending_lock:
            self._dropped_count += count

    def _format_as_otlp_dict(self, record: logging.LogRecord) -> dict[str, Any]:
        """Convert a log record to an OTLP-compatible dictionary.

        Maps correlation IDs to trace attributes and Python log levels
        to OTLP severity numbers.

        Args:
            record: The log record to convert.

        Returns:
            Dictionary with OTLP log record fields.
        """
        attributes: list[dict[str, Any]] = [
            {
                "key": "logger.name",
                "value": {"stringValue": record.name},
            },
        ]
        for field in _CORRELATION_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                attributes.append(
                    {
                        "key": field,
                        "value": {"stringValue": str(value)},
                    }
                )

        # Use self.format(record) so the ProcessorFormatter and
        # foreign_pre_chain run, producing structured JSON output.
        body = self.format(record) if self.formatter else record.getMessage()

        return {
            "body": {"stringValue": body},
            "severityNumber": _SEVERITY_MAP.get(record.levelno, 0),
            "severityText": record.levelname,
            "timeUnixNano": str(int(record.created * 1_000_000_000)),
            "attributes": attributes,
        }

    def _flush_loop(self) -> None:
        """Background loop: flush on interval, batch-ready, or shutdown."""
        while not self._shutdown.is_set():
            self._batch_ready.wait(timeout=self._flush_interval)
            self._batch_ready.clear()
            if self._shutdown.is_set():
                break
            try:
                self._drain_and_flush()
            except Exception as exc:
                reraise_critical(exc)
                _internal_logger.error(
                    METRICS_OTLP_FLUSHER_ERROR,
                )

    def _drain_and_flush(self) -> None:
        """Drain all queued records and export as OTLP batches."""
        records: list[logging.LogRecord] = []
        while True:
            try:
                records.append(self._queue.get_nowait())
            except queue.Empty:
                break

        with self._pending_lock:
            self._pending_count = max(0, self._pending_count - len(records))

        for start in range(0, len(records), self._batch_size):
            batch = records[start : start + self._batch_size]
            if batch:
                self._export_batch(batch)

    def _export_batch(self, records: list[logging.LogRecord]) -> None:
        """Export a batch of records as OTLP JSON log records."""
        log_records: list[dict[str, Any]] = []
        format_drops = 0
        for record in records:
            try:
                log_records.append(self._format_as_otlp_dict(record))
            except Exception as exc:
                reraise_critical(exc)
                self.handleError(record)
                self._increment_dropped(1)
                format_drops += 1

        if not log_records:
            # Pure-formatting failure: surface the drop count so
            # the export-outcome callback reflects every lost
            # record instead of silently zeroing the counter.
            if format_drops:
                self._invoke_export_callback("failure", format_drops)
            return

        # OTLP JSON format: wrap in resourceLogs envelope
        payload = {
            "resourceLogs": [
                {
                    "resource": {"attributes": []},
                    "scopeLogs": [
                        {
                            "scope": {"name": "synthorg"},
                            "logRecords": log_records,
                        },
                    ],
                },
            ],
        }
        body = json.dumps(payload).encode()

        # Use /v1/logs path for OTLP HTTP JSON
        url = strip_trailing_slash(self._endpoint) + "/v1/logs"
        request = urllib.request.Request(url, data=body, method="POST")  # noqa: S310
        request.add_header("Content-Type", "application/json")
        for name, value in self._extra_headers.items():
            request.add_header(name, value)

        try:
            with urllib.request.urlopen(  # noqa: S310
                request,
                timeout=self._timeout,
            ):
                pass
        except Exception as exc:
            reraise_critical(exc)
            # urllib.error.HTTPError wraps a file pointer to the response
            # body.  Close it explicitly to avoid leaking file descriptors.
            if isinstance(exc, urllib.error.HTTPError):
                exc.close()
            self._increment_dropped(len(log_records))
            with self._pending_lock:
                total_dropped = self._dropped_count
            _internal_logger.warning(
                METRICS_OTLP_EXPORT_FAILED,
                url=url,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                dropped_records=len(log_records),
                total_dropped=total_dropped,
            )
            # Include records lost to formatting alongside the
            # HTTP-export loss so the callback sees the full drop
            # total (format_drops were already incremented above).
            self._invoke_export_callback(
                "failure",
                format_drops + len(log_records),
            )
            return
        self._invoke_export_callback("success", format_drops)

    def _invoke_export_callback(self, outcome: str, dropped: int) -> None:
        """Call the registered export callback, swallowing any errors.

        A callback failure must never break the export loop. Instead
        of re-raising, the exception is caught and emitted as a
        structured :data:`METRICS_OTLP_CALLBACK_ERROR` warning via
        the module's internal logger (with ``exc_info``), so operators
        can see which sink went bad without losing subsequent export
        outcomes. :class:`MemoryError` and :class:`RecursionError` are
        propagated so the interpreter can react as usual.
        """
        callback = self._export_callback
        if callback is None:
            return
        try:
            callback(outcome, dropped)
        except Exception as exc:
            reraise_critical(exc)
            _internal_logger.warning(
                METRICS_OTLP_CALLBACK_ERROR,
                outcome=outcome,
                dropped_records=dropped,
            )

    @override
    def close(self) -> None:
        """Signal shutdown, flush remaining records, stop thread."""
        self._shutdown.set()
        self._batch_ready.set()
        join_timeout = self._timeout * 2
        if self._flusher.is_alive():
            self._flusher.join(timeout=join_timeout)
            if self._flusher.is_alive():
                # close() may run before the logging system is fully
                # re-configured (e.g. atexit), so prefer stderr to
                # avoid ordering hazards during shutdown.
                print(  # noqa: T201
                    "WARNING: log-otlp-flusher thread did not stop "
                    f"within {join_timeout:.1f}s timeout",
                    file=sys.stderr,
                    flush=True,
                )
        # Always drain remaining records regardless of thread state.
        self._drain_and_flush()
        super().close()


def build_otlp_handler(
    sink: SinkConfig,
    foreign_pre_chain: list[Any],
) -> OtlpHandler:
    """Build an OtlpHandler from an OTLP sink configuration.

    Args:
        sink: The OTLP sink configuration.
        foreign_pre_chain: Processor chain for stdlib-originated logs.

    Returns:
        A configured ``OtlpHandler`` with JSON formatting.

    Raises:
        ValueError: If ``sink.otlp_endpoint`` is absent or empty.
    """
    if not sink.otlp_endpoint:
        msg = "OTLP sink requires a non-empty otlp_endpoint"
        raise ValueError(msg)
    handler = OtlpHandler(
        endpoint=sink.otlp_endpoint,
        protocol=sink.otlp_protocol,
        headers=sink.otlp_headers,
        batch_size=sink.otlp_batch_size,
        flush_interval=sink.otlp_export_interval_seconds,
        timeout=sink.otlp_timeout_seconds,
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
