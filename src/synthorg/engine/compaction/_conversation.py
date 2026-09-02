"""Which messages a compaction drops, and what the conversation becomes.

The surgery half of compaction, split from the callback that orchestrates it
(``summarizer.py``). The seam is a real one: everything here is a pure
question about a conversation (what may be archived, what the summary says,
what the rewritten message list looks like), while the callback owns when to
run, which summariser to ask, and whether to offload.
"""

from dataclasses import dataclass
from typing import Final

from synthorg.core.execution_identity import current_execution_identity
from synthorg.core.task_enums import Complexity
from synthorg.core.text_clipping import clip_with_ellipsis
from synthorg.engine.compaction.epistemic import (
    extract_marker_sentences,
    should_preserve_message,
)
from synthorg.engine.compaction.models import (
    CompactionConfig,
    CompressionMetadata,
)
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_correction_budget import correction_tail_messages
from synthorg.engine.prompt_safety import TAG_COMPACTION_SUMMARY, wrap_untrusted
from synthorg.engine.sanitization import sanitize_message
from synthorg.engine.token_estimation import PromptTokenEstimator
from synthorg.observability import get_logger
from synthorg.observability.events.context_budget import (
    CONTEXT_BUDGET_COMPACTION_COMPLETED,
    CONTEXT_BUDGET_COMPACTION_FALLBACK,
    CONTEXT_BUDGET_COMPACTION_SKIPPED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ZERO_TOKEN_USAGE, ChatMessage, TokenUsage

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationSplit:
    """One compaction's view of a conversation, with the pins located.

    A pinned message survives compaction wherever it sits, so the split
    carries WHERE each pin is rather than only which messages move: the
    compacted conversation is rebuilt in a different order, and a pin set
    that is not re-mapped alongside it names whatever message happens to
    land at the old index.

    Attributes:
        head: Leading SYSTEM messages, preserved verbatim. A prefix of the
            original conversation, so its indices are unchanged.
        archivable: What the summary replaces, pins already removed.
        recent: The trailing window kept verbatim.
        pinned_head: Indices within ``head`` that are pinned.
        rescued: Pinned messages that would otherwise have been archived,
            in their original order. They are re-seated between the head
            and the summary, so what the agent was told to do outlives the
            turns in which it was told.
        pinned_recent: Offsets within ``recent`` that are pinned.
    """

    head: tuple[ChatMessage, ...]
    archivable: tuple[ChatMessage, ...]
    recent: tuple[ChatMessage, ...]
    pinned_head: frozenset[int]
    rescued: tuple[ChatMessage, ...]
    pinned_recent: frozenset[int]


_MAX_SUMMARY_CHARS: Final[int] = 500
"""Defensive cap on the snippet-join summary length; prevents bloated
summaries when many long assistant messages are archived. Not exposed to
the settings registry (the LLM summariser has its own token cap)."""


def resolve_preserve_markers(
    config: CompactionConfig, *, override: bool | None
) -> bool:
    """Resolve one compaction's epistemic-marker setting.

    Both summary paths (text-only and LLM) answer the same request, so they
    read it through one resolution rather than each restating the fallback.

    Args:
        config: Compaction configuration, carrying the configured default.
        override: The requesting caller's per-call choice, or ``None``.

    Returns:
        The override when supplied, else ``config.preserve_epistemic_markers``.
    """
    if override is None:
        return config.preserve_epistemic_markers
    return override


def build_text_summary(
    ctx: AgentContext,
    archivable: tuple[ChatMessage, ...],
    config: CompactionConfig,
    *,
    preserve_markers_override: bool | None = None,
) -> str:
    """Build the snippet-join text summary for the archived batch.

    Args:
        ctx: Current agent context.
        archivable: The messages being archived.
        config: Compaction configuration.
        preserve_markers_override: Per-call override for
            ``config.preserve_epistemic_markers``; ``None`` uses the
            configured default.

    Returns:
        The summary text (also the semantic-path fallback).
    """
    task_complexity = extract_task_complexity(ctx)
    return build_summary(
        archivable,
        preserve_markers=resolve_preserve_markers(
            config, override=preserve_markers_override
        ),
        task_complexity=task_complexity,
    )


def finalise(
    ctx: AgentContext,
    split: ConversationSplit,
    *,
    estimator: PromptTokenEstimator,
    summary_text: str,
    summary_usage: TokenUsage = ZERO_TOKEN_USAGE,
) -> AgentContext:
    """Compress with the resolved summary text and return the new context.

    Args:
        ctx: Current agent context.
        split: What this compaction archives, keeps and re-seats.
        estimator: Token estimator.
        summary_text: The resolved summary text.
        summary_usage: What producing it cost. Zero for the text path,
            which is what costing nothing looks like.

    Returns:
        The compacted ``AgentContext``.
    """
    compressed, metadata, summary_tokens, pins = _compress(
        ctx,
        split,
        estimator,
        summary_text=summary_text,
        summary_usage=summary_usage,
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
    return ctx.with_compression(metadata, compressed, new_fill, pinned=pins)


def split_conversation(
    ctx: AgentContext,
    config: CompactionConfig,
) -> ConversationSplit | None:
    """Split the conversation into head, archivable and recent segments.

    Pinned messages are removed from the archivable span and returned as
    ``rescued``: a pin is a claim that this message has to be in front of
    the model on every turn, which is a claim compaction cannot honour by
    summarising it. The task brief is the one the loop makes.

    Returns:
        The split, or ``None`` when nothing can be archived (the preserved
        window already covers every non-system message, or every message
        outside it is pinned).
    """
    conversation = ctx.conversation
    pinned = ctx.pinned_message_indices
    # The correction budget is derived from the trailing run of nudges, so
    # archiving any of them tells the next turn the run has earned fewer
    # corrections than it has. Widen the window rather than trimming the
    # stretch: it is bounded by MAX_CONSECUTIVE_CORRECTIONS and a productive
    # turn ends it, so the cost is a handful of messages at worst.
    preserve_count = max(
        config.preserve_recent_turns * 2,
        correction_tail_messages(conversation),
    )
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

    archivable_start = start_idx
    recent_start = len(conversation) - preserve_count
    span = range(archivable_start, recent_start)
    archivable = tuple(conversation[i] for i in span if i not in pinned)
    rescued = tuple(conversation[i] for i in span if i in pinned)
    if not archivable:
        # Every candidate is pinned. A compaction here would archive nothing
        # and still splice in a summary, so the conversation GROWS by one
        # message per pass and the fill it was meant to relieve never drops.
        logger.debug(
            CONTEXT_BUDGET_COMPACTION_SKIPPED,
            execution_id=ctx.execution_id,
            reason="everything_pinned",
            preserve_count=preserve_count,
            message_count=len(conversation),
        )
        return None
    return ConversationSplit(
        head=head,
        archivable=archivable,
        recent=conversation[recent_start:],
        pinned_head=frozenset(i for i in pinned if i < start_idx),
        rescued=rescued,
        pinned_recent=frozenset(i - recent_start for i in pinned if i >= recent_start),
    )


def _compress(
    ctx: AgentContext,
    split: ConversationSplit,
    estimator: PromptTokenEstimator,
    *,
    summary_text: str,
    summary_usage: TokenUsage,
) -> tuple[tuple[ChatMessage, ...], CompressionMetadata, int, frozenset[int]]:
    """Build compressed conversation and metadata from a resolved summary.

    Args:
        ctx: Current agent context.
        split: What this compaction archives, keeps and re-seats.
        estimator: Token estimator.
        summary_text: The resolved summary text (text or semantic).
        summary_usage: What producing it cost, added to whatever earlier
            compactions on this context already spent.

    Returns:
        ``(compressed_conversation, metadata, summary_tokens, pinned)`` --
        the rewritten conversation with the summary system message, the
        cumulative :class:`CompressionMetadata`, the estimated token count
        of the summary, and where the pins have moved to.
    """
    head, archivable, recent = split.head, split.archivable, split.recent
    # Fenced, because the summary is spliced in at SYSTEM rank and was made
    # from the turns it replaces: tool output, task content and the model's
    # own replies, any of which an injection may have steered into
    # instruction-shaped text the summariser then repeats.
    summary_msg = ChatMessage(
        role=MessageRole.SYSTEM,
        content=wrap_untrusted(TAG_COMPACTION_SUMMARY, summary_text),
    )
    summary_tokens = estimator.estimate_tokens(summary_text)
    compressed = (*head, *split.rescued, summary_msg, *recent)
    # Re-mapped rather than carried: the compacted list is a different
    # list, so an index that is not moved with it names whatever message
    # happens to land there.
    recent_base = len(head) + len(split.rescued) + 1
    pins = frozenset(
        {
            *split.pinned_head,
            *range(len(head), len(head) + len(split.rescued)),
            *(recent_base + offset for offset in split.pinned_recent),
        }
    )

    prior = ctx.compression_metadata
    compactions_count = prior.compactions_performed + 1 if prior is not None else 1
    prior_archived = prior.archived_turns if prior is not None else 0

    archived_turn_count = sum(1 for m in archivable if m.role == MessageRole.ASSISTANT)
    metadata = CompressionMetadata(
        compression_point=ctx.turn_count,
        archived_turns=prior_archived + archived_turn_count,
        summary_tokens=summary_tokens,
        compactions_performed=compactions_count,
        summary_cost=(prior.summary_cost if prior is not None else 0.0)
        + summary_usage.cost,
        summary_input_tokens=(prior.summary_input_tokens if prior is not None else 0)
        + summary_usage.input_tokens,
        summary_output_tokens=(prior.summary_output_tokens if prior is not None else 0)
        + summary_usage.output_tokens,
    )
    return compressed, metadata, summary_tokens, pins


def extract_task_complexity(ctx: AgentContext) -> Complexity:
    """Extract task complexity from context, defaulting to COMPLEX.

    Returns:
        The :class:`Complexity` declared on the bound task; falls
        back to :attr:`Complexity.COMPLEX` when no task is wired.
    """
    if ctx.task_execution is None:
        return Complexity.COMPLEX
    return ctx.task_execution.task.estimated_complexity


def build_summary(
    messages: tuple[ChatMessage, ...],
    *,
    preserve_markers: bool,
    task_complexity: Complexity,
) -> str:
    """Build a text summary from archived messages.

    When ``preserve_markers`` is True, assistant messages with
    epistemic markers (hedging, reconsideration, etc.) are preserved
    as marker-containing sentences instead of being sanitized down
    to 100-char snippets. The run id for the fallback log is read from
    the ambient ``current_execution_identity()``.

    Args:
        messages: The archived messages to summarize.
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
        identity = current_execution_identity()
        logger.debug(
            CONTEXT_BUDGET_COMPACTION_FALLBACK,
            execution_id=identity.execution_id if identity is not None else None,
            reason="no_useful_assistant_content_for_summary",
            archived_count=len(messages),
        )
        return f"[Archived {len(messages)} messages from earlier in the conversation.]"

    joined = clip_with_ellipsis("; ".join(useful), _MAX_SUMMARY_CHARS)

    if preserved_count > 0:
        msg_word = "message" if preserved_count == 1 else "messages"
        return (
            f"[Archived {len(messages)} messages. "
            f"Epistemic markers preserved from "
            f"{preserved_count} {msg_word}. "
            f"Summary: {joined}]"
        )
    return f"[Archived {len(messages)} messages. Summary of prior work: {joined}]"


__all__ = [
    "ConversationSplit",
    "build_summary",
    "build_text_summary",
    "extract_task_complexity",
    "finalise",
    "resolve_preserve_markers",
    "split_conversation",
]
