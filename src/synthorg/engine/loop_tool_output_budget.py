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
from synthorg.observability.events.settings import SETTINGS_FETCH_FAILED
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

#: The registered default of ``engine.tool_output_max_chars``: roughly six
#: thousand tokens, enough for a long file or a full test log, and small
#: enough that a runaway listing cannot claim a fifth of a context window.
DEFAULT_TOOL_OUTPUT_MAX_CHARS: Final[int] = 24_000

_NAMESPACE: Final[str] = "engine"
_KEY: Final[str] = "tool_output_max_chars"
#: The head keeps the larger share: what a result IS is said first, and the
#: tail only needs enough room for how it ended.
_HEAD_SHARE: Final[float] = 0.7


def abbreviate_tool_output(content: str, *, max_chars: int) -> tuple[str, int]:
    """Keep *content*'s head and tail within *max_chars*.

    Args:
        content: The tool result as the tool returned it.
        max_chars: The ceiling; ``0`` means no ceiling.

    Returns:
        The content to append and how many characters were elided, which
        is ``0`` when the content fit.
    """
    total = len(content)
    if max_chars <= 0 or total <= max_chars:
        return content, 0
    marker_budget = len(_marker(total, total))
    keep = max(max_chars - marker_budget, 0)
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
        The configured ceiling, or :data:`DEFAULT_TOOL_OUTPUT_MAX_CHARS`
        when no resolver is wired or the read fails.
    """
    if resolver is None:
        return DEFAULT_TOOL_OUTPUT_MAX_CHARS
    try:
        return await resolver.get_int("engine", "tool_output_max_chars")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            SETTINGS_FETCH_FAILED,
            namespace=_NAMESPACE,
            key=_KEY,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return DEFAULT_TOOL_OUTPUT_MAX_CHARS


__all__ = [
    "DEFAULT_TOOL_OUTPUT_MAX_CHARS",
    "abbreviate_tool_output",
    "resolve_tool_output_max_chars",
]
