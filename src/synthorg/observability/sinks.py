"""Log handler factory for building stdlib handlers from sink config.

Translates :class:`~synthorg.observability.config.SinkConfig` instances
into fully configured :class:`logging.Handler` objects with the
appropriate structlog :class:`~structlog.stdlib.ProcessorFormatter`.
"""

import logging
import logging.handlers
import sys
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import structlog
from structlog.stdlib import ProcessorFormatter

from synthorg.observability.config import RotationConfig, SinkConfig
from synthorg.observability.enums import RotationStrategy, SinkType

# ── Logger name routing ───────────────────────────────────────────

# Maps sink file_path to the logger name prefixes that should be
# routed to that sink.  Sinks not listed here are catch-all sinks
# (no name filter attached).
_SINK_ROUTING: MappingProxyType[str, tuple[str, ...]] = MappingProxyType(
    {
        "audit.log": ("synthorg.security.",),
        "cost_usage.log": ("synthorg.budget.", "synthorg.providers."),
        "agent_activity.log": ("synthorg.engine.", "synthorg.core."),
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
        if self._exclude:
            for prefix in self._exclude:
                if name.startswith(prefix):
                    return False
        if self._include:
            return any(name.startswith(prefix) for prefix in self._include)
        return True


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

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        msg = (
            f"Failed to create log directory '{file_path.parent}' "
            f"for sink '{sink.file_path}': {exc}"
        )
        raise RuntimeError(msg) from exc

    rotation = sink.rotation or RotationConfig()

    try:
        if rotation.strategy == RotationStrategy.BUILTIN:
            return logging.handlers.RotatingFileHandler(
                filename=str(file_path),
                maxBytes=rotation.max_bytes,
                backupCount=rotation.backup_count,
            )
        return logging.handlers.WatchedFileHandler(
            filename=str(file_path),
        )
    except OSError as exc:
        msg = (
            f"Failed to open log file '{file_path}' for sink '{sink.file_path}': {exc}"
        )
        raise RuntimeError(msg) from exc


def build_handler(
    sink: SinkConfig,
    log_dir: Path,
    foreign_pre_chain: list[Any],
) -> logging.Handler:
    """Build a stdlib logging handler from a sink configuration.

    For ``CONSOLE`` sinks a :class:`logging.StreamHandler` writing to
    ``stderr`` is created.  For ``FILE`` sinks see
    :func:`_build_file_handler`.

    Args:
        sink: The sink configuration describing the handler to build.
        log_dir: Base directory for log files.
        foreign_pre_chain: Processor chain for stdlib-originated logs.

    Returns:
        A configured :class:`logging.Handler` with formatter attached.
    """
    if sink.sink_type == SinkType.CONSOLE:
        handler: logging.Handler = logging.StreamHandler(sys.stderr)
    else:
        handler = _build_file_handler(sink, log_dir)

    handler.setLevel(sink.level.value)

    renderer: Any
    if sink.json_format:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    formatter = ProcessorFormatter(
        processors=[
            ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=foreign_pre_chain,
    )
    handler.setFormatter(formatter)

    # Attach a logger name filter for dedicated sink files.
    # Only include_prefixes is used today; exclude_prefixes exists on
    # _LoggerNameFilter for future routing needs (e.g. noisy-logger suppression).
    if sink.file_path is not None and sink.file_path in _SINK_ROUTING:
        name_filter = _LoggerNameFilter(
            include_prefixes=_SINK_ROUTING[sink.file_path],
        )
        handler.addFilter(name_filter)

    return handler
