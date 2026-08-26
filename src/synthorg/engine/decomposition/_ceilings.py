# module-kind: code
"""The bounds a decomposition runs under: reading them, and classifying a hit.

Three settings bound a decomposition: a wall-clock ceiling per planning
session, a wall-clock ceiling for the whole recursive call, and how many
planning sessions that call may open. All three are read live so an operator
raising one applies to the next decomposition rather than the next restart,
and all three fall back to the definition's own default when the setting
cannot answer, because a bound nobody can read is not a licence to spend.

The two ceilings fire as ``TimeoutError``, which is also what a call INSIDE
can raise on its own, and telling those apart decides whether a caller may
usefully retry. Both halves live here because they are the same concern seen
from its two ends: what the bound is, and what it means when it is reached.

The session budget is the one that stops GRACEFULLY, which is why it exists
beside the ceilings rather than instead of one: running out of sessions
returns the tree as far as it got and leaves the units it could not split
saying so, while a ceiling raises and discards every level already paid for.
"""

from typing import Final

from synthorg.engine.errors import DecompositionError, DecompositionTimeoutError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_CEILING_UNREADABLE,
    DECOMPOSITION_FAILED,
)
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

#: Mirrors ``coordination.decomposition_timeout_seconds``. Held here because a
#: harness runs with no settings at all, and the bound has to stand there too.
DEFAULT_SESSION_CEILING_SECONDS: Final[float] = 600.0

#: Mirrors ``coordination.decomposition_tree_timeout_seconds``, for the same
#: reason.
DEFAULT_TREE_CEILING_SECONDS: Final[float] = 14400.0

#: Mirrors ``coordination.decomposition_tree_max_sessions``, for the same
#: reason.
DEFAULT_TREE_MAX_SESSIONS: Final[int] = 40


async def tree_session_budget(resolver: ConfigResolverProtocol | None) -> int:
    """Read how many planning sessions one whole tree may spend.

    The bound in the unit that costs money: recursion is a planning session
    per node, so this composes with the per-session token ceiling into a real
    token bound on a tree of unknown shape.

    Args:
        resolver: The live settings reader, or ``None`` in a harness.

    Returns:
        The number of planning sessions available to the tree.
    """
    if resolver is None:
        return DEFAULT_TREE_MAX_SESSIONS
    try:
        return await resolver.get_int("coordination", "decomposition_tree_max_sessions")
    except (SettingNotFoundError, ValueError) as exc:
        # lint-allow: swallow-ok -- a budget the setting cannot answer for is
        # the definition's default by construction, and a bound still stands,
        # so the runaway this exists to catch is caught either way
        logger.warning(
            DECOMPOSITION_CEILING_UNREADABLE,
            setting="decomposition_tree_max_sessions",
            fallback_sessions=DEFAULT_TREE_MAX_SESSIONS,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return DEFAULT_TREE_MAX_SESSIONS


async def ceiling_seconds(
    resolver: ConfigResolverProtocol | None, key: str, default: float
) -> float:
    """Read the wall-clock ceiling *key* in force for this decomposition.

    Only the two failures the resolver documents fall back: the key is not
    registered, or its stored value is not a float. Both are facts about the
    setting, unchanged until someone changes it, and the default is the honest
    answer to either. Anything else, a dead settings store above all,
    propagates: it is transient, the ceiling is re-read per node of a
    recursion, and swallowing it silently substitutes a bound nobody chose for
    as long as the store stays down. A sweep arming a ceiling in the tens of
    thousands of seconds and quietly getting the default back is exactly the
    failure the arming exists to prevent.

    Args:
        resolver: The live settings reader, or ``None`` in a harness.
        key: The coordination setting naming the ceiling.
        default: The definition's own default, in force when there is no
            resolver or the setting cannot answer.

    Returns:
        The ceiling, in seconds.
    """
    if resolver is None:
        return default
    try:
        return await resolver.get_float("coordination", key)
    except (SettingNotFoundError, ValueError) as exc:
        # lint-allow: swallow-ok -- a ceiling the setting cannot answer for is
        # the definition's default by construction, and a bound still stands,
        # so the unbounded wait this exists to prevent cannot happen either way
        logger.warning(
            DECOMPOSITION_CEILING_UNREADABLE,
            setting=key,
            fallback_seconds=default,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return default


def timeout_failure(
    exc: TimeoutError,
    *,
    task_id: str,
    strategy: str,
    expired: bool,
    ceiling: str,
) -> DecompositionError:
    """Classify a ``TimeoutError`` and log it, returning what to raise.

    The distinction is the whole point: a ceiling is unchanged on the next
    attempt, so retrying pays it again to reach the same place, while a call
    that timed out on its own is the ordinary transient a retry exists for.
    Both arrive at the same handler as the same type, so only the scope can
    say which happened.

    Args:
        exc: What was caught.
        task_id: The task being decomposed, for the log line.
        strategy: Which planner was running, for the log line.
        expired: Whether the ceiling's own scope is what fired.
        ceiling: Which ceiling the site guards, for the log line.

    Returns:
        The error to raise: the non-retryable type when the ceiling fired, the
        ordinary one when something inside timed out on its own.
    """
    msg = (
        f"Decomposition outran its {ceiling} wall-clock ceiling"
        if expired
        else "A call inside the decomposition timed out"
    )
    logger.warning(
        DECOMPOSITION_FAILED,
        task_id=task_id,
        strategy=strategy,
        error_type=type(exc).__name__,
        error=msg,
    )
    return DecompositionTimeoutError(msg) if expired else DecompositionError(msg)


__all__ = [
    "DEFAULT_SESSION_CEILING_SECONDS",
    "DEFAULT_TREE_CEILING_SECONDS",
    "DEFAULT_TREE_MAX_SESSIONS",
    "ceiling_seconds",
    "timeout_failure",
    "tree_session_budget",
]
