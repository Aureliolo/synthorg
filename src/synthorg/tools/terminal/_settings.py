# module-kind: code
"""Live settings reads for the terminal tools.

Read per command rather than baked in when the tool is built, because the
ceiling on one command is the difference between a deployment that can install
a dependency and one that cannot, and an operator who raises it should not have
to restart anything to find out whether it was enough.
"""

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.terminal import TERMINAL_COMMAND_FAILED
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)


async def resolve_shell_command_timeout(
    resolver: ConfigResolverProtocol | None,
    *,
    fallback: float,
) -> float:
    """Resolve the wall-clock ceiling for one shell command.

    Degrades to *fallback* rather than raising: a settings-backend hiccup must
    not turn every agent command into an error, and the fallback is the
    deployment's configured default rather than a number invented here.

    Args:
        resolver: Live settings resolver, or ``None`` when none is wired.
        fallback: The configured default to use when nothing resolves.

    Returns:
        The ceiling in seconds; *fallback* when the read is unavailable or
        yields a non-positive value.
    """
    if resolver is None:
        return fallback
    try:
        resolved = await resolver.get_float("tools", "shell_command_timeout_seconds")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort settings read; the command
        # still runs, under the configured default
        reraise_critical(exc)
        logger.warning(
            TERMINAL_COMMAND_FAILED,
            key="shell_command_timeout_seconds",
            reason="settings_read_degraded",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return fallback
    return resolved if resolved > 0 else fallback


async def resolve_shell_command_background_enabled(
    resolver: ConfigResolverProtocol | None,
    *,
    fallback: bool,
) -> bool:
    """Resolve whether ``shell_command`` may be run with ``background=true``.

    Read only when a caller actually asks to background a command: an
    operator can disable new backgrounded jobs without touching ordinary
    foreground commands, without a rebuild.

    Args:
        resolver: Live settings resolver, or ``None`` when none is wired.
        fallback: The configured default to use when nothing resolves.

    Returns:
        Whether backgrounding is enabled; *fallback* when the read is
        unavailable.
    """
    if resolver is None:
        return fallback
    try:
        return await resolver.get_bool("tools", "shell_command_background_enabled")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort settings read; the
        # configured default stands
        reraise_critical(exc)
        logger.warning(
            TERMINAL_COMMAND_FAILED,
            key="shell_command_background_enabled",
            reason="settings_read_degraded",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return fallback


async def resolve_shell_command_background_max_duration_seconds(
    resolver: ConfigResolverProtocol | None,
    *,
    fallback: float,
) -> float:
    """Resolve the duration ceiling a NEW background job starts under.

    Read once at job start (not on every later poll): the ceiling in force
    when a job starts governs its own lifetime, exactly like the foreground
    ``shell_command_timeout_seconds`` precedent, so a later operator change
    only affects jobs started after it.

    Args:
        resolver: Live settings resolver, or ``None`` when none is wired.
        fallback: The configured default to use when nothing resolves.

    Returns:
        The ceiling in seconds; *fallback* when the read is unavailable or
        yields a non-positive value.
    """
    if resolver is None:
        return fallback
    try:
        resolved = await resolver.get_float(
            "tools", "shell_command_background_max_duration_seconds"
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort settings read; the job
        # still starts, under the configured default
        reraise_critical(exc)
        logger.warning(
            TERMINAL_COMMAND_FAILED,
            key="shell_command_background_max_duration_seconds",
            reason="settings_read_degraded",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return fallback
    return resolved if resolved > 0 else fallback


__all__ = [
    "resolve_shell_command_background_enabled",
    "resolve_shell_command_background_max_duration_seconds",
    "resolve_shell_command_timeout",
]
