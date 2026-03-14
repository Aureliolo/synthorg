"""Memory context formatter — converts ranked memories to ChatMessages.

Handles token budget enforcement via greedy packing: iterates by rank,
skips entries that exceed the remaining budget, and continues with
smaller entries to maximise context within the token limit.
"""

from synthorg.memory.injection import (
    InjectionPoint,
    TokenEstimator,
)
from synthorg.memory.ranking import ScoredMemory  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_FORMAT_COMPLETE,
    MEMORY_FORMAT_INVALID_INJECTION_POINT,
    MEMORY_TOKEN_BUDGET_EXCEEDED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage

logger = get_logger(__name__)

MEMORY_BLOCK_START = "--- AGENT MEMORY ---"
"""Delimiter marking the start of memory context."""

MEMORY_BLOCK_END = "--- END MEMORY ---"
"""Delimiter marking the end of memory context."""

_INJECTION_POINT_TO_ROLE: dict[InjectionPoint, MessageRole] = {
    InjectionPoint.SYSTEM: MessageRole.SYSTEM,
    InjectionPoint.USER: MessageRole.USER,
}


def _format_line(memory: ScoredMemory) -> str:
    """Format a single memory entry as a display line.

    Format: ``[{category} | score: {score:.2f}] {content}``
    Shared entries are prefixed with ``[shared]``.

    Args:
        memory: Scored memory entry.

    Returns:
        Formatted line string.
    """
    shared_prefix = "[shared] " if memory.is_shared else ""
    category = memory.entry.category.value
    score = memory.combined_score
    # Sanitise content to prevent delimiter injection — replace end
    # delimiter inside memory content so it cannot break the block.
    content = memory.entry.content.replace(MEMORY_BLOCK_END, "[DELIMITER_REDACTED]")
    return f"{shared_prefix}[{category} | score: {score:.2f}] {content}"


def format_memory_context(
    memories: tuple[ScoredMemory, ...],
    *,
    estimator: TokenEstimator,
    token_budget: int,
    injection_point: InjectionPoint = InjectionPoint.SYSTEM,
) -> tuple[ChatMessage, ...]:
    """Format ranked memories into ChatMessage(s), respecting token budget.

    Uses greedy packing: iterates memories by rank order and includes
    each one if it fits within the remaining budget.

    Args:
        memories: Pre-ranked memories (highest score first).
        estimator: Token estimation implementation.
        token_budget: Maximum tokens for the memory block.
        injection_point: Role for the output message.

    Returns:
        Tuple containing a single ``ChatMessage`` with formatted
        memories, or empty tuple if no memories fit or input is empty.
    """
    if not memories or token_budget <= 0:
        return ()

    # Account for both newlines in the final block format:
    # "{START}\n{body}\n{END}"
    delimiter_text = f"{MEMORY_BLOCK_START}\n\n{MEMORY_BLOCK_END}"
    delimiter_tokens = estimator.estimate_tokens(delimiter_text)

    remaining = token_budget - delimiter_tokens
    if remaining <= 0:
        logger.debug(
            MEMORY_TOKEN_BUDGET_EXCEEDED,
            budget=token_budget,
            delimiter_tokens=delimiter_tokens,
            reason="budget exhausted by delimiters",
        )
        return ()

    # Greedy packing: iterate by rank, include memories that fit.
    # Entries too large for the remaining budget are skipped (not
    # stopping), allowing shorter lower-ranked entries to fill the
    # remaining space.
    included_lines: list[str] = []
    for memory in memories:
        line = _format_line(memory)
        line_tokens = estimator.estimate_tokens(line)
        # Account for the newline separator added by "\n".join()
        separator_cost = estimator.estimate_tokens("\n") if included_lines else 0
        if line_tokens + separator_cost <= remaining:
            included_lines.append(line)
            remaining -= line_tokens + separator_cost
        else:
            logger.debug(
                MEMORY_TOKEN_BUDGET_EXCEEDED,
                budget=token_budget,
                remaining=remaining,
                line_tokens=line_tokens,
                skipped_memory_id=memory.entry.id,
            )

    if not included_lines:
        logger.debug(
            MEMORY_TOKEN_BUDGET_EXCEEDED,
            budget=token_budget,
            total_candidates=len(memories),
            reason="no memories fit within budget",
        )
        return ()

    body = "\n".join(included_lines)
    block = f"{MEMORY_BLOCK_START}\n{body}\n{MEMORY_BLOCK_END}"

    try:
        role = _INJECTION_POINT_TO_ROLE[injection_point]
    except KeyError:
        msg = f"Unsupported injection point: {injection_point!r}"
        logger.warning(
            MEMORY_FORMAT_INVALID_INJECTION_POINT,
            injection_point=injection_point,
            reason=msg,
        )
        raise ValueError(msg) from None
    message = ChatMessage(role=role, content=block)

    logger.debug(
        MEMORY_FORMAT_COMPLETE,
        included_count=len(included_lines),
        total_candidates=len(memories),
        token_budget=token_budget,
        injection_point=injection_point.value,
    )

    return (message,)
