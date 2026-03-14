"""Logging system setup and configuration.

Provides the idempotent :func:`configure_logging` entry point that
wires structlog processors, stdlib handlers, and per-logger levels.
"""

import logging
import sys
from pathlib import Path
from typing import Any

import structlog

from synthorg.observability.config import DEFAULT_SINKS, LogConfig
from synthorg.observability.enums import LogLevel
from synthorg.observability.processors import sanitize_sensitive_fields
from synthorg.observability.sinks import build_handler

# Default per-logger levels applied when no config overrides are given.
_DEFAULT_LOGGER_LEVELS: tuple[tuple[str, LogLevel], ...] = (
    ("synthorg.core", LogLevel.INFO),
    ("synthorg.engine", LogLevel.DEBUG),
    ("synthorg.communication", LogLevel.INFO),
    ("synthorg.providers", LogLevel.INFO),
    ("synthorg.budget", LogLevel.INFO),
    ("synthorg.security", LogLevel.INFO),
    ("synthorg.memory", LogLevel.DEBUG),
    ("synthorg.tools", LogLevel.INFO),
    ("synthorg.api", LogLevel.INFO),
    ("synthorg.cli", LogLevel.INFO),
    ("synthorg.config", LogLevel.INFO),
    ("synthorg.templates", LogLevel.INFO),
)

# Processors shared between structlog and stdlib (foreign) chains.
_BASE_PROCESSORS: tuple[Any, ...] = (
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.stdlib.PositionalArgumentsFormatter(),
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
    structlog.processors.UnicodeDecoder(),
    sanitize_sensitive_fields,
)


def _build_shared_processors(
    *,
    enable_correlation: bool = True,
) -> list[Any]:
    """Build the shared processor chain for stdlib-originated log records.

    Applied via the ``ProcessorFormatter`` pre-chain to foreign
    (non-structlog) log records before the final renderer.

    Args:
        enable_correlation: Whether to include ``merge_contextvars``.

    Returns:
        A list of structlog processors for the foreign pre-chain.
    """
    processors: list[Any] = []
    if enable_correlation:
        processors.append(structlog.contextvars.merge_contextvars)
    processors.extend(_BASE_PROCESSORS)
    return processors


def _clear_root_handlers(root_logger: logging.Logger) -> None:
    """Remove and close all handlers from the root logger.

    Each handler is closed individually so a failure on one does not
    prevent cleanup of the remaining handlers.

    Args:
        root_logger: The stdlib root logger to clear.
    """
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            print(  # noqa: T201
                f"WARNING: Failed to close log handler {handler!r}",
                file=sys.stderr,
            )


def _configure_structlog(*, enable_correlation: bool = True) -> None:
    """Configure the structlog processor chain and logger factory.

    Args:
        enable_correlation: Whether to include ``merge_contextvars``.
    """
    processors: list[Any] = []
    if enable_correlation:
        processors.append(structlog.contextvars.merge_contextvars)
    processors.append(structlog.stdlib.filter_by_level)
    processors.extend(_BASE_PROCESSORS)
    processors.append(structlog.stdlib.ProcessorFormatter.wrap_for_formatter)

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def _attach_handlers(
    config: LogConfig,
    root_logger: logging.Logger,
    shared_processors: list[Any],
) -> None:
    """Build and attach a handler for each configured sink.

    Failures on individual sinks are logged to stderr and skipped so
    that the remaining sinks can still be initialised.

    Args:
        config: The logging configuration.
        root_logger: The stdlib root logger.
        shared_processors: Processor chain for the foreign pre-chain.
    """
    log_dir = Path(config.log_dir)
    for sink in config.sinks:
        try:
            handler = build_handler(
                sink=sink,
                log_dir=log_dir,
                foreign_pre_chain=shared_processors,
            )
            root_logger.addHandler(handler)
        except OSError, RuntimeError, ValueError:
            print(  # noqa: T201
                f"WARNING: Failed to initialise log sink "
                f"{sink!r}. This sink will be skipped.",
                file=sys.stderr,
            )


def _apply_logger_levels(config: LogConfig) -> None:
    """Apply default and config-override per-logger levels.

    Default levels are applied first, then any overrides from
    ``config.logger_levels`` take precedence.

    Args:
        config: The logging configuration with optional overrides.
    """
    for name, level in _DEFAULT_LOGGER_LEVELS:
        logging.getLogger(name).setLevel(level.value)

    for name, level in config.logger_levels:
        logging.getLogger(name).setLevel(level.value)


def configure_logging(config: LogConfig | None = None) -> None:
    """Configure the structured logging system.

    Sets up structlog processor chains, stdlib handlers, and per-logger
    levels.  This function is **idempotent** — calling it multiple times
    replaces the previous configuration without duplicating handlers.

    Args:
        config: Logging configuration.  When ``None``, uses sensible
            defaults with all standard sinks.
    """
    if config is None:
        config = LogConfig(sinks=DEFAULT_SINKS)

    # 1. Reset structlog to a clean state
    structlog.reset_defaults()

    # 2. Clear existing stdlib root handlers
    root_logger = logging.getLogger()
    _clear_root_handlers(root_logger)

    # 3. Set root logger level from config
    root_logger.setLevel(config.root_level.value)

    # 4. Build shared processor chain (foreign pre-chain)
    shared = _build_shared_processors(
        enable_correlation=config.enable_correlation,
    )

    # 5. Configure structlog main chain
    _configure_structlog(enable_correlation=config.enable_correlation)

    # 6. Build and attach handlers for each sink
    _attach_handlers(config, root_logger, shared)

    # 7. Apply per-logger levels
    _apply_logger_levels(config)
