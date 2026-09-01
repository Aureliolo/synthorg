"""Oldest-turns summarization compaction callback factory.

Creates a ``CompactionCallback`` that archives the oldest conversation
turns into a summary message when the context fill level exceeds a
configurable threshold.
"""

from synthorg.core.types import NotBlankStr
from synthorg.engine.compaction._conversation import (
    ConversationSplit,
    build_text_summary,
    finalise,
    resolve_preserve_markers,
    split_conversation,
)
from synthorg.engine.compaction.llm_summarizer import LLMSummarizer
from synthorg.engine.compaction.memory_offload import MemoryOffloader
from synthorg.engine.compaction.models import CompactionConfig
from synthorg.engine.compaction.protocol import CompactionCallback
from synthorg.engine.context import AgentContext
from synthorg.engine.token_estimation import (
    DefaultTokenEstimator,
    PromptTokenEstimator,
)
from synthorg.observability import get_logger
from synthorg.observability.events.context_budget import (
    CONTEXT_BUDGET_COMPACTION_SKIPPED,
    CONTEXT_BUDGET_COMPACTION_STARTED,
)
from synthorg.providers.models import ZERO_TOKEN_USAGE

logger = get_logger(__name__)


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

    When a semantic ``summarizer`` or ``offloader`` is wired (and enabled
    in ``config``), the archived batch is semantically summarised by an
    LLM and/or offloaded to memory before the in-context summary replaces
    it; both degrade to the text path on absence or failure.

    Args:
        config: Compaction configuration.
        estimator: Token estimator for summary size estimation;
            defaults to ``DefaultTokenEstimator``.
        summarizer: Optional LLM-backed semantic summariser.
        offloader: Optional memory offloader for archived batches.

    Returns:
        An async compaction callback.
    """
    est = estimator or DefaultTokenEstimator()

    async def _compact(
        ctx: AgentContext,
        *,
        force: bool = False,
        preserve_markers: bool | None = None,
    ) -> AgentContext | None:
        if force:
            return await force_compaction(
                ctx,
                config,
                est,
                summarizer=summarizer,
                offloader=offloader,
                preserve_markers_override=preserve_markers,
            )
        if summarizer is None and offloader is None:
            return _do_compaction(ctx, config, est)
        return await _do_semantic_compaction(
            ctx, config, est, summarizer=summarizer, offloader=offloader
        )

    return _compact


def _do_compaction(
    ctx: AgentContext,
    config: CompactionConfig,
    estimator: PromptTokenEstimator,
    *,
    force: bool = False,
    preserve_markers_override: bool | None = None,
) -> AgentContext | None:
    """Core (text, synchronous) compaction logic.

    Args:
        ctx: Current agent context.
        config: Compaction configuration.
        estimator: Token estimator.
        force: Skip fill threshold check (for agent-initiated compaction).
        preserve_markers_override: Per-call override for
            ``config.preserve_epistemic_markers``; ``None`` uses the
            configured default.

    Returns:
        New compacted ``AgentContext`` or ``None`` if no compaction needed.
    """
    split = _prepare_compaction(ctx, config, force=force)
    if split is None:
        return None
    return finalise(
        ctx,
        split,
        estimator=estimator,
        summary_text=build_text_summary(
            ctx,
            split.archivable,
            config,
            preserve_markers_override=preserve_markers_override,
        ),
    )


async def _do_semantic_compaction(
    ctx: AgentContext,
    config: CompactionConfig,
    estimator: PromptTokenEstimator,
    *,
    summarizer: LLMSummarizer | None,
    offloader: MemoryOffloader | None,
    force: bool = False,
    preserve_markers_override: bool | None = None,
) -> AgentContext | None:
    """Semantic compaction: LLM summary and/or memory offload.

    Falls back to the text summary when the LLM summariser is
    absent / disabled / fails. The offload is best-effort and never
    blocks the compaction.

    Args:
        ctx: Current agent context.
        config: Compaction configuration.
        estimator: Token estimator.
        summarizer: Optional LLM-backed summariser.
        offloader: Optional memory offloader.
        force: Skip the fill-threshold check (agent-initiated compaction).
        preserve_markers_override: Per-call override for
            ``config.preserve_epistemic_markers``; ``None`` uses the
            configured default.

    Returns:
        New compacted ``AgentContext`` or ``None`` if no compaction needed.
    """
    split = _prepare_compaction(ctx, config, force=force)
    if split is None:
        return None
    archivable = split.archivable
    summary_text = build_text_summary(
        ctx, archivable, config, preserve_markers_override=preserve_markers_override
    )
    summary_usage = ZERO_TOKEN_USAGE
    if summarizer is not None and config.llm_summarizer_enabled:
        outcome = await summarizer.summarize(
            archivable,
            fallback_text=summary_text,
            preserve_markers=resolve_preserve_markers(
                config, override=preserve_markers_override
            ),
        )
        summary_text = outcome.text
        summary_usage = outcome.usage
    if offloader is not None and config.memory_offload_enabled:
        await offloader.offload(
            agent_id=NotBlankStr(str(ctx.identity.id)),
            archivable=archivable,
        )
    return finalise(
        ctx,
        split,
        estimator=estimator,
        summary_text=summary_text,
        summary_usage=summary_usage,
    )


def _prepare_compaction(
    ctx: AgentContext,
    config: CompactionConfig,
    *,
    force: bool,
) -> ConversationSplit | None:
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
    return split_conversation(ctx, config)


async def force_compaction(
    ctx: AgentContext,
    config: CompactionConfig,
    estimator: PromptTokenEstimator,
    *,
    summarizer: LLMSummarizer | None = None,
    offloader: MemoryOffloader | None = None,
    preserve_markers_override: bool | None = None,
) -> AgentContext | None:
    """Compact context without checking the fill threshold.

    Used when an agent explicitly requests compaction via the
    ``compact_context`` tool. Skips the fill-threshold comparison while
    preserving all other checks (minimum message count, recent-turn
    preservation). When a semantic ``summarizer`` / ``offloader`` is
    supplied (and enabled in ``config``) the forced compaction runs the
    same semantic summary / memory-offload path as the threshold-triggered
    callback, rather than silently downgrading to the text summary.

    Args:
        ctx: Current agent context.
        config: Compaction configuration.
        estimator: Token estimator.
        summarizer: Optional LLM-backed semantic summariser.
        offloader: Optional memory offloader for archived batches.
        preserve_markers_override: Per-call override for
            ``config.preserve_epistemic_markers``, from the requesting
            tool call; ``None`` uses the configured default.

    Returns:
        Compacted context, or ``None`` if too few messages.
    """
    if summarizer is None and offloader is None:
        return _do_compaction(
            ctx,
            config,
            estimator,
            force=True,
            preserve_markers_override=preserve_markers_override,
        )
    return await _do_semantic_compaction(
        ctx,
        config,
        estimator,
        summarizer=summarizer,
        offloader=offloader,
        force=True,
        preserve_markers_override=preserve_markers_override,
    )
