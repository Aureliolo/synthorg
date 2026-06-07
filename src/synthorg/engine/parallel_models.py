"""Parallel execution models.

Frozen Pydantic models for describing parallel agent assignments,
their outcomes, and execution group metadata.
"""

from collections import Counter
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.budget.currency import (
    CurrencyCode,
    assert_currencies_match,
)
from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.context import DEFAULT_MAX_TURNS
from synthorg.engine.run_result import AgentRunResult
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
)

_CURRENCY_UNSET: Final[object] = object()
"""Cache sentinel for :meth:`ParallelExecutionResult._resolved_currency`.

A separate object distinguishes "guard not yet run" from "guard ran
and resolved to ``None``" (no completed outcomes), since both states
would otherwise look identical.
"""


class AgentAssignment(BaseModel):
    """A single agent-task pairing for parallel execution.

    Attributes:
        identity: Agent to run.
        task: Task to execute.
        completion_config: Optional LLM completion configuration override.
        max_turns: Maximum execution turns.
        timeout_seconds: Optional wall-clock timeout for this agent.
        memory_messages: Pre-loaded memory messages for the agent.
        resource_claims: File paths requiring exclusive access (unique).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    identity: AgentIdentity = Field(description="Agent to run")
    task: Task = Field(description="Task to execute")
    completion_config: CompletionConfig | None = Field(
        default=None,
        description="Optional LLM completion configuration override",
    )
    max_turns: int = Field(
        default=DEFAULT_MAX_TURNS,
        ge=1,
        description="Maximum execution turns",
    )
    timeout_seconds: float | None = Field(
        default=None,
        gt=0,
        description="Optional wall-clock timeout for this agent",
    )
    memory_messages: tuple[ChatMessage, ...] = Field(
        default=(),
        description="Pre-loaded memory messages",
    )
    resource_claims: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="File paths requiring exclusive access (unique)",
    )

    @model_validator(mode="after")
    def _validate_resource_claims_unique(self) -> Self:
        if len(self.resource_claims) != len(set(self.resource_claims)):
            dupes = sorted(
                r
                for r in set(self.resource_claims)
                if self.resource_claims.count(r) > 1
            )
            msg = f"Duplicate resource claims: {dupes}"
            raise ValueError(msg)
        return self

    @computed_field(  # type: ignore[prop-decorator]
        description="Agent identifier string",
    )
    @property
    def agent_id(self) -> str:
        """Agent identifier (string form of UUID)."""
        return str(self.identity.id)

    @computed_field(  # type: ignore[prop-decorator]
        description="Task identifier string",
    )
    @property
    def task_id(self) -> str:
        """Task identifier."""
        return str(self.task.id)


class ParallelExecutionGroup(BaseModel):
    """A group of agent assignments to execute in parallel.

    Attributes:
        group_id: Unique group identifier.
        assignments: Agent-task pairings (non-empty).
        max_concurrency: Max simultaneous runs (None = unlimited).
        fail_fast: Cancel remaining assignments on first failure.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    group_id: NotBlankStr = Field(
        description="Unique group identifier",
    )
    assignments: tuple[AgentAssignment, ...] = Field(
        description="Agent-task pairings",
    )
    max_concurrency: int | None = Field(
        default=None,
        ge=1,
        description="Max simultaneous runs (None = unlimited)",
    )
    fail_fast: bool = Field(
        default=False,
        description="Cancel remaining on first failure",
    )

    @model_validator(mode="after")
    def _validate_assignments(self) -> Self:
        if not self.assignments:
            msg = "assignments must contain at least one entry"
            raise ValueError(msg)

        task_ids = [a.task_id for a in self.assignments]
        task_counts = Counter(task_ids)
        dupes = sorted(tid for tid, c in task_counts.items() if c > 1)
        if dupes:
            msg = f"Duplicate task IDs in assignments: {dupes}"
            raise ValueError(msg)

        agent_ids = [a.agent_id for a in self.assignments]
        agent_counts = Counter(agent_ids)
        dupes = sorted(aid for aid, c in agent_counts.items() if c > 1)
        if dupes:
            msg = f"Duplicate agent IDs in assignments: {dupes}"
            raise ValueError(msg)

        return self


class AgentOutcome(BaseModel):
    """Outcome of a single agent execution within a parallel group.

    Attributes:
        task_id: Task identifier.
        agent_id: Agent identifier.
        result: Present if execution completed (success or failure).
        error: Present if the agent failed, was cancelled, or could
            not execute. Mutually exclusive with ``result``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    task_id: NotBlankStr = Field(description="Task identifier")
    agent_id: NotBlankStr = Field(description="Agent identifier")
    result: AgentRunResult | None = Field(
        default=None,
        description="Present if execution completed",
    )
    error: str | None = Field(
        default=None,
        description="Present if agent failed, was cancelled, or could not execute",
    )

    @model_validator(mode="after")
    def _validate_result_or_error(self) -> Self:
        if (self.result is None) == (self.error is None):
            msg = "Exactly one of result or error must be set"
            raise ValueError(msg)
        if self.result is not None:
            if self.result.task_id != self.task_id:
                msg = (
                    f"result.task_id {self.result.task_id!r} "
                    f"must match task_id {self.task_id!r}"
                )
                raise ValueError(msg)
            if self.result.agent_id != self.agent_id:
                msg = (
                    f"result.agent_id {self.result.agent_id!r} "
                    f"must match agent_id {self.agent_id!r}"
                )
                raise ValueError(msg)
        return self

    @computed_field(  # type: ignore[prop-decorator]
        description="Whether the agent completed successfully",
    )
    @property
    def is_success(self) -> bool:
        """True when result is present and successful."""
        return self.result is not None and self.result.is_success


class ParallelExecutionResult(BaseModel):
    """Result of a complete parallel execution group.

    Attributes:
        group_id: Group identifier.
        outcomes: Tuple of agent outcomes.
        total_duration_seconds: Wall-clock duration of the group.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    group_id: NotBlankStr = Field(description="Group identifier")
    outcomes: tuple[AgentOutcome, ...] = Field(
        description="Tuple of agent outcomes",
    )
    total_duration_seconds: float = Field(
        ge=0.0,
        description="Wall-clock duration of the group execution",
    )

    def _completed_results(self) -> tuple[AgentRunResult, ...]:
        """Outcomes that produced an :class:`AgentRunResult`.

        Returns:
            Tuple of completed :class:`AgentRunResult` instances
            (outcomes whose ``result`` field is non-``None``).
        """
        return tuple(o.result for o in self.outcomes if o.result is not None)

    def _resolved_currency(self) -> CurrencyCode | None:
        """Resolve and cache the shared currency across completed outcomes.

        Runs :func:`assert_currencies_match` exactly once per instance
        and stores the result via ``object.__setattr__`` (frozen models
        forbid normal assignment).  Both :attr:`total_cost` and
        :attr:`currency` consult the cache so ``model_dump()`` emits at
        most one ``BUDGET_MIXED_CURRENCY_REJECTED`` warning even when
        the underlying outcomes mix currencies.

        Returns:
            The shared :class:`CurrencyCode` across completed
            outcomes; ``None`` when outcomes mix currencies (the
            mismatch already logged on first resolution).
        """
        cached: CurrencyCode | None | object = self.__dict__.get(
            "_currency_cache",
            _CURRENCY_UNSET,
        )
        if cached is not _CURRENCY_UNSET:
            return cached  # type: ignore[return-value]
        results = self._completed_results()
        resolved = assert_currencies_match(r.currency for r in results)
        object.__setattr__(self, "_currency_cache", resolved)
        return resolved

    @computed_field(  # type: ignore[prop-decorator]
        description="Total cost in the configured currency across all agents",
    )
    @property
    def total_cost(self) -> float:
        """Sum of costs from all outcomes with results.

        Same-currency invariant: every contributing outcome's
        ``AgentRunResult.currency`` must agree.  Mixed currencies raise
        :class:`MixedCurrencyAggregationError` (HTTP 409) before any
        summation, preserving the data-integrity contract.
        """
        self._resolved_currency()
        # lint-allow: currency-aggregation -- guard ran in _resolved_currency()
        return sum(r.total_cost for r in self._completed_results())

    @computed_field(  # type: ignore[prop-decorator]
        description="ISO 4217 currency that denominates ``total_cost``",
    )
    @property
    def currency(self) -> CurrencyCode | None:
        """Currency shared by every outcome's ``AgentRunResult``.

        ``None`` when no outcome carries a result (e.g. all failures);
        the same-currency invariant raises before this property could
        observe a mixed state, so callers can rely on
        at-most-one-currency semantics.
        """
        return self._resolved_currency()

    @computed_field(  # type: ignore[prop-decorator]
        description="Number of agents that succeeded",
    )
    @property
    def agents_succeeded(self) -> int:
        """Count of successful agent outcomes."""
        return sum(1 for o in self.outcomes if o.is_success)

    @computed_field(  # type: ignore[prop-decorator]
        description="Number of agents that failed",
    )
    @property
    def agents_failed(self) -> int:
        """Count of non-successful outcomes (includes cancelled)."""
        return sum(1 for o in self.outcomes if not o.is_success)

    @computed_field(  # type: ignore[prop-decorator]
        description="Whether all agents completed successfully",
    )
    @property
    def all_succeeded(self) -> bool:
        """True when every outcome is a success."""
        return all(o.is_success for o in self.outcomes)


class ParallelProgress(BaseModel):
    """Point-in-time snapshot of parallel execution progress.

    Attributes:
        group_id: Group identifier.
        total: Total number of assignments.
        completed: Number of assignments finished (success or failure).
        in_progress: Number of assignments currently running.
        pending: Derived: ``total - completed - in_progress`` (clamped >= 0).
        succeeded: Number of successful completions.
        failed: Number of failed completions.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    group_id: NotBlankStr = Field(description="Group identifier")
    total: int = Field(ge=0, description="Total assignments")
    completed: int = Field(ge=0, description="Finished assignments")
    in_progress: int = Field(ge=0, description="Currently running")
    succeeded: int = Field(ge=0, description="Successful completions")
    failed: int = Field(ge=0, description="Failed completions")

    @model_validator(mode="after")
    def _validate_counts(self) -> Self:
        if self.completed + self.in_progress > self.total:
            msg = "completed + in_progress must not exceed total"
            raise ValueError(msg)
        if self.succeeded + self.failed > self.completed:
            msg = "succeeded + failed must not exceed completed"
            raise ValueError(msg)
        return self

    @computed_field(  # type: ignore[prop-decorator]
        description="Not yet started",
    )
    @property
    def pending(self) -> int:
        """Assignments not yet started."""
        return max(0, self.total - self.completed - self.in_progress)
