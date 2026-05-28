"""Memory context formatter: converts ranked memories to ChatMessages.

Handles token budget enforcement via greedy packing: iterates by rank,
skips entries that exceed the remaining budget, and continues with
smaller entries to maximise context within the token limit.

Each memory entry is wrapped under ``TAG_MEMORY_ENTRY`` via
:func:`wrap_untrusted` so an attacker who plants a stored memory cannot
break out of the fence and inject system-prompt-level instructions.
Consumers that splice the formatted messages into an LLM call must
append :func:`untrusted_content_directive` for ``TAG_MEMORY_ENTRY`` to
their system prompt: :func:`format_memory_context_with_directive` is
the canonical helper that bundles both steps.
"""

from synthorg.engine.prompt_safety import (
    TAG_MEMORY_ENTRY,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.memory.injection import (
    InjectionPoint,
    TokenEstimator,
)
from synthorg.memory.ranking import ScoredMemory
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_FORMAT_COMPLETE,
    MEMORY_FORMAT_INVALID_INJECTION_POINT,
    MEMORY_TOKEN_BUDGET_EXCEEDED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage

logger = get_logger(__name__)

_INJECTION_POINT_TO_ROLE: dict[InjectionPoint, MessageRole] = {
    InjectionPoint.SYSTEM: MessageRole.SYSTEM,
    InjectionPoint.USER: MessageRole.USER,
}


def _format_entry(memory: ScoredMemory) -> str:
    """Format and fence a single memory entry under ``TAG_MEMORY_ENTRY``.

    Format of the inner line: ``[{category} | score: {score:.2f}] {content}``.
    Shared entries are prefixed with ``[shared]``. The inner line is
    then wrapped via :func:`wrap_untrusted` so any literal
    ``</memory-entry>`` inside the stored content (in any case variant)
    cannot break out of the fence.

    Args:
        memory: Scored memory entry.

    Returns:
        Fenced ``<memory-entry>...</memory-entry>`` block.
    """
    shared_prefix = "[shared] " if memory.is_shared else ""
    category = memory.entry.category.value
    score = memory.combined_score
    inner = f"{shared_prefix}[{category} | score: {score:.2f}] {memory.entry.content}"
    return wrap_untrusted(TAG_MEMORY_ENTRY, inner)


def _format_memory_context(
    memories: tuple[ScoredMemory, ...],
    *,
    estimator: TokenEstimator,
    token_budget: int,
    injection_point: InjectionPoint = InjectionPoint.SYSTEM,
) -> tuple[ChatMessage, ...]:
    """Format ranked memories into ChatMessage(s), respecting token budget.

    Uses greedy packing: iterates memories by rank order and includes
    each one if it fits within the remaining budget. Each included
    entry is individually wrapped under ``TAG_MEMORY_ENTRY``.

    Private helper: callers MUST go through
    :func:`format_memory_context_with_directive`, which bundles the
    untrusted-content directive that tells the model the memory
    blocks are data, not instructions. Exposing the bare formatter
    here would let a caller forget the directive and reintroduce a
    prompt-injection surface this module is hardened to close.

    Args:
        memories: Pre-ranked memories (highest score first).
        estimator: Token estimation implementation.
        token_budget: Maximum tokens for the memory block.
        injection_point: Role for the output message.

    Returns:
        Tuple containing a single ``ChatMessage`` with formatted
        memories, or empty tuple if no memories fit or input is empty.

    Raises:
        ValueError: If an argument fails domain validation.
    """
    if not memories or token_budget <= 0:
        return ()

    # Greedy packing: iterate by rank, include memories that fit.
    # Entries too large for the remaining budget are skipped (not
    # stopping), allowing shorter lower-ranked entries to fill the
    # remaining space. The wrap fence is counted as part of each
    # entry's token cost via ``_format_entry``.
    remaining = token_budget
    included_blocks: list[str] = []
    for memory in memories:
        block = _format_entry(memory)
        block_tokens = estimator.estimate_tokens(block)
        separator_cost = estimator.estimate_tokens("\n") if included_blocks else 0
        if block_tokens + separator_cost <= remaining:
            included_blocks.append(block)
            remaining -= block_tokens + separator_cost
        else:
            logger.debug(
                MEMORY_TOKEN_BUDGET_EXCEEDED,
                budget=token_budget,
                remaining=remaining,
                line_tokens=block_tokens,
                skipped_memory_id=memory.entry.id,
            )

    if not included_blocks:
        logger.debug(
            MEMORY_TOKEN_BUDGET_EXCEEDED,
            budget=token_budget,
            total_candidates=len(memories),
            reason="no memories fit within budget",
        )
        return ()

    content = "\n".join(included_blocks)

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
    message = ChatMessage(role=role, content=content)

    logger.debug(
        MEMORY_FORMAT_COMPLETE,
        included_count=len(included_blocks),
        total_candidates=len(memories),
        token_budget=token_budget,
        injection_point=injection_point.value,
    )

    return (message,)


def format_memory_context_with_directive(
    memories: tuple[ScoredMemory, ...],
    *,
    estimator: TokenEstimator,
    token_budget: int,
    injection_point: InjectionPoint = InjectionPoint.SYSTEM,
) -> tuple[ChatMessage, ...]:
    """Format memories with the untrusted-content directive prepended.

    Calls the private :func:`_format_memory_context` and prepends a
    SYSTEM-role :class:`ChatMessage` carrying the untrusted-content
    directive for ``TAG_MEMORY_ENTRY``. Returns an empty tuple when
    no memories fit.

    This is the only public memory-formatter entry point: the
    directive is the contract that tells the model the memory
    blocks are data, not instructions.

    Args:
        memories: Pre-ranked memories (highest score first).
        estimator: Token estimation implementation.
        token_budget: Maximum tokens for the memory block.
        injection_point: Role for the memory message.

    Returns:
        ``(directive_message, memory_message)`` on success, or empty
        tuple when no memories fit (so the directive does not appear
        on its own).
    """
    memory_messages = _format_memory_context(
        memories,
        estimator=estimator,
        token_budget=token_budget,
        injection_point=injection_point,
    )
    if not memory_messages:
        return ()
    directive = ChatMessage(
        role=MessageRole.SYSTEM,
        content=untrusted_content_directive((TAG_MEMORY_ENTRY,)),
    )
    return (directive, *memory_messages)
