"""Observability-specific enumerations."""

from enum import StrEnum


class LogLevel(StrEnum):
    """Standard log severity levels.

    Values match Python's stdlib logging level names for seamless
    integration between structlog and the ``logging`` module.
    """

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class RotationStrategy(StrEnum):
    """Log file rotation strategies.

    Attributes:
        BUILTIN: Size-based rotation via ``RotatingFileHandler``.
        EXTERNAL: Watched rotation via ``WatchedFileHandler`` (logrotate).
    """

    BUILTIN = "builtin"
    EXTERNAL = "external"


class SinkType(StrEnum):
    """Log output destination types.

    Attributes:
        CONSOLE: Write to stderr via ``StreamHandler``.
        FILE: Write to a log file with optional rotation.
    """

    CONSOLE = "console"
    FILE = "file"
