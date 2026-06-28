"""LLM-based proposer for success-derived procedural memory entries.

Uses a SEPARATE completion provider call to analyse a successful task
execution and produce a procedural memory proposal that captures reusable
knowledge from the successful approach. Similar to ProceduralMemoryProposer
but optimized for success outcomes with a lighter system prompt.
"""

from typing import ClassVar

from synthorg.budget.call_category import LLMCallCategory

# ``CostTrackerProtocol`` is part of ``SuccessMemoryProposer.__init__``'s
# annotation, so it must resolve at runtime when downstream tooling
# evaluates type hints (DI containers, doc generators).  Importing at
# module top -- not under ``TYPE_CHECKING`` -- keeps the name in module
# globals.
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.loop_protocol import ExecutionResult
from synthorg.engine.prompt_safety import (
    TAG_TASK_DATA,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.llm.metadata import ModelPinMetadata
from synthorg.llm.model_pins import pin_for
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.memory.procedural._response_parsing import parse_proposal_response
from synthorg.memory.procedural.models import (
    ProceduralMemoryConfig,
    ProceduralMemoryProposal,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.procedural_memory import (
    PROCEDURAL_MEMORY_PROPOSER_INIT,
    PROCEDURAL_MEMORY_SKIPPED,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.errors import ProviderError
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are a success analysis assistant. Given a description of a "
    "successful agent task execution, extract and propose a procedural "
    "memory entry that captures the successful approach and reasoning.\n\n"
    "Respond with a JSON object containing exactly these fields:\n"
    '- "discovery": A one-sentence summary (~100 tokens) for retrieval.\n'
    '- "condition": When this successful approach applies.\n'
    '- "action": What worked well and should be repeated.\n'
    '- "rationale": Why this approach was effective.\n'
    '- "execution_steps": Ordered list of concrete steps that led to success '
    '(e.g. ["Step 1", "Step 2"]).\n'
    '- "confidence": Your confidence in this proposal (0.0-1.0).\n'
    '- "tags": List of semantic tags (e.g. ["efficient", "multi_tool"]).\n\n'
    "Respond ONLY with the JSON object, no markdown fences or explanation.\n\n"
    + untrusted_content_directive((TAG_TASK_DATA,))
)


def _build_user_message(execution_result: ExecutionResult) -> str:
    """Format execution context into a user message for the proposer LLM.

    The execution context (turn count, tool names, outcome) is wrapped
    via :func:`wrap_untrusted` under :data:`TAG_TASK_DATA` so the
    proposer LLM treats the body as data; the system prompt's
    ``untrusted_content_directive`` gives the model an explicit
    instruction to ignore embedded directives.

    Returns:
        Result of type ``str``.
    """
    # Collect all unique tools used across all turns
    all_tools: set[str] = set()
    for turn in execution_result.turns:
        all_tools.update(turn.tool_calls_made)
    tools_str = ", ".join(sorted(all_tools)) if all_tools else "none"

    body = (
        f"Turns completed: {len(execution_result.turns)}\n"
        f"Tools used: {tools_str}\n"
        "Outcome: SUCCESSFUL"
    )
    return wrap_untrusted(TAG_TASK_DATA, body)


class SuccessMemoryProposer:
    """Generates procedural memory proposals from successful task execution.

    Uses a separate LLM call to analyse a successful execution
    and produce a ``ProceduralMemoryProposal``.  Non-retryable
    provider errors propagate; retryable errors return ``None``.

    Args:
        provider: Completion provider for the proposer LLM call.
        config: Procedural memory configuration.
    """

    _PURPOSE_ID: ClassVar[PromptPurposeId] = PromptPurposeId.PROCEDURAL_SUCCESS_PROPOSER

    @property
    def metadata(self) -> ModelPinMetadata:
        """Pinned model + sampling for this prompt class."""
        return pin_for(self._PURPOSE_ID)

    def __init__(
        self,
        *,
        provider: CompletionProvider,
        config: ProceduralMemoryConfig,
        cost_tracker: CostTrackerProtocol | None = None,
    ) -> None:
        self._provider = provider
        self._config = config
        self._cost_tracker = cost_tracker
        self._completion_config = CompletionConfig(
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            top_p=config.top_p,
        )
        logger.debug(
            PROCEDURAL_MEMORY_PROPOSER_INIT,
            model=config.model,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            min_confidence=config.min_confidence,
        )

    async def propose(
        self,
        execution_result: ExecutionResult,
    ) -> ProceduralMemoryProposal | None:
        """Analyse success and propose a procedural memory entry.

        Returns ``None`` when the LLM response is empty, malformed,
        or below the confidence threshold.  Non-retryable provider
        errors propagate to the caller.

        Args:
            execution_result: Execution result object with turn_count
                and tools_used attributes.

        Returns:
            A validated proposal, or ``None`` if skipped.

        Raises:
            ProviderError: If the related operation fails.
        """
        try:
            messages = [
                ChatMessage(role=MessageRole.SYSTEM, content=_SYSTEM_PROMPT),
                ChatMessage(
                    role=MessageRole.USER,
                    content=_build_user_message(execution_result),
                ),
            ]
            async with cost_recording_scope(
                cost_tracker=self._cost_tracker,
                agent_id=NotBlankStr("system"),
                task_id=NotBlankStr("system:procedural:success_proposer"),
                purpose=self.metadata.prompt_class_id,
                call_category=LLMCallCategory.SYSTEM,
            ):
                response = await self._provider.complete(
                    messages,
                    self._config.model,
                    config=self._completion_config,
                )
        except ProviderError as exc:
            if not exc.is_retryable:
                raise
            logger.warning(
                PROCEDURAL_MEMORY_SKIPPED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="retryable_provider_error",
                is_retryable=True,
            )
            return None
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # Drop exc_info + scrub message.
            logger.warning(
                PROCEDURAL_MEMORY_SKIPPED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="unexpected_error",
                is_retryable=False,
            )
            return None

        return parse_proposal_response(response.content, self._config)
