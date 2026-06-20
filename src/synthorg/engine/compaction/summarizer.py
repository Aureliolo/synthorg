"""Oldest-turns summarization compaction callback factory.

Creates a ``CompactionCallback`` that archives the oldest conversation
turns into a summary message when the context fill level exceeds a
configurable threshold.
"""

from typing import Final

from synthorg.core.task_enums import Complexity
from synthorg.core.types import NotBlankStr
from synthorg.engine.compaction.epistemic import (
    extract_marker_sentences,
    should_preserve_message,
)
from synthorg.engine.compaction.llm_summarizer import LLMSummarizer
from synthorg.engine.compaction.memory_offload import MemoryOffloader
from synthorg.engine.compaction.models import (
    CompactionConfig,
    CompressionMetadata,
)
from synthorg.engine.compaction.protocol import CompactionCallback
from synthorg.engine.context import AgentContext
from synthorg.engine.sanitization import sanitize_message
from synthorg.engine.token_estimation import (
    DefaultTokenEstimator,
    PromptTokenEstimator,
)
from synthorg.observability import get_logger
from synthorg.observability.events.context_budget import (
    CONTEXT_BUDGET_COMPACTION_COMPLETED,
    CONTEXT_BUDGET_COMPACTION_FALLBACK,
    CONTEXT_BUDGET_COMPACTION_SKIPPED,
    CONTEXT_BUDGET_COMPACTION_STARTED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage

logger = get_logger(__name__)

type _ConversationSplit = tuple[
    tuple[ChatMessage, ...],
    tuple[ChatMessage, ...],
    tuple[ChatMessage, ...],
]

_MAX_SUMMARY_CHARS: Final[int] = 500
"""Defensive cap on the snippet-join summary length; prevents bloated
summaries when many long assistant messages are archived. Not exposed to
the settings registry (the LLM summariser has its own token cap)."""


def make_compaction_callback(
    *,
    config: CompactionConfig,
    estimator: PromptTokenEstimator | None = None,
    summarizer: LLMSummarizer | None = None,
    offloader: MemoryOffloader | None = None,
) -> CompactionCallback:
    """Create a compaction callback with the given configuration.

    The returned async callable checks whether the context fill level
    exceeds ``config.fill_threshold_percent`` and, if so, replaces
    the oldest conversation turns with a summary message.

    When a Phase-2 ``summarizer`` or ``offloader`` is wired (and enabled
    in ``config``), the archived batch is semantically summarised by an
    LLM and/or offloaded to memory before the in-context summary replaces
    it; both degrade to the Phase-1 text path on absence or failure.

    Args:
        config: Compaction configuration.
        estimator: Token estimator for summary size estimation;
            defaults to ``DefaultTokenEstimator``.
        summarizer: Optional LLM-backed summariser (Phase-2).
        offloader: Optional memory offloader for archived batches (Phase-2).

    Returns:
        An async compaction callback.
    """
    est = estimator or DefaultTokenEstimator()

    async def _compact(ctx: AgentContext) -> AgentContext | None:
        if summarizer is None and offloader is None:
            return _do_compaction(ctx, config, est)
        return await _do_compaction_phase2(
            ctx, config, est, summarizer=summarizer, offloader=offloader
        )

    return _compact


def _do_compaction(
    ctx: AgentContext,
    config: CompactionConfig,
    estimator: PromptTokenEstimator,
    *,
    force: bool = False,
) -> AgentContext | None:
    """Core (Phase-1, synchronous) compaction logic.

    Args:
        ctx: Current agent context.
        config: Compaction configuration.
        estimator: Token estimator.
        force: Skip fill threshold check (for agent-initiated compaction).

    Returns:
        New compacted ``AgentContext`` or ``None`` if no compaction needed.
    """
    prep = _prepare_compaction(ctx, config, force=force)
    if prep is None:
        return None
    head, archivable, recent = prep
    summary_text = _build_phase1_summary(ctx, archivable, config)
    return _finalise(ctx, head, archivable, recent, estimator, summary_text)


async def _do_compaction_phase2(  # noqa: PLR0913 -- ctx + config + estimator + two Phase-2 collaborators + force flag
    ctx: AgentContext,
    config: CompactionConfig,
    estimator: PromptTokenEstimator,
    *,
    summarizer: LLMSummarizer | None,
    offloader: MemoryOffloader | None,
    force: bool = False,
) -> AgentContext | None:
    """Phase-2 compaction: LLM summary and/or memory offload.

    Falls back to the Phase-1 text summary when the LLM summariser is
    absent / disabled / fails. The offload is best-effort and never
    blocks the compaction.

    Args:
        ctx: Current agent context.
        config: Compaction configuration.
        estimator: Token estimator.
        summarizer: Optional LLM-backed summariser.
        offloader: Optional memory offloader.
        force: Skip the fill-threshold check (agent-initiated compaction).

    Returns:
        New compacted ``AgentContext`` or ``None`` if no compaction needed.
    """
    prep = _prepare_compaction(ctx, config, force=force)
    if prep is None:
        return None
    head, archivable, recent = prep
    summary_text = _build_phase1_summary(ctx, archivable, config)
    if summarizer is not None and config.llm_summarizer_enabled:
        summary_text = await summarizer.summarize(
            archivable,
            execution_id=ctx.execution_id,
            fallback_text=summary_text,
        )
    if offloader is not None and config.memory_offload_enabled:
        await offloader.offload(
            agent_id=NotBlankStr(str(ctx.identity.id)),
            archivable=archivable,
            execution_id=ctx.execution_id,
        )
    return _finalise(ctx, head, archivable, recent, estimator, summary_text)


def _prepare_compaction(
    ctx: AgentContext,
    config: CompactionConfig,
    *,
    force: bool,
) -> _ConversationSplit | None:
    """Run the gating checks and split the conversation for compaction.

    Returns:
        ``(head, archivable, recent)`` when compaction should proceed, or
        ``None`` when the threshold / message-count / split checks bail.
    """
    fill_pct = ctx.context_fill_percent
    if not force:
        effective_threshold = (
            config.safety_threshold_percent
            if config.agent_controlled
            else config.fill_threshold_percent
        )
        if fill_pct is None or fill_pct < effective_threshold:
            return None

    conversation = ctx.conversation
    if len(conversation) < config.min_messages_to_compact:
        logger.debug(
            CONTEXT_BUDGET_COMPACTION_SKIPPED,
            execution_id=ctx.execution_id,
            reason="too_few_messages",
            message_count=len(conversation),
            min_required=config.min_messages_to_compact,
        )
        return None

    logger.info(
        CONTEXT_BUDGET_COMPACTION_STARTED,
        execution_id=ctx.execution_id,
        fill_percent=fill_pct,
        message_count=len(conversation),
        forced=force,
    )
    return _split_conversation(ctx, config)


def _build_phase1_summary(
    ctx: AgentContext,
    archivable: tuple[ChatMessage, ...],
    config: CompactionConfig,
) -> str:
    """Build the Phase-1 snippet-join summary text for the archived batch.

    Returns:
        The summary text (also the Phase-2 fallback).
    """
    task_complexity = _extract_task_complexity(ctx)
    return _build_summary(
        archivable,
        ctx.execution_id,
        preserve_markers=config.preserve_epistemic_markers,
        task_complexity=task_complexity,
    )


def _finalise(  # noqa: PLR0913 -- segments + estimator + summary threaded in
    ctx: AgentContext,
    head: tuple[ChatMessage, ...],
    archivable: tuple[ChatMessage, ...],
    recent: tuple[ChatMessage, ...],
    estimator: PromptTokenEstimator,
    summary_text: str,
) -> AgentContext:
    """Compress with the resolved summary text and return the new context.

    Returns:
        The compacted ``AgentContext``.
    """
    compressed, metadata, summary_tokens = _compress(
        ctx, head, archivable, recent, estimator, summary_text=summary_text
    )

    # Re-estimate fill with compressed conversation.  Counts
    # conversation tokens only -- system prompt and tool overhead
    # are excluded.  The loop's next ``update_context_fill``
    # call restores the full estimate.
    new_fill = estimator.estimate_conversation_tokens(compressed)

    logger.info(
        CONTEXT_BUDGET_COMPACTION_COMPLETED,
        execution_id=ctx.execution_id,
        original_messages=len(ctx.conversation),
        compacted_messages=len(compressed),
        archived_turns=metadata.archived_turns,
        summary_tokens=summary_tokens,
        compactions_total=metadata.compactions_performed,
    )
    return ctx.with_compression(metadata, compressed, new_fill)


def _split_conversation(
    ctx: AgentContext,
    config: CompactionConfig,
) -> _ConversationSplit | None:
    """Split conversation into head, archivable, and recent segments.

    Returns:
        ``(head, archivable, recent)`` segments for compaction;
        ``None`` when nothing can be archived (preserved-window
        already covers every non-system message).
    """
    conversation = ctx.conversation
    preserve_count = config.preserve_recent_turns * 2
    # Preserve all leading SYSTEM messages (original system prompt
    # and any prior compaction summaries).
    start_idx = 0
    while (
        start_idx < len(conversation)
        and conversation[start_idx].role == MessageRole.SYSTEM
    ):
        start_idx += 1
    head = tuple(conversation[:start_idx])

    if preserve_count >= len(conversation) - start_idx:
        logger.debug(
            CONTEXT_BUDGET_COMPACTION_SKIPPED,
            execution_id=ctx.execution_id,
            reason="nothing_to_archive",
            preserve_count=preserve_count,
            message_count=len(conversation),
        )
        return None

    archivable = conversation[start_idx:-preserve_count]
    recent = conversation[-preserve_count:]
    return head, archivable, recent


def _compress(  # noqa: PLR0913
    ctx: AgentContext,
    head: tuple[ChatMessage, ...],
    archivable: tuple[ChatMessage, ...],
    recent: tuple[ChatMessage, ...],
    estimator: PromptTokenEstimator,
    *,
    summary_text: str,
) -> tuple[tuple[ChatMessage, ...], CompressionMetadata, int]:
    """Build compressed conversation and metadata from a resolved summary.

    Args:
        ctx: Current agent context.
        head: Preserved leading system messages.
        archivable: The messages being archived (for turn counting).
        recent: Preserved recent messages.
        estimator: Token estimator.
        summary_text: The resolved summary text (Phase-1 or Phase-2).

    Returns:
        ``(compressed_conversation, metadata, summary_tokens)`` --
        the rewritten conversation with the summary system message,
        the cumulative :class:`CompressionMetadata`, and the
        estimated token count of the summary.
    """
    summary_msg = ChatMessage(
        role=MessageRole.SYSTEM,
        content=summary_text,
    )
    summary_tokens = estimator.estimate_tokens(summary_text)
    compressed = (*head, summary_msg, *recent)

    prior = ctx.compression_metadata
    compactions_count = prior.compactions_performed + 1 if prior is not None else 1
    prior_archived = prior.archived_turns if prior is not None else 0

    archived_turn_count = sum(1 for m in archivable if m.role == MessageRole.ASSISTANT)
    metadata = CompressionMetadata(
        compression_point=ctx.turn_count,
        archived_turns=prior_archived + archived_turn_count,
        summary_tokens=summary_tokens,
        compactions_performed=compactions_count,
    )
    return compressed, metadata, summary_tokens


def _extract_task_complexity(ctx: AgentContext) -> Complexity:
    """Extract task complexity from context, defaulting to COMPLEX.

    Returns:
        The :class:`Complexity` declared on the bound task; falls
        back to :attr:`Complexity.COMPLEX` when no task is wired.
    """
    task_exec = getattr(ctx, "task_execution", None)
    if task_exec is not None:
        task = getattr(task_exec, "task", None)
        if task is not None:
            complexity = getattr(task, "estimated_complexity", None)
            if isinstance(complexity, Complexity):
                return complexity
    return Complexity.COMPLEX


def _build_summary(
    messages: tuple[ChatMessage, ...],
    execution_id: str,
    *,
    preserve_markers: bool,
    task_complexity: Complexity,
) -> str:
    """Build a text summary from archived messages.

    When ``preserve_markers`` is True, assistant messages with
    epistemic markers (hedging, reconsideration, etc.) are preserved
    as marker-containing sentences instead of being sanitized down
    to 100-char snippets.

    Args:
        messages: The archived messages to summarize.
        execution_id: Execution identifier for log correlation.
        preserve_markers: Whether to preserve epistemic markers.
        task_complexity: Task complexity for marker thresholds.

    Returns:
        Summary text describing the archived conversation.
    """
    snippets: list[str] = []
    preserved_count = 0

    for msg in messages:
        if msg.role != MessageRole.ASSISTANT or not msg.content:
            continue
        cleaned = msg.content.replace("\n", " ").strip()
        if not cleaned:
            continue

        # Check for epistemic markers worth preserving.
        if preserve_markers and should_preserve_message(
            cleaned,
            task_complexity,
        ):
            marker_text = extract_marker_sentences(cleaned)
            if marker_text:
                sanitized_markers = sanitize_message(
                    marker_text,
                    max_length=max(len(marker_text), 1),
                )
                snippets.append(sanitized_markers)
                preserved_count += 1
                continue

        # Standard sanitized snippet.
        snippet = sanitize_message(cleaned, max_length=100)
        snippets.append(snippet)

    # Drop useless "details redacted" placeholders.
    useful = [s for s in snippets if s != "details redacted"]
    if not useful:
        logger.debug(
            CONTEXT_BUDGET_COMPACTION_FALLBACK,
            execution_id=execution_id,
            reason="no_useful_assistant_content_for_summary",
            archived_count=len(messages),
        )
        return f"[Archived {len(messages)} messages from earlier in the conversation.]"

    joined = "; ".join(useful)
    if len(joined) > _MAX_SUMMARY_CHARS:
        joined = joined[:_MAX_SUMMARY_CHARS] + "..."

    if preserved_count > 0:
        msg_word = "message" if preserved_count == 1 else "messages"
        return (
            f"[Archived {len(messages)} messages. "
            f"Epistemic markers preserved from "
            f"{preserved_count} {msg_word}. "
            f"Summary: {joined}]"
        )
    return f"[Archived {len(messages)} messages. Summary of prior work: {joined}]"


async def force_compaction(
    ctx: AgentContext,
    config: CompactionConfig,
    estimator: PromptTokenEstimator,
    *,
    summarizer: LLMSummarizer | None = None,
    offloader: MemoryOffloader | None = None,
) -> AgentContext | None:
    """Compact context without checking the fill threshold.

    Used when an agent explicitly requests compaction via the
    ``compact_context`` tool. Skips the fill-threshold comparison while
    preserving all other checks (minimum message count, recent-turn
    preservation). When a Phase-2 ``summarizer`` / ``offloader`` is
    supplied (and enabled in ``config``) the forced compaction runs the
    same semantic summary / memory-offload path as the threshold-triggered
    callback, rather than silently downgrading to the Phase-1 text summary.

    Args:
        ctx: Current agent context.
        config: Compaction configuration.
        estimator: Token estimator.
        summarizer: Optional LLM-backed summariser (Phase-2).
        offloader: Optional memory offloader for archived batches (Phase-2).

    Returns:
        Compacted context, or ``None`` if too few messages.
    """
    if summarizer is None and offloader is None:
        return _do_compaction(ctx, config, estimator, force=True)
    return await _do_compaction_phase2(
        ctx,
        config,
        estimator,
        summarizer=summarizer,
        offloader=offloader,
        force=True,
    )
