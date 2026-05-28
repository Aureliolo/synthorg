"""Lightweight per-agent runtime state for dashboard queries and recovery.

``AgentRuntimeState`` is a frozen Pydantic model that captures an agent's
current execution status (idle / executing / paused), the associated
execution and task identifiers, cost, and turn count.  It is persisted
via :class:`~synthorg.persistence.repositories.AgentStateRepository` and
is independent of the heavier checkpoint system.
"""

from datetime import UTC, datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.budget.currency import CurrencyCode
from synthorg.core.enums import ExecutionStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.context import AgentContext


class AgentRuntimeState(BaseModel):
    """Frozen snapshot of an agent's runtime execution state.

    Attributes:
        agent_id: Primary key -- the agent identifier.
        execution_id: Current execution run identifier (``None`` when idle).
        task_id: Current task identifier (``None`` when idle or taskless).
        status: Execution status (idle / executing / paused).
        turn_count: Turns completed in the current execution.
        accumulated_cost: Cost accumulated in the current execution,
            denominated in ``currency``.
        currency: ISO 4217 currency code for ``accumulated_cost``.
            Required even when the agent is IDLE and the balance is
            zero so the persisted row always carries an unambiguous
            unit; callers pass the operator's active ``budget.currency``
            at construction time.
        last_activity_at: Timestamp of the last state update.
        started_at: When the current execution started (``None`` when idle).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Agent identifier (primary key)")
    execution_id: NotBlankStr | None = Field(
        default=None,
        description="Current execution run identifier",
    )
    task_id: NotBlankStr | None = Field(
        default=None,
        description="Current task identifier",
    )
    status: ExecutionStatus = Field(description="Execution status")
    turn_count: int = Field(default=0, ge=0, description="Turns completed")
    accumulated_cost: float = Field(
        default=0.0,
        ge=0.0,
        description="Cost in current execution, denominated in ``currency``",
    )
    currency: CurrencyCode = Field(
        description="ISO 4217 currency code for ``accumulated_cost``",
    )
    last_activity_at: AwareDatetime = Field(
        description="Timestamp of last state update",
    )
    started_at: AwareDatetime | None = Field(
        default=None,
        description="When the current execution started",
    )

    def _idle_violations(self) -> list[str]:
        """Collect field violations for IDLE status.

        Returns:
            List of violation strings; empty when every IDLE
            invariant holds.
        """
        violations: list[str] = []
        if self.execution_id is not None:
            violations.append("execution_id must be None")
        if self.task_id is not None:
            violations.append("task_id must be None")
        if self.started_at is not None:
            violations.append("started_at must be None")
        if self.turn_count != 0:
            violations.append("turn_count must be 0")
        if self.accumulated_cost != 0.0:
            violations.append("accumulated_cost must be 0.0")
        return violations

    @model_validator(mode="after")
    def _validate_status_invariants(self) -> AgentRuntimeState:
        """Enforce status-dependent field invariants.

        * **IDLE** requires ``execution_id``, ``task_id``, and
          ``started_at`` to be ``None``, and ``turn_count`` and
          ``accumulated_cost`` to be zero.
        * **EXECUTING** / **PAUSED** require ``execution_id`` and
          ``started_at`` to be set.

        Returns:
            ``self`` unchanged when every status-dependent
            invariant holds.

        Raises:
            ValueError: When any status-specific field invariant is
                violated (the message names the offending fields).
        """
        if self.status == ExecutionStatus.IDLE:
            violations = self._idle_violations()
            if violations:
                msg = f"IDLE state invariant violated: {'; '.join(violations)}"
                raise ValueError(msg)
        else:
            active_violations: list[str] = []
            if self.execution_id is None:
                active_violations.append("execution_id is required")
            if self.started_at is None:
                active_violations.append("started_at is required")
            if active_violations:
                msg = (
                    f"{self.status.value.upper()} state invariant violated: "
                    f"{'; '.join(active_violations)}"
                )
                raise ValueError(msg)
        return self

    @classmethod
    def idle(
        cls,
        agent_id: NotBlankStr,
        *,
        currency: CurrencyCode,
    ) -> AgentRuntimeState:
        """Create an IDLE state for the given agent.

        Args:
            agent_id: The agent identifier.
            currency: Operator's active ISO 4217 currency code.  Stored
                even when the balance is zero so the persisted row keeps
                an unambiguous unit if the agent later transitions to
                EXECUTING.

        Returns:
            A new ``AgentRuntimeState`` in IDLE status.
        """
        return cls(
            agent_id=agent_id,
            status=ExecutionStatus.IDLE,
            currency=currency,
            last_activity_at=datetime.now(UTC),
        )

    @classmethod
    def from_context(
        cls,
        context: AgentContext,
        status: ExecutionStatus,
        *,
        currency: CurrencyCode,
    ) -> AgentRuntimeState:
        """Create a runtime state from an ``AgentContext``.

        Args:
            context: The agent execution context.
            status: Must be ``EXECUTING`` or ``PAUSED`` (not ``IDLE``).
            currency: Operator's active ISO 4217 currency code used to
                denominate ``context.accumulated_cost``.

        Returns:
            A new ``AgentRuntimeState`` derived from the context.

        Raises:
            ValueError: If *status* is ``IDLE``.
        """
        if status == ExecutionStatus.IDLE:
            msg = "Cannot create from_context with IDLE status; use idle() instead"
            raise ValueError(msg)
        task_execution = context.task_execution
        return cls(
            agent_id=str(context.identity.id),
            execution_id=context.execution_id,
            task_id=task_execution.task.id if task_execution is not None else None,
            status=status,
            turn_count=context.turn_count,
            accumulated_cost=context.accumulated_cost.cost,
            currency=currency,
            last_activity_at=datetime.now(UTC),
            started_at=context.started_at,
        )
