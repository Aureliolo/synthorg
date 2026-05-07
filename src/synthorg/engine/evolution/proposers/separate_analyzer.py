"""LLM-based proposer for adaptation proposals.

Uses a SEPARATE completion provider call to analyze evolution context
and generate adaptation proposals. Follows the EvoSkill principle:
the agent being evolved does NOT propose its own changes.
"""

from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

from synthorg.budget.call_category import LLMCallCategory

# ``CostTracker`` is part of ``SeparateAnalyzerProposer.__init__``'s
# public annotation, so it must resolve at runtime when downstream
# tooling evaluates type hints (DI containers, doc generators).
from synthorg.budget.tracker import CostTracker  # noqa: TC001
from synthorg.core.json_parsing import extract_json_from_llm_response
from synthorg.core.types import NotBlankStr
from synthorg.engine.evolution.models import (
    AdaptationProposal,
)
from synthorg.engine.prompt_safety import (
    TAG_TASK_FACT,
    TAG_UNTRUSTED_ARTIFACT,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.evolution import (
    EVOLUTION_PROPOSER_ANALYZE,
    EVOLUTION_PROPOSER_INIT,
    EVOLUTION_PROPOSER_PARSE_ERROR,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.errors import ProviderError
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.engine.evolution.protocols import EvolutionContext

logger = get_logger(__name__)

_UNTRUSTED_TAGS: Final[tuple[str, ...]] = (TAG_TASK_FACT, TAG_UNTRUSTED_ARTIFACT)

_DEFAULT_SUMMARY_CAP: Final[int] = 5
"""How many recent tasks and memories to summarise in the prompt."""

_MEMORY_CONTENT_MAX_CHARS: Final[int] = 500
"""Per-memory content truncation so long memories cannot blow the prompt."""

_SYSTEM_PROMPT = (
    "You are an agent evolution analyst. Given an agent's current "
    "identity, performance data, and recent task results, propose "
    "zero or more structured adaptations to improve its behavior.\n\n"
    "Respond with a JSON object containing exactly this field:\n"
    '- "proposals": A list of proposed adaptations.\n\n'
    "Each proposal must have:\n"
    '- "axis": "identity", "strategy_selection", or "prompt_template"\n'
    '- "description": A one-sentence description of the change\n'
    '- "changes": A dict with axis-specific changes\n'
    '- "confidence": Your confidence (0.0-1.0)\n'
    '- "source": "failure", "success", "inflection", or "scheduled"\n\n'
    "Return an empty proposals list if no adaptations are warranted.\n"
    "Respond ONLY with the JSON object, no markdown or explanation.\n\n"
    + untrusted_content_directive(_UNTRUSTED_TAGS)
)


def _extract_json(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from LLM response text via the shared helper."""

    def _log_parse_failure(detail: str) -> None:
        logger.debug(
            EVOLUTION_PROPOSER_PARSE_ERROR,
            reason="json_decode_error",
            detail=detail,
        )

    return extract_json_from_llm_response(text, logger_callback=_log_parse_failure)


def _validate_proposals_list(
    data: dict[str, Any],
    agent_id: NotBlankStr,
) -> list[Any] | None:
    """Validate that data has a "proposals" key with a list value.

    Args:
        data: Parsed JSON dict.
        agent_id: Agent identifier for logging.

    Returns:
        The proposals list or None if validation fails.
    """
    proposals_data = data.get("proposals")
    if proposals_data is None:
        logger.warning(
            EVOLUTION_PROPOSER_PARSE_ERROR,
            agent_id=str(agent_id),
            reason="missing_proposals_key",
        )
        return None

    if not isinstance(proposals_data, list):
        logger.warning(
            EVOLUTION_PROPOSER_PARSE_ERROR,
            agent_id=str(agent_id),
            reason="proposals_not_list",
        )
        return None

    return proposals_data


def _summarise_tasks(
    tasks: tuple[Any, ...],
    *,
    cap: int,
) -> str:
    """Render up to *cap* recent tasks as compact summary lines.

    The tuple is capped from the tail so the most recent entries win
    when the fleet exceeds *cap*.
    """
    if not tasks or cap <= 0:
        return "  (none)"
    recent = tasks[-cap:]
    lines: list[str] = []
    for record in recent:
        quality = (
            f"{record.quality_score:.2f}" if record.quality_score is not None else "n/a"
        )
        outcome = "success" if record.is_success else "failure"
        lines.append(
            f"  - task_id={record.task_id} type={record.task_type.value} "
            f"outcome={outcome} quality={quality} "
            f"duration={record.duration_seconds:.0f}s "
            f"turns={record.turns_used} tokens={record.tokens_used}"
        )
    return "\n".join(lines)


def _summarise_memories(
    memories: tuple[Any, ...],
    *,
    cap: int,
    content_max_chars: int,
) -> str:
    """Render up to *cap* procedural memories with truncated content.

    Content strings are clamped at code-point boundaries to
    ``content_max_chars`` characters so a single oversized memory
    cannot balloon the prompt.
    """
    if not memories or cap <= 0:
        return "  (none)"
    recent = memories[-cap:]
    lines: list[str] = []
    for entry in recent:
        raw = entry.content
        clipped = (
            raw if len(raw) <= content_max_chars else raw[:content_max_chars] + "..."
        )
        lines.append(
            f"  - memory_id={entry.id} category={entry.category.value} "
            f"content={clipped!r}"
        )
    return "\n".join(lines)


def _build_user_message(
    agent_id: NotBlankStr,
    context: EvolutionContext,
    *,
    summary_cap: int = _DEFAULT_SUMMARY_CAP,
    memory_content_max_chars: int = _MEMORY_CONTENT_MAX_CHARS,
) -> str:
    """Format the context into a user message for the proposer LLM.

    Per-item summaries of recent tasks (id, type, outcome, quality,
    duration, turns, tokens) and procedural memories (id, category,
    truncated content) are included so the model has real signal
    instead of just counts.  The entire block is wrapped in a
    :data:`TAG_TASK_FACT` fence and the system prompt carries the
    matching :func:`untrusted_content_directive` so the model treats
    everything inside as untrusted data.

    Args:
        agent_id: Target agent identifier.
        context: Evolution context with identity, performance, and memory data.
        summary_cap: Per-list cap for the per-item summaries.
        memory_content_max_chars: Max characters of memory content to inline.

    Returns:
        Formatted user message string.
    """
    identity_str = (
        f"Name: {context.identity.name}, Level: "
        f"{context.identity.level}, Role: {context.identity.role}"
    )

    perf_str = "No performance data"
    if context.performance_snapshot:
        perf_str = (
            f"Quality: {context.performance_snapshot.overall_quality_score}, "
            f"Collaboration: "
            f"{context.performance_snapshot.overall_collaboration_score}"
        )

    tasks_block = _summarise_tasks(
        context.recent_task_results,
        cap=summary_cap,
    )
    memories_block = _summarise_memories(
        context.recent_procedural_memories,
        cap=summary_cap,
        content_max_chars=memory_content_max_chars,
    )

    body = (
        f"Agent ID: {agent_id}\n"
        f"Identity: {identity_str}\n"
        f"Performance: {perf_str}\n"
        f"Recent Tasks ({len(context.recent_task_results)} total, "
        f"showing last {min(summary_cap, len(context.recent_task_results))}):\n"
        f"{tasks_block}\n"
        f"Procedural Memories ({len(context.recent_procedural_memories)} total, "
        f"showing last {min(summary_cap, len(context.recent_procedural_memories))}):\n"
        f"{memories_block}"
    )
    return wrap_untrusted(TAG_TASK_FACT, body)


class SeparateAnalyzerProposer:
    """Generates adaptation proposals via dedicated LLM analysis.

    Uses a separate completion provider call to analyze evolution context
    and produce adaptation proposals. Follows the EvoSkill principle:
    the target agent does NOT propose its own changes.

    Args:
        provider: Completion provider for the proposer LLM call.
        model: Model identifier to use for analysis.
        temperature: Sampling temperature (default: 0.3).
        max_tokens: Maximum tokens to generate (default: 2000).
    """

    def __init__(  # noqa: PLR0913
        self,
        provider: CompletionProvider,
        *,
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 2000,
        summary_cap: int = _DEFAULT_SUMMARY_CAP,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        if summary_cap < 0:
            msg = f"summary_cap must be non-negative; got {summary_cap}"
            logger.warning(
                EVOLUTION_PROPOSER_INIT,
                proposer="separate_analyzer",
                model=model,
                summary_cap=summary_cap,
                error=msg,
            )
            raise ValueError(msg)
        self._provider = provider
        self._model = model
        self._summary_cap = summary_cap
        self._cost_tracker = cost_tracker
        self._completion_config = CompletionConfig(
            temperature=temperature,
            max_tokens=max_tokens,
        )
        logger.debug(
            EVOLUTION_PROPOSER_INIT,
            proposer="separate_analyzer",
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            summary_cap=summary_cap,
        )

    @property
    def name(self) -> str:
        """Human-readable proposer name."""
        return "separate_analyzer"

    async def propose(
        self,
        *,
        agent_id: NotBlankStr,
        context: EvolutionContext,
    ) -> tuple[AdaptationProposal, ...]:
        """Generate zero or more adaptation proposals.

        Args:
            agent_id: Agent to generate proposals for.
            context: Evolution context with identity, performance,
                and memory data.

        Returns:
            Tuple of proposals (empty if no adaptations suggested).
        """
        try:
            messages = [
                ChatMessage(role=MessageRole.SYSTEM, content=_SYSTEM_PROMPT),
                ChatMessage(
                    role=MessageRole.USER,
                    content=_build_user_message(
                        agent_id,
                        context,
                        summary_cap=self._summary_cap,
                    ),
                ),
            ]
            async with cost_recording_scope(
                cost_tracker=self._cost_tracker,
                agent_id=agent_id,
                task_id=NotBlankStr("system:evolution:propose"),
                call_category=LLMCallCategory.SYSTEM,
            ):
                response = await self._provider.complete(
                    messages,
                    self._model,
                    config=self._completion_config,
                )
        except MemoryError, RecursionError:
            raise
        except ProviderError as exc:
            if not exc.is_retryable:
                # Non-retryable provider failures must surface to
                # operators with context before propagating; otherwise
                # a downstream catch-and-translate would hide the
                # error path entirely.
                logger.error(
                    EVOLUTION_PROPOSER_PARSE_ERROR,
                    agent_id=str(agent_id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    reason="provider_error_non_retryable",
                    is_retryable=False,
                )
                raise
            # Drop exc_info + scrub the message -- provider
            # HTTPStatusError can carry the API key.
            logger.warning(
                EVOLUTION_PROPOSER_PARSE_ERROR,
                agent_id=str(agent_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="provider_error_retryable",
                is_retryable=True,
            )
            return ()
        except Exception as exc:
            logger.warning(
                EVOLUTION_PROPOSER_PARSE_ERROR,
                agent_id=str(agent_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="provider_error",
                is_retryable=False,
            )
            return ()

        return self._parse_response(response.content, agent_id)

    def _parse_response(
        self,
        content: str | None,
        agent_id: NotBlankStr,
    ) -> tuple[AdaptationProposal, ...]:
        """Parse and validate the LLM response into proposals.

        Args:
            content: Response content from LLM.
            agent_id: Agent identifier for proposal context.

        Returns:
            Tuple of validated proposals (empty on parse/validation failure).
        """
        if not content or not content.strip():
            logger.debug(
                EVOLUTION_PROPOSER_PARSE_ERROR,
                agent_id=str(agent_id),
                reason="empty_response",
            )
            return ()

        data = _extract_json(content)
        if data is None:
            logger.warning(
                EVOLUTION_PROPOSER_PARSE_ERROR,
                agent_id=str(agent_id),
                reason="malformed_json",
            )
            return ()

        proposals_data = _validate_proposals_list(data, agent_id)
        if proposals_data is None:
            return ()

        return self._validate_and_build_proposals(proposals_data, agent_id)

    def _validate_and_build_proposals(
        self,
        proposals_data: list[Any],
        agent_id: NotBlankStr,
    ) -> tuple[AdaptationProposal, ...]:
        """Validate and build AdaptationProposal objects from parsed data.

        Args:
            proposals_data: List of proposal dicts from LLM response.
            agent_id: Agent identifier for proposal context.

        Returns:
            Tuple of validated proposals (skips invalid ones).
        """
        proposals: list[AdaptationProposal] = []
        for idx, item in enumerate(proposals_data):
            try:
                proposal_dict = {
                    "agent_id": str(agent_id),
                    **item,
                }
                proposal = AdaptationProposal(**proposal_dict)
                proposals.append(proposal)
                logger.debug(
                    EVOLUTION_PROPOSER_ANALYZE,
                    agent_id=str(agent_id),
                    axis=proposal.axis,
                    confidence=proposal.confidence,
                    source=proposal.source,
                )
            except ValidationError as exc:
                logger.warning(
                    EVOLUTION_PROPOSER_PARSE_ERROR,
                    agent_id=str(agent_id),
                    proposal_index=idx,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    reason="validation_failed",
                )
                continue

        return tuple(proposals)
