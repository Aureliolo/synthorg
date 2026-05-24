"""Observability module for structured logging and correlation tracking.

Provides:

- Structured logging via structlog with stdlib bridge
- Log configuration with console and file sinks
- Sensitive field sanitization
- Correlation ID tracking via context variables

.. note::

    Call :func:`configure_logging` once at application startup to
    initialise the logging pipeline.  Use :func:`get_logger` in all
    modules to obtain a bound structured logger.
"""

from synthorg.observability._logger import get_logger
from synthorg.observability.config import (
    DEFAULT_SINKS,
    LogConfig,
    RotationConfig,
    SinkConfig,
)
from synthorg.observability.correlation import (
    bind_correlation_id,
    clear_correlation_ids,
    generate_correlation_id,
    unbind_correlation_id,
    with_correlation,
    with_correlation_async,
)
from synthorg.observability.enums import (
    LogLevel,
    RotationStrategy,
    SinkType,
    SyslogFacility,
    SyslogProtocol,
)
from synthorg.observability.processors import (
    sanitize_sensitive_fields,
    scrub_event_fields,
)
from synthorg.observability.redaction import (
    log_exception_redacted,
    safe_error_description,
    scrub_secret_tokens,
)
from synthorg.observability.setup import configure_logging

__all__ = [
    "DEFAULT_SINKS",
    "LogConfig",
    "LogLevel",
    "RotationConfig",
    "RotationStrategy",
    "SinkConfig",
    "SinkType",
    "SyslogFacility",
    "SyslogProtocol",
    "bind_correlation_id",
    "clear_correlation_ids",
    "configure_logging",
    "generate_correlation_id",
    "get_logger",
    "log_exception_redacted",
    "safe_error_description",
    "sanitize_sensitive_fields",
    "scrub_event_fields",
    "scrub_secret_tokens",
    "unbind_correlation_id",
    "with_correlation",
    "with_correlation_async",
]
