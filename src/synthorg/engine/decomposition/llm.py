"""LLM-based task decomposition strategy.

Uses an LLM provider with tool calling to break a task into subtasks.
Falls back to parsing JSON from content when tool calls are absent.
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from synthorg.engine.decomposition.llm_prompt import (
    build_decomposition_tool,
    build_retry_message,
    build_system_message,
    build_task_message,
    parse_content_response,
    parse_tool_call_response,
)
from synthorg.engine.errors import (
    DecompositionDepthError,
    DecompositionError,
)
from synthorg.observability import get_logger
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_COMPLETED,
    DECOMPOSITION_FAILED,
    DECOMPOSITION_LLM_CALL_COMPLETE,
    DECOMPOSITION_LLM_CALL_START,
    DECOMPOSITION_LLM_PARSE_ERROR,
    DECOMPOSITION_LLM_RETRY,
    DECOMPOSITION_VALIDATION_ERROR,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
)

if TYPE_CHECKING:
    from synthorg.core.task import Task
    from synthorg.engine.decomposition.models import (
        DecompositionContext,
        DecompositionPlan,
    )
    from synthorg.providers.models import (
        CompletionResponse,
    )
    from synthorg.providers.protocol import (
        CompletionProvider,
    )

logger = get_logger(__name__)


class LlmDecompositionConfig(BaseModel):
    """Configuration for the LLM decomposition strategy.

    Attributes:
        max_retries: Maximum retry attempts on parse failure.
        temperature: Sampling temperature for the LLM call.
        max_output_tokens: Maximum tokens for the LLM response.
    """

    model_config = ConfigDict(frozen=True)

    max_retries: int = Field(default=2, ge=0, le=5, description="Max retry attempts")
    temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="Sampling temperature",
    )
    max_output_tokens: int = Field(
        default=4096,
        gt=0,
        description="Max output tokens",
    )


class LlmDecompositionStrategy:
    """Decomposition strategy that uses an LLM to generate plans.

    Sends the task details to an LLM provider with a tool
    definition for structured output. Falls back to parsing
    JSON from content if tool calls are absent. Retries on
    parse/validation failures up to ``max_retries`` times.
    """

    __slots__ = ("_config", "_model", "_provider")

    def __init__(
        self,
        *,
        provider: CompletionProvider,
        model: str,
        config: LlmDecompositionConfig | None = None,
    ) -> None:
        """Initialize the LLM decomposition strategy.

        Args:
            provider: LLM completion provider for making calls.
            model: Model identifier to use for decomposition.
            config: Optional strategy configuration. Uses defaults
                if not provided.

        Raises:
            ValueError: If model is blank.
        """
        if not model or not model.strip():
            msg = "model must be a non-blank string"
            logger.warning(DECOMPOSITION_FAILED, error=msg)
            raise ValueError(msg)
        self._provider = provider
        self._model = model
        self._config = config or LlmDecompositionConfig()

    async def decompose(
        self,
        task: Task,
        context: DecompositionContext,
    ) -> DecompositionPlan:
        """Decompose a task into subtasks using an LLM.

        Args:
            task: The parent task to decompose.
            context: Decomposition constraints.

        Returns:
            A decomposition plan with subtask definitions.

        Raises:
            DecompositionDepthError: If current depth meets or
                exceeds max depth.
            DecompositionError: If all retries are exhausted or
                the plan violates constraints.
        """
        self._check_depth(context)

        messages = self._build_initial_messages(task, context)
        tool_def = build_decomposition_tool()
        comp_config = CompletionConfig(
            temperature=self._config.temperature,
            max_tokens=self._config.max_output_tokens,
        )

        last_error: str | None = None
        last_response: CompletionResponse | None = None
        attempts = 1 + self._config.max_retries

        for attempt in range(attempts):
            if attempt > 0 and last_error is not None:
                logger.info(
                    DECOMPOSITION_LLM_RETRY,
                    task_id=task.id,
                    attempt=attempt,
                    error=last_error,
                )
                # Include the failed assistant response for context
                assistant_msg = ChatMessage(
                    role=MessageRole.ASSISTANT,
                    content=(last_response.content or "") if last_response else "",
                    tool_calls=last_response.tool_calls if last_response else (),
                )
                messages = [
                    *messages,
                    assistant_msg,
                    build_retry_message(last_error),
                ]

            logger.debug(
                DECOMPOSITION_LLM_CALL_START,
                task_id=task.id,
                model=self._model,
                attempt=attempt,
            )

            response = await self._provider.complete(
                messages,
                self._model,
                tools=[tool_def],
                config=comp_config,
            )
            last_response = response

            logger.debug(
                DECOMPOSITION_LLM_CALL_COMPLETE,
                task_id=task.id,
                finish_reason=response.finish_reason.value,
            )

            try:
                plan = self._parse_response(response, task.id)
            except DecompositionError as exc:
                last_error = str(exc)
                logger.warning(
                    DECOMPOSITION_LLM_PARSE_ERROR,
                    task_id=task.id,
                    attempt=attempt,
                    error=last_error,
                )
                continue

            try:
                self._validate_plan(plan, context)
            except DecompositionError as exc:
                last_error = str(exc)
                logger.warning(
                    DECOMPOSITION_VALIDATION_ERROR,
                    task_id=task.id,
                    error=last_error,
                )
                continue

            logger.debug(
                DECOMPOSITION_COMPLETED,
                task_id=task.id,
                strategy="llm",
                subtask_count=len(plan.subtasks),
            )
            return plan

        msg = (
            f"LLM decomposition retries exhausted after "
            f"{attempts} attempts for task {task.id!r}"
        )
        logger.warning(
            DECOMPOSITION_FAILED,
            task_id=task.id,
            error=msg,
        )
        raise DecompositionError(msg)

    def get_strategy_name(self) -> str:
        """Return the strategy name."""
        return "llm"

    @staticmethod
    def _check_depth(context: DecompositionContext) -> None:
        """Raise if depth limit is reached.

        Args:
            context: Decomposition constraints.

        Raises:
            DecompositionDepthError: If current depth meets or
                exceeds max depth.
        """
        if context.current_depth >= context.max_depth:
            msg = (
                f"Decomposition depth {context.current_depth} "
                f"meets or exceeds max depth {context.max_depth}"
            )
            logger.warning(DECOMPOSITION_VALIDATION_ERROR, error=msg)
            raise DecompositionDepthError(msg)

    @staticmethod
    def _build_initial_messages(
        task: Task,
        context: DecompositionContext,
    ) -> list[ChatMessage]:
        """Build the initial system + task messages.

        Args:
            task: The parent task.
            context: Decomposition constraints.

        Returns:
            List of initial chat messages.
        """
        return [
            build_system_message(),
            build_task_message(task, context),
        ]

    @staticmethod
    def _parse_response(
        response: CompletionResponse,
        parent_task_id: str,
    ) -> DecompositionPlan:
        """Parse a plan from tool calls, content fallback, or raise.

        Args:
            response: The LLM completion response.
            parent_task_id: ID of the parent task.

        Returns:
            A parsed ``DecompositionPlan``.

        Raises:
            DecompositionError: If both parsing paths fail.
        """
        if response.tool_calls:
            return parse_tool_call_response(response, parent_task_id)
        if response.content is not None:
            return parse_content_response(response, parent_task_id)
        msg = "Response has no tool calls and no content"
        logger.warning(DECOMPOSITION_LLM_PARSE_ERROR, error=msg)
        raise DecompositionError(msg)

    @staticmethod
    def _validate_plan(
        plan: DecompositionPlan,
        context: DecompositionContext,
    ) -> None:
        """Validate plan against context constraints.

        Args:
            plan: The parsed decomposition plan.
            context: Decomposition constraints.

        Raises:
            DecompositionError: If subtask count exceeds limit.
        """
        if len(plan.subtasks) > context.max_subtasks:
            msg = (
                f"Plan has {len(plan.subtasks)} subtasks, "
                f"exceeds max_subtasks of "
                f"{context.max_subtasks}"
            )
            logger.warning(
                DECOMPOSITION_VALIDATION_ERROR,
                subtask_count=len(plan.subtasks),
                max_subtasks=context.max_subtasks,
                error=msg,
            )
            raise DecompositionError(msg)
