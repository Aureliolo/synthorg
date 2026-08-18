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
from synthorg.engine.loop_budget_defaults import DEFAULT_MAX_TURNS
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

    @computed_field(
        description="Agent identifier string",
    )
    @property
    def agent_id(self) -> str:
        """Agent identifier (string form of UUID)."""
        return str(self.identity.id)

    @computed_field(
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
        dag_level: Which dependency level of the plan these assignments came
            from. A group is one round of AGENTS, and a level whose subtasks
            share an agent is split across several groups, so the group's
            position in the sequence does NOT give its level back. Anything
            reasoning about dependencies has to read it here: comparing
            positions instead treats a sibling as an upstream.
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
    dag_level: int = Field(
        default=0,
        ge=0,
        description="Dependency level these assignments came from",
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

    @computed_field(
        description="Whether the agent completed successfully",
    )
    @property
    def is_success(self) -> bool:
        """True when result is present and successful."""
        return self.result is not None and self.result.is_success

    @computed_field(
        description="Whether the agent is suspended awaiting a human decision",
    )
    @property
    def is_awaiting_human(self) -> bool:
        """True when the agent parked on an escalation."""
        return self.result is not None and self.result.is_awaiting_human

    @computed_field(
        description="Whether the agent failed (neither succeeded nor parked)",
    )
    @property
    def is_failure(self) -> bool:
        """True when the outcome is neither a success nor a human wait.

        An outcome carrying an ``error`` (a raise, or a fail-fast
        cancellation) is a failure, and so is any terminal reason that is
        not ``COMPLETED`` or ``PARKED``.

        Deliberately the complement rather than a list of failing reasons: a
        terminal reason added later has not succeeded and is not waiting on
        anyone, so counting it as a failure is the fail-closed answer and
        needs no one to remember to widen a list.
        """
        return not self.is_success and not self.is_awaiting_human


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
        cached: CurrencyCode | object | None = self.__dict__.get(
            "_currency_cache",
            _CURRENCY_UNSET,
        )
        if cached is not _CURRENCY_UNSET:
            return cached  # type: ignore[return-value]
        results = self._completed_results()
        resolved = assert_currencies_match(r.currency for r in results)
        object.__setattr__(self, "_currency_cache", resolved)
        return resolved

    @computed_field(
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

    @computed_field(
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

    @computed_field(
        description="Number of agents that succeeded",
    )
    @property
    def agents_succeeded(self) -> int:
        """Count of successful agent outcomes."""
        return sum(1 for o in self.outcomes if o.is_success)

    @computed_field(
        description="Number of agents suspended awaiting a human decision",
    )
    @property
    def agents_awaiting_human(self) -> int:
        """Count of outcomes parked on an escalation."""
        return sum(1 for o in self.outcomes if o.is_awaiting_human)

    @computed_field(
        description="Number of agents that failed",
    )
    @property
    def agents_failed(self) -> int:
        """Count of failed outcomes (includes cancelled, excludes parked).

        A parked agent is waiting on a human, not failing, so it is counted
        by :attr:`agents_awaiting_human` instead.
        """
        return sum(1 for o in self.outcomes if o.is_failure)

    @computed_field(
        description="Whether any agent failed",
    )
    @property
    def any_failed(self) -> bool:
        """True when at least one outcome genuinely failed.

        The question a caller deciding "did this wave fail" must ask.
        ``not all_succeeded`` is the wrong test: a group in which every
        non-success is a human wait has not failed, it is unfinished.
        """
        return any(o.is_failure for o in self.outcomes)

    @computed_field(
        description="Whether all agents completed successfully",
    )
    @property
    def all_succeeded(self) -> bool:
        """True when every outcome is a success.

        A parked outcome is deliberately not a success: the group is
        incomplete until the human decides.
        """
        return all(o.is_success for o in self.outcomes)


class ParallelProgress(BaseModel):
    """Point-in-time snapshot of parallel execution progress.

    Attributes:
        group_id: Group identifier.
        total: Total number of assignments.
        completed: Number of assignments finished (success, failure, or a
            park awaiting a human).
        in_progress: Number of assignments currently running.
        pending: Derived: ``total - completed - in_progress`` (clamped >= 0).
        succeeded: Number of successful completions.
        failed: Number of failed completions.
        awaiting_human: Number of runs parked on an escalation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    group_id: NotBlankStr = Field(description="Group identifier")
    total: int = Field(ge=0, description="Total assignments")
    completed: int = Field(ge=0, description="Finished assignments")
    in_progress: int = Field(ge=0, description="Currently running")
    succeeded: int = Field(ge=0, description="Successful completions")
    failed: int = Field(ge=0, description="Failed completions")
    awaiting_human: int = Field(
        default=0,
        ge=0,
        description="Runs parked awaiting a human decision",
    )

    @model_validator(mode="after")
    def _validate_counts(self) -> Self:
        """Bound the counters without demanding they agree exactly.

        The three outcome categories are exhaustive over a completed run, so
        equality looks like the stronger check. It is the wrong one: this is
        a snapshot of a live group, taken by one task's callback while its
        siblings are mid-update, and a task that has counted its category but
        not yet its completion is a moment that legitimately occurs. Bounding
        catches an accounting error; equality would report the moment.

        Returns:
            The validated snapshot.

        Raises:
            ValueError: A counter exceeds what the group can account for.
        """
        if self.completed + self.in_progress > self.total:
            msg = "completed + in_progress must not exceed total"
            raise ValueError(msg)
        if self.succeeded + self.failed + self.awaiting_human > self.total:
            msg = "succeeded + failed + awaiting_human must not exceed total"
            raise ValueError(msg)
        return self

    @computed_field(
        description="Not yet started",
    )
    @property
    def pending(self) -> int:
        """Assignments not yet started."""
        return max(0, self.total - self.completed - self.in_progress)
