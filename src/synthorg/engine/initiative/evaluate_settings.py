# module-kind: code
"""Live settings reads for the EVALUATE stage.

Every value here is re-read per evaluation rather than captured at wiring, so
an operator retuning the judgement's turn cap, spend ceiling or wall-clock
budget applies it to the next run instead of the next boot.

Each read is best-effort against the registered default: refusing to judge a
delivered initiative because a settings read failed would trade a real verdict
for the absence of one, and the tail's fail-closed posture already parks a plan
that produced no verdict at all.
"""

from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.initiative.evaluate_session import EvaluationSessionConfig
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.initiative import INITIATIVE_EVALUATION_SKIPPED
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

#: Namespace every key below lives in.
_NS: Final[str] = "engine"

#: Fallbacks for when no resolver is wired or a read fails.
DEFAULT_MAX_TURNS: Final[int] = 10
DEFAULT_COST_CEILING: Final[float] = 1.0
DEFAULT_TIMEOUT_SECONDS: Final[float] = 300.0


async def session_config(
    resolver: ConfigResolverProtocol | None,
) -> EvaluationSessionConfig:
    """Build the session configuration from live settings.

    Args:
        resolver: The stage's config resolver, or ``None`` when it was built
            without one (test harness, anonymous boot).

    Returns:
        An :class:`EvaluationSessionConfig` carrying the current turn cap and
        cost ceiling, so an operator's change applies to the next evaluation
        without a restart.
    """
    return EvaluationSessionConfig(
        max_turns=await resolve_int(
            resolver, "evaluation_session_max_turns", DEFAULT_MAX_TURNS
        ),
        cost_ceiling=await resolve_float(
            resolver, "evaluation_session_cost_ceiling", DEFAULT_COST_CEILING
        ),
    )


async def timeout_seconds(resolver: ConfigResolverProtocol | None) -> float:
    """Resolve the per-evaluation wall-clock ceiling.

    Args:
        resolver: The stage's config resolver, or ``None``.

    Returns:
        The configured ceiling, or the default when unresolvable or
        non-positive (a zero ceiling would abandon every judgement instantly).
    """
    resolved = await resolve_float(
        resolver, "evaluation_session_timeout_seconds", DEFAULT_TIMEOUT_SECONDS
    )
    return resolved if resolved > 0 else DEFAULT_TIMEOUT_SECONDS


async def resolve_int(
    resolver: ConfigResolverProtocol | None,
    key: str,
    default: int,
) -> int:
    """Resolve a live ``engine.<key>`` int, falling back to *default*.

    Args:
        resolver: The stage's config resolver, or ``None``.
        key: The setting key within the ``engine`` namespace.
        default: The registered default to fall back to.

    Returns:
        The configured value, or *default* when no resolver is wired or the
        read fails.
    """
    if resolver is None:
        return default
    try:
        return await resolver.get_int(_NS, key)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort settings read
        reraise_critical(exc)
        _log_degraded(key, exc)
        return default


async def resolve_float(
    resolver: ConfigResolverProtocol | None,
    key: str,
    default: float,
) -> float:
    """Resolve a live ``engine.<key>`` float, falling back to *default*.

    Args:
        resolver: The stage's config resolver, or ``None``.
        key: The setting key within the ``engine`` namespace.
        default: The registered default to fall back to.

    Returns:
        The configured value, or *default* when no resolver is wired or the
        read fails.
    """
    if resolver is None:
        return default
    try:
        return await resolver.get_float(_NS, key)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort settings read
        reraise_critical(exc)
        _log_degraded(key, exc)
        return default


def _log_degraded(key: str, exc: Exception) -> None:
    """Warn that a best-effort ``engine.<key>`` read fell back to a default."""
    logger.warning(
        INITIATIVE_EVALUATION_SKIPPED,
        key=key,
        reason="settings_read_degraded",
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
    )


__all__ = [
    "DEFAULT_COST_CEILING",
    "DEFAULT_MAX_TURNS",
    "DEFAULT_TIMEOUT_SECONDS",
    "resolve_float",
    "resolve_int",
    "session_config",
    "timeout_seconds",
]
