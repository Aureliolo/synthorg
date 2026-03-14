"""Task completion metrics model.

Proxy overhead metrics for an agent run, computed from
``AgentRunResult`` data per the Operations design page.
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.engine.run_result import AgentRunResult


class TaskCompletionMetrics(BaseModel):
    """Proxy overhead metrics for an agent run (see Operations design page).

    Computed from ``AgentRunResult`` after execution to surface
    orchestration overhead indicators (turns, tokens, cost, duration).

    Attributes:
        task_id: Task identifier (``None`` for future taskless runs).
        agent_id: Agent identifier (string form of UUID).
        turns_per_task: Number of LLM turns to complete the task.
        tokens_per_task: Total tokens consumed (input + output).
        cost_per_task: Total USD cost for the task.
        duration_seconds: Wall-clock execution time in seconds.
        prompt_tokens: Estimated system prompt tokens (per-call estimate
            from ``SystemPrompt.estimated_tokens``).
        prompt_token_ratio: Per-call ratio of prompt tokens to total tokens
            (overhead indicator, derived via ``@computed_field``).  For
            multi-turn runs, the actual overhead is higher because the
            system prompt is resent on every turn.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    task_id: NotBlankStr | None = Field(
        default=None,
        description="Task identifier",
    )
    agent_id: NotBlankStr = Field(description="Agent identifier")
    turns_per_task: int = Field(
        ge=0,
        description="Number of LLM turns to complete the task",
    )
    tokens_per_task: int = Field(
        ge=0,
        description="Total tokens consumed (input + output)",
    )
    cost_per_task: float = Field(
        ge=0.0,
        description="Total USD cost for the task",
    )
    duration_seconds: float = Field(
        ge=0.0,
        description="Wall-clock execution time in seconds",
    )
    prompt_tokens: int = Field(
        default=0,
        ge=0,
        description="Estimated system prompt tokens",
    )

    @model_validator(mode="after")
    def _cap_prompt_tokens(self) -> TaskCompletionMetrics:
        """Cap prompt_tokens to tokens_per_task.

        The heuristic estimator (char/4) can legitimately overshoot
        actual provider-reported tokens, so we clamp rather than reject.
        Skipped when ``tokens_per_task`` is 0 (zero-turn runs).
        """
        if self.tokens_per_task > 0 and self.prompt_tokens > self.tokens_per_task:
            object.__setattr__(self, "prompt_tokens", self.tokens_per_task)
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def prompt_token_ratio(self) -> float:
        """Per-call ratio of prompt tokens to total tokens (overhead indicator).

        For multi-turn runs the actual overhead is higher because the
        system prompt is resent on every turn.
        """
        if self.tokens_per_task > 0:
            return self.prompt_tokens / self.tokens_per_task
        return 0.0

    @classmethod
    def from_run_result(cls, result: AgentRunResult) -> TaskCompletionMetrics:
        """Build metrics from an agent run result.

        Args:
            result: The ``AgentRunResult`` to extract metrics from.

        Returns:
            New ``TaskCompletionMetrics`` with values extracted from
            the result's execution context and metadata.
        """
        accumulated = result.execution_result.context.accumulated_cost
        return cls(
            task_id=result.task_id,
            agent_id=result.agent_id,
            turns_per_task=result.total_turns,
            tokens_per_task=accumulated.total_tokens,
            cost_per_task=result.total_cost_usd,
            duration_seconds=result.duration_seconds,
            prompt_tokens=result.system_prompt.estimated_tokens,
        )
