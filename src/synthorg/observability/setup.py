"""Logging system setup and configuration.

Provides the idempotent :func:`configure_logging` entry point that
wires structlog processors, stdlib handlers, and per-logger levels.
"""

import logging
import os
import sys
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

import structlog

from synthorg.observability.config import DEFAULT_SINKS, LogConfig, SinkConfig
from synthorg.observability.enums import LogLevel, SinkType
from synthorg.observability.log_trace_correlation import inject_trace_context
from synthorg.observability.processors import (
    sanitize_sensitive_fields,
    scrub_event_fields,
)
from synthorg.observability.sinks import SINK_ROUTING, build_handler

# Default per-logger levels applied when no config overrides are given.
# CFG-1 audit: ``synthorg.engine`` and ``synthorg.memory`` previously
# defaulted to DEBUG which produces noisy disk output and can surface
# PII in shared environments. Operators who want DEBUG for those
# namespaces opt in via the ``observability/per_logger_levels`` setting
# (or the ``config.logger_levels`` YAML key); ``SYNTHORG_LOG_LEVEL``
# only adjusts the CONSOLE sink level and does NOT override these
# per-logger defaults.
_DEFAULT_LOGGER_LEVELS: tuple[tuple[str, LogLevel], ...] = (
    ("synthorg.core", LogLevel.INFO),
    ("synthorg.engine", LogLevel.INFO),
    ("synthorg.communication", LogLevel.INFO),
    ("synthorg.providers", LogLevel.INFO),
    ("synthorg.budget", LogLevel.INFO),
    ("synthorg.security", LogLevel.INFO),
    ("synthorg.memory", LogLevel.INFO),
    ("synthorg.tools", LogLevel.INFO),
    ("synthorg.api", LogLevel.INFO),
    ("synthorg.cli", LogLevel.INFO),
    ("synthorg.config", LogLevel.INFO),
    ("synthorg.templates", LogLevel.INFO),
)

# Third-party loggers that add their own handlers or emit noisy DEBUG
# output.  Clearing their handlers and enforcing ``propagate = True``
# routes all messages through the root logger where our structlog sinks
# capture them uniformly.
# Level is set to WARNING by default -- our provider/persistence layers
# already log meaningful events at the appropriate level.
_THIRD_PARTY_LOGGER_LEVELS: tuple[tuple[str, LogLevel], ...] = (
    ("LiteLLM", LogLevel.WARNING),
    ("LiteLLM Router", LogLevel.WARNING),
    ("LiteLLM Proxy", LogLevel.WARNING),
    ("aiosqlite", LogLevel.WARNING),
    ("httpcore", LogLevel.WARNING),
    ("httpcore.http11", LogLevel.WARNING),
    ("httpcore.connection", LogLevel.WARNING),
    ("httpx", LogLevel.WARNING),
    ("uvicorn", LogLevel.WARNING),
    ("uvicorn.error", LogLevel.WARNING),
    ("uvicorn.access", LogLevel.WARNING),
    ("anyio", LogLevel.WARNING),
    ("multipart", LogLevel.WARNING),
    ("faker", LogLevel.WARNING),
    ("faker.factory", LogLevel.WARNING),
)

# Processors shared between structlog and stdlib (foreign) chains.
# Trace correlation lives in ``_build_shared_processors`` so that
# ``enable_correlation=False`` also suppresses ``trace_id``/``span_id``
# injection -- otherwise the flag would disable ``merge_contextvars``
# but still emit trace fields whenever an OTel span is active.
_BASE_PROCESSORS: tuple[Any, ...] = (
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.stdlib.PositionalArgumentsFormatter(),
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.UnicodeDecoder(),
    # Realise ``exc_info`` into a string under the ``exception``
    # key BEFORE ``scrub_event_fields`` runs, so the scrubber also
    # sees (and masks) any credential patterns embedded in the
    # traceback. Without this, ``logger.exception(...)`` would skip
    # the scrubber because ``exc_info`` is a tuple at that point,
    # not a string.
    structlog.processors.format_exc_info,
    sanitize_sensitive_fields,
    # Deep-scrub credential patterns out of every string value.
    # Runs after ``sanitize_sensitive_fields`` so already-redacted keys
    # stay redacted; this layer catches leaks via ``error=str(exc)`` and
    # the realised traceback string the field-name scrubber cannot reach.
    scrub_event_fields,
)


def _build_shared_processors(
    *,
    enable_correlation: bool = True,
) -> list[Any]:
    """Build the shared processor chain for stdlib-originated log records.

    Applied via the ``ProcessorFormatter`` pre-chain to foreign
    (non-structlog) log records before the final renderer.

    Args:
        enable_correlation: Whether to include ``merge_contextvars``
            and trace-id/span-id injection.

    Returns:
        A list of structlog processors for the foreign pre-chain.
    """
    processors: list[Any] = []
    if enable_correlation:
        processors.append(structlog.contextvars.merge_contextvars)
        processors.append(inject_trace_context)
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
                flush=True,
            )


def _configure_structlog(*, enable_correlation: bool = True) -> None:
    """Configure the structlog processor chain and logger factory.

    Args:
        enable_correlation: Whether to include ``merge_contextvars``.
    """
    processors: list[Any] = []
    if enable_correlation:
        processors.append(structlog.contextvars.merge_contextvars)
        processors.append(inject_trace_context)
    processors.append(structlog.stdlib.filter_by_level)
    processors.extend(_BASE_PROCESSORS)
    processors.append(structlog.stdlib.ProcessorFormatter.wrap_for_formatter)

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        # Disabled: cached proxies retain stale processor chains after
        # reset_defaults() + reconfigure, losing log routing to new handlers.
        cache_logger_on_first_use=False,
    )


_CRITICAL_SINKS = frozenset({"audit.log", "access.log"})


def _handle_sink_failure(
    sink: SinkConfig,
    exc: Exception,
) -> None:
    """Handle a sink initialisation failure.

    Critical sinks (audit, access) cause a hard failure.  Others are
    skipped with a stderr warning.

    Raises:
        RuntimeError: If the failed sink is critical.
    """
    if sink.file_path in _CRITICAL_SINKS:
        print(  # noqa: T201
            f"CRITICAL: Log sink '{sink.file_path}' could not "
            f"be initialised: {exc}. Refusing to start with "
            "missing audit/access logs.",
            file=sys.stderr,
            flush=True,
        )
        msg = (
            f"Critical log sink '{sink.file_path}' could not be "
            "initialised. Refusing to start with missing "
            "audit/access logs."
        )
        raise RuntimeError(msg) from exc
    print(  # noqa: T201
        f"WARNING: Failed to initialise log sink "
        f"{sink!r}: {exc}. This sink will be skipped.",
        file=sys.stderr,
        flush=True,
    )


def _attach_handlers(
    config: LogConfig,
    root_logger: logging.Logger,
    shared_processors: list[Any],
    *,
    routing_overrides: Mapping[str, tuple[str, ...]] | None = None,
) -> None:
    """Build and attach a handler for each configured sink.

    Failures on individual sinks are logged to stderr and skipped so
    that the remaining sinks can still be initialised.  Critical sinks
    cause a hard failure via :func:`_handle_sink_failure`.

    Args:
        config: The logging configuration.
        root_logger: The stdlib root logger.
        shared_processors: Processor chain for the foreign pre-chain.
        routing_overrides: Optional extra routing entries (e.g. from
            custom sinks) merged with the default ``SINK_ROUTING``.
            An empty mapping is treated the same as ``None``.

    Raises:
        RuntimeError: If a critical sink fails to initialise.
    """
    effective_routing: Mapping[str, tuple[str, ...]] | None = None
    if routing_overrides:
        merged = dict(SINK_ROUTING)
        merged.update(routing_overrides)
        effective_routing = MappingProxyType(merged)

    log_dir = Path(config.log_dir)
    for sink in config.sinks:
        try:
            handler = build_handler(
                sink=sink,
                log_dir=log_dir,
                foreign_pre_chain=shared_processors,
                routing=effective_routing,
            )
            root_logger.addHandler(handler)
        except (OSError, RuntimeError, ValueError) as exc:
            _handle_sink_failure(sink, exc)


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


def _tame_third_party_loggers() -> None:
    """Remove third-party handlers and silence noisy loggers.

    Libraries like LiteLLM attach their own ``StreamHandler`` at import
    time, causing duplicate output in Docker logs (once via the library
    handler, once via root propagation through our structlog sinks).

    This function strips those handlers so all output flows exclusively
    through the structured logging pipeline. It also suppresses
    LiteLLM's ``print_verbose()`` raw ``print()`` calls by setting
    ``set_verbose = False`` at configuration time.

    The LiteLLM module-level attribute suppression (``set_verbose``,
    ``suppress_debug_info``) only executes when ``litellm`` is already
    imported, avoiding expensive import side-effects.  Handler cleanup
    and level enforcement run unconditionally -- ``logging.getLogger()``
    does not trigger library imports.
    """
    # Suppress LiteLLM's raw print() output if already imported.
    _litellm = sys.modules.get("litellm")
    if _litellm is not None:
        _litellm.set_verbose = False  # type: ignore[attr-defined]
        _litellm.suppress_debug_info = True  # type: ignore[attr-defined]

    for name, level in _THIRD_PARTY_LOGGER_LEVELS:
        lg = logging.getLogger(name)
        for handler in lg.handlers[:]:
            lg.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                print(  # noqa: T201
                    f"WARNING: Failed to close third-party log handler "
                    f"{handler!r} on logger {name!r}",
                    file=sys.stderr,
                    flush=True,
                )
        lg.setLevel(level.value)
        lg.propagate = True


def _apply_console_level_override(config: LogConfig) -> LogConfig:
    """Override the console sink level from the configured override.

    Resolves the override through the documented chain for the
    ``observability.log_level_console`` registry entry, minus the DB
    leg (the settings service is not yet available at this bootstrap
    site -- DB-driven runtime updates flow through a future
    ``SettingsChangeDispatcher`` subscriber, tracked as a follow-up):

    1. ``SYNTHORG_LOG_LEVEL`` env var (operator-facing override)
    2. ``LogConfig.console_level`` (YAML, ``logging.console_level``)
    3. unset -- per-sink / root_level default applies

    Invalid values fall back to INFO with a stderr warning.

    Args:
        config: Current logging configuration.

    Returns:
        Possibly updated config with the console sink level overridden.
    """
    env_raw = os.environ.get("SYNTHORG_LOG_LEVEL", "").strip()
    yaml_raw = config.console_level.strip()
    raw = env_raw or yaml_raw
    source = "env" if env_raw else "yaml" if yaml_raw else None
    if not raw:
        return config

    try:
        level = LogLevel(raw.upper())
    except ValueError:
        valid = ", ".join(lvl.value.lower() for lvl in LogLevel)
        print(  # noqa: T201
            f"WARNING: Invalid console-level override {raw!r} "
            f"(source={source}). Valid values: {valid}. "
            "Falling back to INFO.",
            file=sys.stderr,
            flush=True,
        )
        level = LogLevel.INFO

    found_console = False
    new_sinks: list[SinkConfig] = []
    for sink in config.sinks:
        if sink.sink_type == SinkType.CONSOLE:
            found_console = True
            new_sinks.append(sink.model_copy(update={"level": level}))
        else:
            new_sinks.append(sink)
    if not found_console:
        print(  # noqa: T201
            f"WARNING: console-level override {raw!r} (source={source}) "
            "set but no CONSOLE sink found in config -- override has "
            "no effect.",
            file=sys.stderr,
            flush=True,
        )
    return config.model_copy(update={"sinks": tuple(new_sinks)})


def configure_logging(
    config: LogConfig | None = None,
    *,
    routing_overrides: Mapping[str, tuple[str, ...]] | None = None,
) -> None:
    """Configure the structured logging system.

    Sets up structlog processor chains, stdlib handlers, and per-logger
    levels.  This function is **idempotent** -- calling it multiple times
    replaces the previous configuration without duplicating handlers.

    Respects the ``SYNTHORG_LOG_LEVEL`` env var to override the console
    sink level (useful for Docker deployments).

    Args:
        config: Logging configuration.  When ``None``, uses sensible
            defaults with all standard sinks.
        routing_overrides: Optional extra logger-name routing entries
            (e.g. from custom sinks) merged with the default
            ``SINK_ROUTING`` table.

    Raises:
        RuntimeError: If a critical sink fails to initialise.
    """
    if config is None:
        config = LogConfig(sinks=DEFAULT_SINKS)

    config = _apply_console_level_override(config)

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
    _attach_handlers(
        config,
        root_logger,
        shared,
        routing_overrides=routing_overrides,
    )

    # 7. Tame third-party loggers (clear duplicate handlers, set defaults)
    _tame_third_party_loggers()

    # 8. Apply per-logger levels (after taming so user overrides take precedence)
    _apply_logger_levels(config)
