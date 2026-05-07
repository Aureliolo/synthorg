"""Log handler factory for building stdlib handlers from sink config.

Translates :class:`~synthorg.observability.config.SinkConfig` instances
into fully configured :class:`logging.Handler` objects with the
appropriate structlog :class:`~structlog.stdlib.ProcessorFormatter`.
"""

import gzip
import logging
import logging.handlers
import sys
from pathlib import Path as StdPath
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

import structlog
from structlog.stdlib import ProcessorFormatter

from synthorg.observability import safe_error_description
from synthorg.observability.config import RotationConfig, SinkConfig
from synthorg.observability.enums import RotationStrategy, SinkType
from synthorg.observability.events.api import (
    API_REQUEST_COMPLETED,
    API_REQUEST_STARTED,
)

# ── Flushing file handlers ────────────────────────────────────────
# Standard RotatingFileHandler and WatchedFileHandler buffer writes,
# so log entries may never reach disk in a long-running server with
# infrequent events.  These subclasses flush after every emit.


class _FlushingRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler that flushes to disk after every emit."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        try:
            self.flush()
        except Exception:  # mirrors StreamHandler.emit
            self.handleError(record)


class _CompressingRotatingFileHandler(_FlushingRotatingFileHandler):
    """RotatingFileHandler that gzips backup files after rotation.

    When ``compress`` is ``True``, the rotation chain operates on
    ``.gz`` files directly: existing ``.N.gz`` backups are shifted,
    then the freshly rotated ``.1`` file is compressed in place.

    Compression errors are handled gracefully -- the uncompressed
    backup is retained on failure.
    """

    def __init__(
        self,
        *,
        compress: bool = True,
        filename: str,
        maxBytes: int = 0,  # noqa: N803
        backupCount: int = 0,  # noqa: N803
    ) -> None:
        self._compress = compress
        super().__init__(
            filename=filename,
            maxBytes=maxBytes,
            backupCount=backupCount,
        )

    def doRollover(self) -> None:  # noqa: N802
        """Rotate with gzip-aware backup chain."""
        if not self._compress:
            super().doRollover()
            return

        if self.stream:
            self.stream.close()
            self.stream = None

        if self.backupCount > 0:
            try:
                self._shift_gz_backups()
                dfn = self._rotate_current_log()
                self._compress_file(dfn)
            except OSError as exc:
                print(  # noqa: T201
                    f"WARNING: Compressed rotation failed for "
                    f"{self.baseFilename}: {safe_error_description(exc)}; "
                    "uncompressed backup retained",
                    file=sys.stderr,
                    flush=True,
                )

        if not self.delay:
            self.stream = self._open()

    def _shift_gz_backups(self) -> None:
        """Shift existing .gz backups (highest index first)."""
        # Remove the oldest before shifting to make room
        oldest = StdPath(f"{self.baseFilename}.{self.backupCount}.gz")
        if oldest.exists():
            oldest.unlink()
        for i in range(self.backupCount - 1, 0, -1):
            src = StdPath(f"{self.baseFilename}.{i}.gz")
            dst = StdPath(f"{self.baseFilename}.{i + 1}.gz")
            if src.exists():
                if dst.exists():
                    dst.unlink()
                src.rename(dst)

    def _rotate_current_log(self) -> str:
        """Rotate the current log to .1 and return the path."""
        dfn = self.rotation_filename(
            f"{self.baseFilename}.1",
        )
        dfn_path = StdPath(dfn)
        if dfn_path.exists():
            dfn_path.unlink()
        self.rotate(self.baseFilename, dfn)
        return dfn

    def _compress_file(self, path: str) -> None:
        """Gzip a single file in place via atomic temp file."""
        src = StdPath(path)
        tmp_gz = StdPath(f"{path}.gz.tmp")
        gz = StdPath(f"{path}.gz")
        try:
            with src.open("rb") as src_f, gzip.open(tmp_gz, "wb") as gz_f:
                while chunk := src_f.read(1024 * 1024):
                    gz_f.write(chunk)
            tmp_gz.rename(gz)
            src.unlink()
        except OSError as exc:
            print(  # noqa: T201
                f"WARNING: Failed to compress rotated log {path}: {safe_error_description(exc)}",  # noqa: E501
                file=sys.stderr,
                flush=True,
            )
            try:
                if tmp_gz.exists():
                    tmp_gz.unlink()
            except OSError as cleanup_exc:
                print(  # noqa: T201
                    f"WARNING: Failed to clean up temp file {tmp_gz}: {cleanup_exc}",
                    file=sys.stderr,
                    flush=True,
                )
            raise


class _FlushingWatchedFileHandler(logging.handlers.WatchedFileHandler):
    """WatchedFileHandler that flushes to disk after every emit."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        try:
            self.flush()
        except Exception:  # mirrors StreamHandler.emit
            self.handleError(record)


# ── Logger name routing ───────────────────────────────────────────

# Maps sink file_path to the logger name prefixes that should be
# routed to that sink.  Sinks not listed here are catch-all sinks
# (no name filter attached).
SINK_ROUTING: MappingProxyType[str, tuple[str, ...]] = MappingProxyType(
    {
        "audit.log": (
            "synthorg.security.",
            "synthorg.hr.",
            "synthorg.observability.",
        ),
        "cost_usage.log": ("synthorg.budget.", "synthorg.providers."),
        "agent_activity.log": (
            "synthorg.engine.",
            "synthorg.core.",
            "synthorg.communication.",
            "synthorg.tools.",
            "synthorg.memory.",
        ),
        "access.log": ("synthorg.api.",),
        "persistence.log": ("synthorg.persistence.",),
        "configuration.log": ("synthorg.settings.", "synthorg.config."),
        "backup.log": ("synthorg.backup.",),
    }
)

# Maps sink file_path to structlog event names that must NOT land
# in that sink even if the logger-name include filter matches.  Used
# to keep request-lifecycle events out of ``synthorg.log`` (they
# already have a dedicated home in ``access.log``); without this
# 96% of main-log volume would be duplicated request records that
# bury every other event.
SINK_EVENT_EXCLUDES: MappingProxyType[str, tuple[str, ...]] = MappingProxyType(
    {
        "synthorg.log": (API_REQUEST_STARTED, API_REQUEST_COMPLETED),
    }
)

# Maps sink file_path to a strict level-equality filter.  ``debug.log``
# is pinned to ``DEBUG`` exactly so it stays empty when nothing emits
# DEBUG instead of accidentally collecting INFO+ as a duplicate of the
# main log.
SINK_EXACT_LEVELS: MappingProxyType[str, int] = MappingProxyType(
    {
        "debug.log": logging.DEBUG,
    }
)


class _LoggerNameFilter(logging.Filter):
    """Filter log records by logger name prefixes.

    When *include_prefixes* is non-empty, only records whose
    ``record.name`` starts with one of the prefixes are accepted.
    When *exclude_prefixes* is non-empty, records matching any
    exclude prefix are rejected (checked before includes).

    Args:
        include_prefixes: Accept only these prefixes (empty = accept all).
        exclude_prefixes: Reject these prefixes (empty = reject none).
    """

    def __init__(
        self,
        *,
        include_prefixes: tuple[str, ...] = (),
        exclude_prefixes: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        for prefix in (*include_prefixes, *exclude_prefixes):
            if not prefix or not prefix.strip():
                msg = "Logger name prefixes must be non-empty strings"
                raise ValueError(msg)
        self._include = include_prefixes
        self._exclude = exclude_prefixes

    def filter(self, record: logging.LogRecord) -> bool:
        """Return True if *record* passes the prefix filters."""
        name = record.name
        if self._exclude and any(name.startswith(prefix) for prefix in self._exclude):
            return False
        if self._include:
            return any(name.startswith(prefix) for prefix in self._include)
        return True


class _EventNameFilter(logging.Filter):
    """Filter records by structlog event name.

    Records produced through ``structlog.stdlib.ProcessorFormatter.
    wrap_for_formatter`` carry the processed event_dict as
    ``record.msg`` (a ``dict`` with an ``event`` key plus all
    structured kwargs).  Foreign records from third-party loggers
    carry ``record.msg`` as a plain string -- in that case we
    compare the string directly so the filter is robust across
    both record shapes.

    Args:
        exclude_events: Event names to drop (empty = drop nothing).
    """

    def __init__(self, *, exclude_events: tuple[str, ...] = ()) -> None:
        super().__init__()
        for event in exclude_events:
            if not event or not event.strip():
                msg = "Excluded event names must be non-empty strings"
                raise ValueError(msg)
        self._exclude = frozenset(exclude_events)

    def filter(self, record: logging.LogRecord) -> bool:
        """Return ``True`` if *record* is not in the exclude set."""
        if not self._exclude:
            return True
        msg = record.msg
        event = msg.get("event", "") if isinstance(msg, dict) else str(msg)
        return event not in self._exclude


class _ExactLevelFilter(logging.Filter):
    """Accept only records whose level is *exactly* ``levelno``.

    Standard ``handler.setLevel(level)`` accepts ``level`` and above;
    this filter narrows the gate so a sink can be pinned to one
    level (``debug.log`` to DEBUG only, for example).
    """

    def __init__(self, *, levelno: int) -> None:
        super().__init__()
        self._levelno = levelno

    def filter(self, record: logging.LogRecord) -> bool:
        """Return ``True`` only when ``record.levelno`` matches."""
        return record.levelno == self._levelno


def _ensure_log_dir(file_path: Path, sink_name: str) -> None:
    """Create parent directories for a log file path.

    Raises:
        RuntimeError: If directory creation fails.
    """
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        msg = (
            f"Failed to create log directory '{file_path.parent}' "
            f"for sink '{sink_name}': {safe_error_description(exc)}"
        )
        raise RuntimeError(msg) from exc


def _build_file_handler(
    sink: SinkConfig,
    log_dir: Path,
) -> logging.Handler:
    """Create a file handler with directory creation and rotation.

    Args:
        sink: The FILE sink configuration.
        log_dir: Base directory for log files.

    Returns:
        A configured file handler.

    Raises:
        RuntimeError: If the log directory or file cannot be created.
        ValueError: If ``file_path`` is unexpectedly ``None``.
    """
    if sink.file_path is None:
        msg = (
            "FILE sink is missing 'file_path'. "
            "This should have been caught by SinkConfig validation."
        )
        raise ValueError(msg)

    file_path = log_dir / sink.file_path
    _ensure_log_dir(file_path, sink.file_path)
    rotation = sink.rotation or RotationConfig()

    try:
        if rotation.strategy == RotationStrategy.BUILTIN:
            if rotation.compress_rotated:
                return _CompressingRotatingFileHandler(
                    filename=str(file_path),
                    maxBytes=rotation.max_bytes,
                    backupCount=rotation.backup_count,
                    compress=True,
                )
            return _FlushingRotatingFileHandler(
                filename=str(file_path),
                maxBytes=rotation.max_bytes,
                backupCount=rotation.backup_count,
            )
        if rotation.compress_rotated:
            msg = "compress_rotated is only supported with RotationStrategy.BUILTIN"
            raise ValueError(msg)
        return _FlushingWatchedFileHandler(filename=str(file_path))
    except OSError as exc:
        msg = f"Failed to open log file '{file_path}' for sink '{sink.file_path}': {safe_error_description(exc)}"  # noqa: E501
        raise RuntimeError(msg) from exc


def _build_formatter(
    sink: SinkConfig,
    foreign_pre_chain: list[Any],
) -> ProcessorFormatter:
    """Build a ``ProcessorFormatter`` for the given sink.

    JSON sinks include ``format_exc_info`` to serialize exception tuples.
    Console sinks omit it because ``ConsoleRenderer`` handles exceptions
    natively.
    """
    renderer: Any
    if sink.json_format:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    processors: list[Any] = [ProcessorFormatter.remove_processors_meta]
    if sink.json_format:
        processors.append(structlog.processors.format_exc_info)
    processors.append(renderer)

    return ProcessorFormatter(
        processors=processors,
        foreign_pre_chain=foreign_pre_chain,
    )


def _attach_formatter_and_routing(
    handler: logging.Handler,
    sink: SinkConfig,
    foreign_pre_chain: list[Any],
    routing: Mapping[str, tuple[str, ...]],
) -> None:
    """Set formatter and optional routing filters on a handler.

    Three filter mechanisms are layered, each driven by its own
    sink-keyed table:

    * ``routing`` (``SINK_ROUTING``): logger-name prefix include.
    * ``SINK_EVENT_EXCLUDES``: drop specific structlog event names
      (e.g. request lifecycle out of the catch-all ``synthorg.log``).
    * ``SINK_EXACT_LEVELS``: pin a sink to one level (``debug.log``
      to DEBUG only) instead of "this level and above".
    """
    handler.setLevel(sink.level.value)
    handler.setFormatter(_build_formatter(sink, foreign_pre_chain))
    if sink.file_path is None:
        return
    if sink.file_path in routing:
        handler.addFilter(
            _LoggerNameFilter(include_prefixes=routing[sink.file_path]),
        )
    if sink.file_path in SINK_EVENT_EXCLUDES:
        handler.addFilter(
            _EventNameFilter(
                exclude_events=SINK_EVENT_EXCLUDES[sink.file_path],
            ),
        )
    if sink.file_path in SINK_EXACT_LEVELS:
        handler.addFilter(
            _ExactLevelFilter(levelno=SINK_EXACT_LEVELS[sink.file_path]),
        )


def build_handler(
    sink: SinkConfig,
    log_dir: Path,
    foreign_pre_chain: list[Any],
    *,
    routing: Mapping[str, tuple[str, ...]] | None = None,
) -> logging.Handler:
    """Build a stdlib logging handler from a sink configuration.

    For ``CONSOLE`` sinks a :class:`logging.StreamHandler` writing to
    ``stderr`` is created.  For ``FILE`` sinks see
    :func:`_build_file_handler`.  For ``SYSLOG`` and ``HTTP`` sinks,
    dedicated handler builders are used.

    Note: SYSLOG and HTTP sinks are built and returned by dedicated
    handler modules; they do not participate in logger-name routing.

    Args:
        sink: The sink configuration describing the handler to build.
        log_dir: Base directory for log files.
        foreign_pre_chain: Processor chain for stdlib-originated logs.
        routing: Optional routing table to use instead of the
            module-level ``SINK_ROUTING``.  When ``None``, the
            default routing is used.

    Returns:
        A configured :class:`logging.Handler` with formatter attached.
    """
    effective_routing = routing if routing is not None else SINK_ROUTING

    handler: logging.Handler
    match sink.sink_type:
        case SinkType.CONSOLE:
            handler = logging.StreamHandler(sys.stderr)
        case SinkType.FILE:
            handler = _build_file_handler(sink, log_dir)
        case SinkType.SYSLOG:
            from synthorg.observability.syslog_handler import (  # noqa: PLC0415
                build_syslog_handler,
            )

            return build_syslog_handler(sink, foreign_pre_chain)
        case SinkType.HTTP:
            from synthorg.observability.http_handler import (  # noqa: PLC0415
                build_http_handler,
            )

            return build_http_handler(sink, foreign_pre_chain)
        case SinkType.PROMETHEUS:
            # Prometheus is pull-based (scrape endpoint), not a log handler.
            # Return a no-op handler -- the /metrics controller serves metrics.
            handler = logging.NullHandler()
            handler.setLevel(sink.level.value)
            return handler
        case SinkType.OTLP:
            from synthorg.observability.otlp_handler import (  # noqa: PLC0415
                build_otlp_handler,
            )

            return build_otlp_handler(sink, foreign_pre_chain)
        case _:  # pragma: no cover
            msg = f"Unsupported sink type: {sink.sink_type}"  # type: ignore[unreachable]
            raise ValueError(msg)

    _attach_formatter_and_routing(
        handler,
        sink,
        foreign_pre_chain,
        effective_routing,
    )
    return handler
