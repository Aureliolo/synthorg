"""A ceiling on what one tool result may put into the conversation.

A tool result is resent on every later turn of the run, so its size is paid
once per turn for the rest of the session rather than once. Compaction
recovers some of that late, after the result has already been carried
through the turns between; abbreviating it at the boundary where it enters
the conversation is what stops the spend at source. The head and the tail
are kept because that is where a result says what it is and how it ended
(a listing's first entries and its count, a log's start and its final
error), and an elision marker says how much was dropped so the agent can
narrow its next call rather than assume it saw everything.
"""

from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_TOOL_OUTPUT_CEILING_RAISED,
)
from synthorg.observability.events.settings import SETTINGS_FETCH_FAILED
from synthorg.settings.errors import SettingsRegistryError
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

_NAMESPACE: Final[str] = "engine"
_KEY: Final[str] = "tool_output_max_chars"


def _registered_default() -> int:
    """Read the ceiling's registered default, so the two cannot drift.

    Returns:
        The default the settings registry declares for the key.

    Raises:
        SettingsRegistryError: The key is not registered, which means the
            definitions module was renamed out from under this reader.
    """
    import synthorg.settings.definitions  # noqa: F401, PLC0415 -- registers the key

    definition = get_registry().get(_NAMESPACE, _KEY)
    if definition is None or definition.default is None:
        msg = f"{_NAMESPACE}.{_KEY} is not registered with a default"
        raise SettingsRegistryError(msg)
    return int(definition.default)


#: The registered default of ``engine.tool_output_max_chars``: roughly six
#: thousand tokens, enough for a long file or a full test log, and small
#: enough that a runaway listing cannot claim a fifth of a context window.
DEFAULT_TOOL_OUTPUT_MAX_CHARS: Final[int] = _registered_default()

#: The smallest ceiling that can still be honoured. The elision marker has to
#: fit inside the ceiling with room for something of the result beside it,
#: so a ceiling below this would emit MORE than it allows; a live value under
#: it is raised to it and said. ``0`` still means no ceiling.
MIN_TOOL_OUTPUT_MAX_CHARS: Final[int] = 256

#: The head keeps the larger share: what a result IS is said first, and the
#: tail only needs enough room for how it ended.
_HEAD_SHARE: Final[float] = 0.7


def abbreviate_tool_output(content: str, *, max_chars: int) -> tuple[str, int]:
    """Keep *content*'s head and tail within *max_chars*.

    Args:
        content: The tool result as the tool returned it.
        max_chars: The ceiling; ``0`` means no ceiling, and a positive value
            is at least :data:`MIN_TOOL_OUTPUT_MAX_CHARS`.

    Returns:
        The content to append and how many characters were elided, which
        is ``0`` when the content fit. The content never exceeds the
        ceiling.

    Raises:
        ValueError: A positive ceiling below the minimum, which no marker
            could fit inside.
    """
    total = len(content)
    if max_chars <= 0 or total <= max_chars:
        return content, 0
    if max_chars < MIN_TOOL_OUTPUT_MAX_CHARS:
        msg = (
            f"tool_output_max_chars={max_chars} is below the "
            f"{MIN_TOOL_OUTPUT_MAX_CHARS} the elision marker needs"
        )
        raise ValueError(msg)
    marker_budget = len(_marker(total, total))
    keep = max_chars - marker_budget
    head_len = int(keep * _HEAD_SHARE)
    tail_len = keep - head_len
    elided = total - keep
    tail = content[total - tail_len :] if tail_len else ""
    return f"{content[:head_len]}{_marker(elided, total)}{tail}", elided


def _marker(elided: int, total: int) -> str:
    return (
        f"\n[... {elided} of {total} characters elided by "
        f"{_NAMESPACE}.{_KEY}; narrow the call to see the rest ...]\n"
    )


async def resolve_tool_output_max_chars(
    resolver: ConfigResolverProtocol | None,
) -> int:
    """Resolve the live ceiling on one tool result.

    Read per turn rather than at construction, so an operator lowering it
    while watching a run drown in output reaches the very next tool call.

    Args:
        resolver: The engine's config resolver, or ``None``.

    Returns:
        The configured ceiling, raised to :data:`MIN_TOOL_OUTPUT_MAX_CHARS`
        when set positive but below it, or :data:`DEFAULT_TOOL_OUTPUT_MAX_CHARS`
        when no resolver is wired or the read fails.
    """
    if resolver is None:
        return DEFAULT_TOOL_OUTPUT_MAX_CHARS
    try:
        configured = await resolver.get_int("engine", "tool_output_max_chars")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- the registered default stands in for one
        # turn and the read is asked again on the next
        reraise_critical(exc)
        logger.warning(
            SETTINGS_FETCH_FAILED,
            namespace=_NAMESPACE,
            key=_KEY,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return DEFAULT_TOOL_OUTPUT_MAX_CHARS
    if 0 < configured < MIN_TOOL_OUTPUT_MAX_CHARS:
        logger.warning(
            EXECUTION_TOOL_OUTPUT_CEILING_RAISED,
            configured=configured,
            applied=MIN_TOOL_OUTPUT_MAX_CHARS,
        )
        return MIN_TOOL_OUTPUT_MAX_CHARS
    return configured


__all__ = [
    "DEFAULT_TOOL_OUTPUT_MAX_CHARS",
    "MIN_TOOL_OUTPUT_MAX_CHARS",
    "abbreviate_tool_output",
    "resolve_tool_output_max_chars",
]
