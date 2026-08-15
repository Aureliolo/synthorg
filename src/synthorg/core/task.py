"""Task domain model and acceptance criteria."""

import copy
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core._task_invariants import (
    check_assignment_consistency,
    check_blocked_reason_pairing,
    check_collections,
    check_delegation,
    check_plan_linkage,
)
from synthorg.core.artifact import ExpectedArtifact
from synthorg.core.task_enums import (
    BlockedReason,
    Complexity,
    CoordinationTopology,
    Priority,
    Stakes,
    TaskSource,
    TaskStatus,
    TaskStructure,
    TaskType,
)
from synthorg.core.task_transitions import validate_transition
from synthorg.core.types import NotBlankStr
from synthorg.core.validation import validate_iso8601_deadline
from synthorg.observability import get_logger
from synthorg.observability.events.task import TASK_STATUS_CHANGED
from synthorg.ontology.decorator import ontology_entity

logger = get_logger(__name__)


class AcceptanceCriterion(BaseModel):
    """A single acceptance criterion for a task.

    Attributes:
        description: The criterion text.
        met: Whether this criterion has been satisfied.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    description: NotBlankStr = Field(
        description="Criterion text",
    )
    met: bool = Field(
        default=False,
        description="Whether this criterion has been satisfied",
    )


@ontology_entity
class Task(BaseModel):
    """A unit of work within the company.

    Represents a task from creation through completion, with full
    lifecycle tracking, dependency modeling, and acceptance criteria.
    Field schema matches the Engine design page.

    Attributes:
        id: Unique task identifier (auto-generated UUID).
        title: Short task title.
        description: Detailed task description.
        type: Classification of the task's work type.
        priority: Task urgency and importance level.
        project: Project ID this task belongs to.
        plan_id: Plan whose dispatch created this task (``None`` for a task
            that did not come from a plan, e.g. a directly filed one).
        plan_item_id: Plan item this task implements (``None`` likewise).
            Stamped at dispatch so plan items and their tasks correlate as
            data rather than by re-deriving the deterministic id mapping.
        created_by: Agent name of the task creator.
        requested_by_user_id: User id of the human who filed the task via
            the API (``None`` for agent-internal tasks); gates SSE
            event-stream session ownership.
        assigned_to: Agent ID of the assignee (``None`` if unassigned).
        reviewers: Agent IDs of designated reviewers.
        dependencies: IDs of tasks this task depends on.
        artifacts_expected: Artifacts expected to be produced.
        acceptance_criteria: Structured acceptance criteria.
        estimated_complexity: Task complexity estimate.
        budget_limit: Maximum spend for this task in the configured currency.
        deadline: Optional deadline (ISO 8601 string or ``None``).
        max_retries: Max reassignment attempts after failure (default 1).
        status: Current lifecycle status.
        parent_task_id: Parent task ID when created via delegation
            (``None`` for root tasks).
        delegation_chain: Ordered agent names of delegators (root first).
        task_structure: Classification of how subtasks relate to each
            other (``None`` when not yet classified).
        coordination_topology: Coordination topology for multi-agent
            execution (defaults to ``AUTO``).
        middleware_override: Per-task middleware chain override
            (``None`` uses company default chain).
        metadata: Arbitrary key-value metadata for pipeline tracking,
            labels, and operator-defined context.  Deep-copied at
            construction to prevent external mutation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(default_factory=uuid4, description="Unique task identifier")
    title: NotBlankStr = Field(description="Short task title")
    description: NotBlankStr = Field(
        description="Detailed task description",
    )
    type: TaskType = Field(description="Task work type")
    priority: Priority = Field(
        default=Priority.MEDIUM,
        description="Task priority",
    )
    project: NotBlankStr = Field(
        description="Project ID this task belongs to",
    )
    blocked_reason: BlockedReason | None = Field(
        default=None,
        description=(
            "Why the task is parked at BLOCKED, when the writer named it. "
            "BLOCKED is reached for unrelated reasons, so a rule written for "
            "one of them reads this rather than the status."
        ),
    )
    plan_id: UUID | None = Field(
        default=None,
        description="Plan whose dispatch created this task",
    )
    plan_item_id: UUID | None = Field(
        default=None,
        description="Plan item this task implements",
    )
    created_by: NotBlankStr = Field(
        description="Agent name of the task creator",
    )
    requested_by_user_id: NotBlankStr | None = Field(
        default=None,
        description=(
            "User id of the human who filed this task via the API"
            " (distinct from created_by, which is the agent name)."
            " Drives SSE event-stream session ownership: only the"
            " requester (or a CEO) may subscribe to a session keyed"
            " by this task's id. None for agent-internal tasks."
        ),
    )
    assigned_to: NotBlankStr | None = Field(
        default=None,
        description="Agent ID of the assignee",
    )
    reviewers: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Agent IDs of designated reviewers",
    )
    dependencies: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="IDs of tasks this task depends on",
    )
    artifacts_expected: tuple[ExpectedArtifact, ...] = Field(
        default=(),
        description="Artifacts expected to be produced",
    )
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = Field(
        default=(),
        description="Structured acceptance criteria",
    )
    estimated_complexity: Complexity = Field(
        default=Complexity.MEDIUM,
        description="Task complexity estimate",
    )
    stakes: Stakes = Field(
        default=Stakes.NORMAL,
        description=(
            "How consequential this task is, setting the capability rung an"
            " agent must run at to take it (and the red-team threshold for"
            " high/critical stakes)"
        ),
    )
    budget_limit: float = Field(
        default=0.0,
        ge=0.0,
        description="Maximum spend for this task in the configured currency",
    )
    deadline: str | None = Field(
        default=None,
        description="Optional deadline (ISO 8601 string)",
    )
    max_retries: int = Field(
        default=1,
        ge=0,
        description="Max reassignment attempts after failure",
    )
    status: TaskStatus = Field(
        default=TaskStatus.CREATED,
        description="Current lifecycle status",
    )
    parent_task_id: NotBlankStr | None = Field(
        default=None,
        description="Parent task ID when created via delegation",
    )
    delegation_chain: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Ordered agent names of delegators (root first)",
    )
    task_structure: TaskStructure | None = Field(
        default=None,
        description="Classification of subtask relationships (None = not classified)",
    )
    coordination_topology: CoordinationTopology = Field(
        default=CoordinationTopology.AUTO,
        description="Coordination topology for multi-agent execution",
    )
    source: TaskSource | None = Field(
        default=None,
        description="Origin of this task (internal, client, or simulation)",
    )
    middleware_override: tuple[NotBlankStr, ...] | None = Field(
        default=None,
        description=("Per-task middleware chain override (None = use company default)"),
    )
    metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Arbitrary key-value metadata for pipeline tracking and labels",
    )
    hard_ceiling: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Per-run hard real-money ceiling in the configured currency."
            " When the in-loop BudgetChecker observes accumulated_cost >="
            " hard_ceiling it raises RunHardCeilingExceededError and the"
            " engine parks the context via ApprovalGate so the operator"
            " can raise the ceiling and resume. None falls back to the"
            " global budget.run_hard_ceiling setting."
        ),
    )
    hard_token_ceiling: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Per-run hard token ceiling. The money ceiling above is only a"
            " bound where the provider bills per token: against a flat-rate"
            " subscription cost never rises, so it can never fire and the"
            " run's only remaining bound is its turn budget. Tokens are"
            " measured on every provider, so this is the same backstop in the"
            " unit that is always available. When the in-loop BudgetChecker"
            " observes accumulated tokens >= hard_token_ceiling it raises"
            " RunHardTokenCeilingExceededError and the engine parks the"
            " context, so the operator raises the ceiling and resumes with"
            " the workspace intact. None falls back to the global"
            " budget.run_hard_token_ceiling setting."
        ),
    )
    forecast_id: UUID | None = Field(
        default=None,
        description=(
            "Identifier of the pre-flight cost Forecast row this task"
            " was dispatched against. The work-entry adapter sets this"
            " after the operator approves the forecast; the engine"
            " plumbs it onto the parked-context payload when a ceiling"
            " halt occurs so the resume UI can show the original"
            " estimate alongside the accumulated cost."
        ),
    )

    @model_validator(mode="after")
    def _deepcopy_metadata(self) -> Self:
        """Defensive copy so callers cannot mutate the frozen model.

        Returns:
            The instance with ``metadata`` deep-copied so a caller's
            original dict cannot mutate the frozen model.
        """
        object.__setattr__(self, "metadata", copy.deepcopy(self.metadata))
        return self

    @model_validator(mode="after")
    def _validate_deadline_format(self) -> Self:
        """Validate deadline format if present.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``deadline`` is whitespace-only or not a valid
                ISO 8601 string.
        """
        validate_iso8601_deadline(self.deadline)
        return self

    @model_validator(mode="after")
    def _validate_invariants(self) -> Self:
        """Enforce every cross-field rule a coherent task row must satisfy.

        The rules themselves live in ``_task_invariants``, where they can be
        read as one set: each is an argument about which field combinations
        mean something, while this file declares what fields there are.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: From whichever invariant the row violates.
        """
        check_collections(self)
        check_delegation(self)
        check_assignment_consistency(self)
        check_blocked_reason_pairing(self)
        check_plan_linkage(self)
        return self

    def with_transition(self, target: TaskStatus, **overrides: object) -> Task:
        """Create a new Task with a validated status transition.

        Calls :func:`~synthorg.core.task_transitions.validate_transition`
        before producing the new instance, ensuring the state machine is
        enforced.  Uses ``model_validate`` so all validators run on the
        new instance.

        Args:
            target: The desired target status.
            **overrides: Additional field overrides for the new task.

        Returns:
            A new Task with the target status.

        Raises:
            ValueError: If the transition is not valid or overrides
                contain ``status``.
        """
        if "status" in overrides:
            msg = "status override is not allowed; pass transition target explicitly"
            raise ValueError(msg)
        validate_transition(self.status, target)
        payload = self.model_dump()
        # The reason names the park, so leaving BLOCKED ends it. Cleared before
        # the overrides so a writer moving INTO blocked still stamps its own,
        # and cleared here rather than in each writer because the writers are
        # exactly the population that would have to remember.
        if target is not TaskStatus.BLOCKED:
            payload["blocked_reason"] = None
        payload.update(overrides)
        payload["status"] = target
        result = Task.model_validate(payload)
        logger.info(
            TASK_STATUS_CHANGED,
            task_id=self.id,
            from_status=self.status.value,
            to_status=target.value,
        )
        return result
