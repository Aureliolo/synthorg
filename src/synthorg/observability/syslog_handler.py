"""Syslog handler builder for shipping structured logs to syslog endpoints.

Builds a ``logging.handlers.SysLogHandler`` configured for structured
JSON output via structlog's ``ProcessorFormatter``.
"""

import logging.handlers
import socket
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import MappingProxyType
from typing import TYPE_CHECKING, override

import structlog
from structlog.stdlib import ProcessorFormatter
from structlog.typing import Processor

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger
from synthorg.observability.config import SinkConfig
from synthorg.observability.enums import SyslogFacility, SyslogProtocol
from synthorg.observability.errors import SinkConstructionError
from synthorg.observability.events.metrics import (
    METRICS_LOG_SINK_CALLBACK_ERROR,
    METRICS_LOG_SINK_EXPORT_FAILED,
    METRICS_LOG_SINK_INVALID_CALLBACK,
)
from synthorg.observability.redaction import safe_error_description

if TYPE_CHECKING:
    from collections.abc import Callable

    # Stays TYPE_CHECKING: a runtime alias would let ``set_export_callback``
    # accept a non-callable before its own TypeError guard can fire.
    ExportCallback = Callable[[str, int], None]

_internal_logger = get_logger(__name__)

# A diagnostic logged from this sink's own failure path propagates back
# through the root logger into the same sink. Syslog ``emit`` is
# synchronous, so that re-entry would recurse emit -> handleError ->
# emit until the stack overflows under a syslog-endpoint outage. The
# thread-local guard makes the sink drop any record produced while it is
# already emitting one of its own diagnostics; the diagnostic still
# reaches non-sink handlers (stderr / file) unaffected.
_reentry_guard = threading.local()


@contextmanager
def _suppress_sink_reentry() -> Iterator[None]:
    """Mark the current thread as emitting a sink diagnostic."""
    previous = getattr(_reentry_guard, "active", False)
    _reentry_guard.active = True
    try:
        yield
    finally:
        _reentry_guard.active = previous


FACILITY_MAP: MappingProxyType[SyslogFacility, int] = MappingProxyType(
    {
        SyslogFacility.USER: logging.handlers.SysLogHandler.LOG_USER,
        SyslogFacility.DAEMON: logging.handlers.SysLogHandler.LOG_DAEMON,
        SyslogFacility.SYSLOG: logging.handlers.SysLogHandler.LOG_SYSLOG,
        SyslogFacility.AUTH: logging.handlers.SysLogHandler.LOG_AUTH,
        SyslogFacility.KERN: logging.handlers.SysLogHandler.LOG_KERN,
        SyslogFacility.LOCAL0: logging.handlers.SysLogHandler.LOG_LOCAL0,
        SyslogFacility.LOCAL1: logging.handlers.SysLogHandler.LOG_LOCAL1,
        SyslogFacility.LOCAL2: logging.handlers.SysLogHandler.LOG_LOCAL2,
        SyslogFacility.LOCAL3: logging.handlers.SysLogHandler.LOG_LOCAL3,
        SyslogFacility.LOCAL4: logging.handlers.SysLogHandler.LOG_LOCAL4,
        SyslogFacility.LOCAL5: logging.handlers.SysLogHandler.LOG_LOCAL5,
        SyslogFacility.LOCAL6: logging.handlers.SysLogHandler.LOG_LOCAL6,
        SyslogFacility.LOCAL7: logging.handlers.SysLogHandler.LOG_LOCAL7,
    }
)

PROTOCOL_MAP: MappingProxyType[SyslogProtocol, int] = MappingProxyType(
    {
        SyslogProtocol.TCP: socket.SOCK_STREAM,
        SyslogProtocol.UDP: socket.SOCK_DGRAM,
    }
)

# Mirrors the stdlib ``SysLogHandler.__init__`` default so the subclass
# signature stays compatible; the builder always passes an explicit
# address, so this only applies to bare construction (e.g. in tests).
_DEFAULT_SYSLOG_ADDRESS: tuple[str, int] = (
    "localhost",
    logging.handlers.SYSLOG_UDP_PORT,
)


class CountingSysLogHandler(logging.handlers.SysLogHandler):
    """``SysLogHandler`` that counts drops and reports export outcomes.

    The stdlib handler swallows send failures through ``handleError``,
    which (with ``logging.raiseExceptions``) only prints a traceback to
    stderr. That hides a misconfigured syslog endpoint, and a traceback
    to stderr can serialise credential-bearing frame-locals.
    This subclass counts every dropped record, emits a redacted
    structured warning instead of the stderr traceback, and pushes an
    export-outcome callback (``"success"`` / ``"failure"``) so startup
    wiring can record :meth:`PrometheusCollector.record_log_sink_export`
    without coupling the handler to AppState.
    """

    def __init__(
        self,
        address: tuple[str, int] | str = _DEFAULT_SYSLOG_ADDRESS,
        facility: int = logging.handlers.SysLogHandler.LOG_USER,
        socktype: socket.SocketKind | None = None,
    ) -> None:
        super().__init__(address=address, facility=facility, socktype=socktype)
        self._dropped_count = 0
        self._drop_lock = threading.Lock()
        self._export_callback: ExportCallback | None = None
        # Per-emit failure flag is thread-local so concurrent emits do
        # not misattribute one record's failure to another.
        self._emit_state = threading.local()

    def set_export_callback(self, callback: ExportCallback | None) -> None:
        """Register a callback invoked after every emit.

        Passed ``(outcome, dropped_records)``; ``dropped_records`` is 1
        on a failed emit, 0 on success.

        Raises:
            TypeError: When ``callback`` is not callable (and not
                ``None``).
        """
        candidate: object = callback
        if candidate is not None and not callable(candidate):
            _internal_logger.warning(
                METRICS_LOG_SINK_INVALID_CALLBACK,
                sink="syslog",
                provided_type=type(callback).__name__,
            )
            msg = "export callback must be callable or None"
            raise TypeError(msg)
        self._export_callback = callback

    def _invoke_export_callback(self, outcome: str, dropped: int) -> None:
        """Call the registered callback, swallowing callback errors."""
        callback = self._export_callback
        if callback is None:
            return
        try:
            callback(outcome, dropped)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            with _suppress_sink_reentry():
                _internal_logger.warning(
                    METRICS_LOG_SINK_CALLBACK_ERROR,
                    sink="syslog",
                    outcome=outcome,
                    dropped_records=dropped,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

    @override
    def emit(self, record: logging.LogRecord) -> None:
        """Send *record* and report the success/failure outcome.

        A record produced while this sink is emitting one of its own
        diagnostics is dropped: routing it back through the sink would
        recurse emit -> handleError -> emit under a syslog outage.
        """
        if getattr(_reentry_guard, "active", False):
            return
        self._emit_state.failed = False
        super().emit(record)
        failed = getattr(self._emit_state, "failed", False)
        self._invoke_export_callback(
            "failure" if failed else "success",
            1 if failed else 0,
        )

    @override
    def handleError(self, record: logging.LogRecord) -> None:
        """Count the drop and log redacted context (no stderr traceback)."""
        self._emit_state.failed = True
        with self._drop_lock:
            self._dropped_count += 1
            total_dropped = self._dropped_count
        exc = sys.exc_info()[1]
        with _suppress_sink_reentry():
            _internal_logger.warning(
                METRICS_LOG_SINK_EXPORT_FAILED,
                sink="syslog",
                error_type=type(exc).__name__ if exc is not None else "unknown",
                error=safe_error_description(exc) if exc is not None else "unknown",
                total_dropped=total_dropped,
            )


def build_syslog_handler(
    sink: SinkConfig,
    foreign_pre_chain: list[Processor],
) -> CountingSysLogHandler:
    """Build a SysLogHandler from a SYSLOG sink configuration.

    Args:
        sink: The SYSLOG sink configuration.
        foreign_pre_chain: Processor chain for stdlib-originated logs.

    Returns:
        A configured ``SysLogHandler`` with JSON formatting.

    Raises:
        ValueError: If ``sink.syslog_host`` is absent or blank (a
            user-correctable invalid config the sink-test endpoint
            surfaces as ``valid=False``).
        SinkConstructionError: If the OS-level socket connection to the
            syslog endpoint fails.
    """
    if not sink.syslog_host or not sink.syslog_host.strip():
        msg = "SYSLOG sink requires a non-empty syslog_host"
        raise ValueError(msg)
    host = sink.syslog_host.strip()
    try:
        handler = CountingSysLogHandler(
            address=(host, sink.syslog_port),
            facility=FACILITY_MAP[sink.syslog_facility],
            socktype=socket.SocketKind(
                PROTOCOL_MAP[sink.syslog_protocol],
            ),
        )
    except OSError as exc:
        msg = (
            f"Failed to connect to syslog endpoint "
            f"{sink.syslog_host}:{sink.syslog_port} "
            f"({sink.syslog_protocol.value.upper()}): {safe_error_description(exc)}"
        )
        raise SinkConstructionError(msg) from exc
    # UDP: disable NUL terminator (datagrams are self-framing, NUL
    # corrupts JSON parsers).  TCP: keep NUL (the traditional syslog-
    # over-TCP message delimiter that receivers expect).
    handler.append_nul = sink.syslog_protocol == SyslogProtocol.TCP
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
