"""Runtime LLM call classification service.

Provides stateless, protocol-based classification of LLM calls into
one of four categories (PRODUCTIVE, COORDINATION, SYSTEM, EMBEDDING)
based on a ``ClassificationContext`` built from execution metadata.

Classification priority (highest wins):
1. EMBEDDING -- is_embedding_operation
2. COORDINATION -- is_delegation or is_review or is_meeting
3. SYSTEM -- is_planning_phase or is_system_prompt or is_quality_judge
4. PRODUCTIVE -- default (everything else)
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.budget.call_category import LLMCallCategory
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger

logger = get_logger(__name__)


class ClassificationContext(BaseModel):
    """Execution context used to classify an LLM call.

    Attributes:
        turn_number: 1-indexed LLM turn number within the execution.
        agent_id: Executing agent identifier.
        task_id: Task identifier.
        is_delegation: Turn is an agent delegation handoff.
        is_review: Turn is a review/verification step.
        is_meeting: Turn is inter-agent discussion/coordination.
        is_planning_phase: Turn is in the planning phase.
        is_system_prompt: Turn is processing a system prompt.
        is_embedding_operation: Turn uses an embedding model.
        is_quality_judge: Turn is performing quality judging.
        tool_calls_made: Names of tools invoked this turn (context only).
        agent_role: Optional semantic role of the agent (context only).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    turn_number: int = Field(gt=0, description="1-indexed turn number")
    agent_id: NotBlankStr = Field(description="Executing agent identifier")
    task_id: NotBlankStr = Field(description="Task identifier")
    is_delegation: bool = Field(default=False, description="Agent delegation handoff")
    is_review: bool = Field(default=False, description="Review/verification step")
    is_meeting: bool = Field(default=False, description="Inter-agent discussion")
    is_planning_phase: bool = Field(default=False, description="Planning phase turn")
    is_system_prompt: bool = Field(
        default=False, description="System prompt processing"
    )
    is_embedding_operation: bool = Field(
        default=False, description="Embedding model call"
    )
    is_quality_judge: bool = Field(default=False, description="Quality judging turn")
    tool_calls_made: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Tool names invoked this turn (context only)",
    )
    agent_role: NotBlankStr | None = Field(
        default=None,
        description="Semantic role of the agent (context only)",
    )


@runtime_checkable
class CallClassificationStrategy(Protocol):
    """Protocol for LLM call classification strategies.

    Implementations are stateless and pure -- no I/O, no side effects.
    """

    def classify(self, context: ClassificationContext) -> LLMCallCategory:
        """Classify an LLM call given its execution context.

        Args:
            context: Metadata about the LLM call to classify.

        Returns:
            The category that best describes this call.
        """
        ...


class RulesBasedClassifier:
    """Default priority-based rules classifier.

    Priority order (highest wins):
    1. EMBEDDING -- ``is_embedding_operation``
    2. COORDINATION -- ``is_delegation or is_review or is_meeting``
    3. SYSTEM -- ``is_planning_phase or is_system_prompt or is_quality_judge``
    4. PRODUCTIVE -- default
    """

    def classify(self, context: ClassificationContext) -> LLMCallCategory:
        """Classify using fixed priority rules.

        Args:
            context: Metadata about the LLM call to classify.

        Returns:
            The highest-priority matching category.
        """
        if context.is_embedding_operation:
            return LLMCallCategory.EMBEDDING
        if context.is_delegation or context.is_review or context.is_meeting:
            return LLMCallCategory.COORDINATION
        if (
            context.is_planning_phase
            or context.is_system_prompt
            or context.is_quality_judge
        ):
            return LLMCallCategory.SYSTEM
        return LLMCallCategory.PRODUCTIVE


_DEFAULT_CLASSIFIER = RulesBasedClassifier()


def classify_call(context: ClassificationContext) -> LLMCallCategory:
    """Classify an LLM call using the default rules-based classifier.

    Convenience function wrapping :class:`RulesBasedClassifier`.
    Stateless -- safe to call from any context.

    Args:
        context: Metadata about the LLM call to classify.

    Returns:
        The category for this call.
    """
    return _DEFAULT_CLASSIFIER.classify(context)
