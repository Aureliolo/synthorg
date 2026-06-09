"""Sprint lifecycle state machine and Sprint domain model.

Defines the five sprint statuses from the Engine design page and
the strictly-linear lifecycle transitions.  The ``Sprint`` model
tracks tasks, story points, and dates across the sprint lifecycle.
"""

from collections import Counter
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.state_machine import StateMachine
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import (
    SPRINT_LIFECYCLE_TRANSITION,
    SPRINT_LIFECYCLE_TRANSITION_CONFIG_ERROR,
    SPRINT_LIFECYCLE_TRANSITION_INVALID,
)

logger = get_logger(__name__)


class SprintStatus(StrEnum):
    """Sprint lifecycle status.

    Transitions are strictly linear with no backward moves::

        PLANNING -> ACTIVE -> IN_REVIEW -> RETROSPECTIVE -> COMPLETED

    Members:
        PLANNING: Sprint backlog is being assembled.
        ACTIVE: Sprint is executing.
        IN_REVIEW: Sprint work is being reviewed.
        RETROSPECTIVE: Team is conducting the retrospective.
        COMPLETED: Sprint is finished.
    """

    PLANNING = "planning"
    ACTIVE = "active"
    IN_REVIEW = "in_review"
    RETROSPECTIVE = "retrospective"
    COMPLETED = "completed"


# -- Sprint lifecycle transitions -------------------------------------------

VALID_SPRINT_TRANSITIONS: MappingProxyType[SprintStatus, frozenset[SprintStatus]] = (
    MappingProxyType(
        {
            SprintStatus.PLANNING: frozenset({SprintStatus.ACTIVE}),
            SprintStatus.ACTIVE: frozenset({SprintStatus.IN_REVIEW}),
            SprintStatus.IN_REVIEW: frozenset({SprintStatus.RETROSPECTIVE}),
            SprintStatus.RETROSPECTIVE: frozenset({SprintStatus.COMPLETED}),
            SprintStatus.COMPLETED: frozenset(),  # terminal
        }
    )
)

_SPRINT_MACHINE: Final[StateMachine[SprintStatus]] = StateMachine(
    VALID_SPRINT_TRANSITIONS,
    name="sprint_lifecycle",
    display_label="sprint",
    invalid_event=SPRINT_LIFECYCLE_TRANSITION_INVALID,
    config_event=SPRINT_LIFECYCLE_TRANSITION_CONFIG_ERROR,
    transition_event=SPRINT_LIFECYCLE_TRANSITION,
    all_states=SprintStatus,
)


def validate_sprint_transition(
    current: SprintStatus,
    target: SprintStatus,
) -> None:
    """Validate that a sprint lifecycle transition is allowed.

    The success INFO log (``SPRINT_LIFECYCLE_TRANSITION``) is emitted
    by :meth:`StateMachine.validate` via its ``transition_event``
    wiring; this thin wrapper just forwards the call so callers
    don't need to know about the underlying machine.

    Args:
        current: The current sprint status.
        target: The desired target status.

    Raises:
        ValueError: If the transition is not allowed.
    """
    _SPRINT_MACHINE.validate(current, target)


# -- Sprint model -----------------------------------------------------------


class Sprint(BaseModel):
    """A time-boxed work cycle in the Agile sprints workflow.

    Attributes:
        id: Unique sprint identifier.
        name: Sprint display name.
        goal: Sprint goal statement.
        status: Current lifecycle status.
        sprint_number: Sequential sprint number (1-based).
        duration_days: Planned sprint duration in days.
        start_date: Sprint start date (ISO 8601, required when ACTIVE+).
        end_date: Sprint end date (ISO 8601, required when COMPLETED).
        task_ids: IDs of tasks in the sprint backlog.
        completed_task_ids: IDs of completed tasks (subset of task_ids).
        story_points_committed: Total story points planned.
        story_points_completed: Story points delivered.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Unique sprint identifier")
    name: NotBlankStr = Field(description="Sprint display name")
    goal: str = Field(default="", description="Sprint goal statement")
    status: SprintStatus = Field(
        default=SprintStatus.PLANNING,
        description="Current lifecycle status",
    )
    sprint_number: int = Field(
        ge=1,
        le=100_000,
        description="Sequential sprint number",
    )
    duration_days: int = Field(
        default=14,
        ge=1,
        le=90,
        description="Planned sprint duration in days",
    )
    start_date: str | None = Field(
        default=None,
        description="Sprint start date (ISO 8601)",
    )
    end_date: str | None = Field(
        default=None,
        description="Sprint end date (ISO 8601)",
    )
    task_ids: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Task IDs in the sprint backlog",
    )
    completed_task_ids: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Completed task IDs (subset of task_ids)",
    )
    story_points_committed: float = Field(
        default=0.0,
        ge=0.0,
        le=100_000.0,
        description="Total story points planned",
    )
    story_points_completed: float = Field(
        default=0.0,
        ge=0.0,
        le=100_000.0,
        description="Story points delivered",
    )

    @model_validator(mode="after")
    def _validate_date_formats(self) -> Self:
        """Validate ISO 8601 date format when present.

        Returns:
            ``self`` unchanged after format validation passes.

        Raises:
            ValueError: When a date field is whitespace-only or not a
                valid ISO 8601 string.
        """
        for field_name in ("start_date", "end_date"):
            value = getattr(self, field_name)
            if value is not None:
                if not value.strip():
                    msg = f"{field_name} must not be whitespace-only"
                    raise ValueError(msg)
                try:
                    datetime.fromisoformat(value)
                except ValueError as exc:
                    msg = f"{field_name} must be a valid ISO 8601 string, got {value!r}"
                    raise ValueError(msg) from exc
        return self

    @model_validator(mode="after")
    def _validate_date_ordering(self) -> Self:
        """Ensure end_date >= start_date when both are present.

        Returns:
            ``self`` unchanged when ordering is valid.

        Raises:
            ValueError: When ``end_date`` precedes ``start_date``.
        """
        if self.start_date is not None and self.end_date is not None:
            start = datetime.fromisoformat(self.start_date)
            end = datetime.fromisoformat(self.end_date)
            if end < start:
                msg = (
                    f"end_date ({self.end_date}) must be >= "
                    f"start_date ({self.start_date})"
                )
                raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_task_collections(self) -> Self:
        """Validate task ID uniqueness and subset constraint.

        Returns:
            ``self`` unchanged when both collections are well-formed.

        Raises:
            ValueError: When ``task_ids`` or ``completed_task_ids``
                contain duplicates, or when ``completed_task_ids`` is
                not a subset of ``task_ids``.
        """
        if len(self.task_ids) != len(set(self.task_ids)):
            dupes = sorted(t for t, c in Counter(self.task_ids).items() if c > 1)
            msg = f"Duplicate entries in task_ids: {dupes}"
            raise ValueError(msg)
        if len(self.completed_task_ids) != len(set(self.completed_task_ids)):
            dupes = sorted(
                t for t, c in Counter(self.completed_task_ids).items() if c > 1
            )
            msg = f"Duplicate entries in completed_task_ids: {dupes}"
            raise ValueError(msg)
        task_set = set(self.task_ids)
        extra = set(self.completed_task_ids) - task_set
        if extra:
            msg = f"completed_task_ids contains IDs not in task_ids: {sorted(extra)}"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_story_points(self) -> Self:
        """story_points_completed must not exceed committed.

        Returns:
            ``self`` unchanged when story-point totals are consistent.

        Raises:
            ValueError: When completed story points exceed the
                committed total.
        """
        if self.story_points_completed > self.story_points_committed:
            msg = (
                f"story_points_completed ({self.story_points_completed}) "
                f"exceeds story_points_committed "
                f"({self.story_points_committed})"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_status_date_requirements(self) -> Self:
        """Enforce date requirements based on status.

        - ACTIVE and later require ``start_date``.
        - COMPLETED requires ``end_date``.

        Returns:
            ``self`` unchanged when the required dates are present.

        Raises:
            ValueError: When ``start_date`` is missing for ACTIVE+ or
                ``end_date`` is missing for COMPLETED.
        """
        requires_start = {
            SprintStatus.ACTIVE,
            SprintStatus.IN_REVIEW,
            SprintStatus.RETROSPECTIVE,
            SprintStatus.COMPLETED,
        }
        if self.status in requires_start and self.start_date is None:
            msg = f"start_date is required when status is {self.status.value!r}"
            raise ValueError(msg)
        if self.status is SprintStatus.COMPLETED and self.end_date is None:
            msg = "end_date is required when status is 'completed'"
            raise ValueError(msg)
        return self

    _TRANSITION_ALLOWED_OVERRIDES: frozenset[str] = frozenset(
        {"start_date", "end_date"},
    )

    def with_transition(self, target: SprintStatus, **overrides: object) -> Sprint:
        """Create a new Sprint with a validated lifecycle transition.

        Only ``start_date`` and ``end_date`` may be passed as overrides;
        all other fields are carried forward from the current sprint.

        Args:
            target: The desired target status.
            **overrides: Additional field overrides (start_date, end_date).

        Returns:
            A new Sprint with the target status.

        Raises:
            ValueError: If the transition is invalid, overrides contain
                ``status``, or overrides contain disallowed fields.
        """
        if "status" in overrides:
            msg = "status override is not allowed; pass transition target explicitly"
            logger.warning(
                SPRINT_LIFECYCLE_TRANSITION_INVALID,
                sprint_id=self.id,
                reason="status_in_overrides",
            )
            raise ValueError(msg)
        disallowed = set(overrides) - self._TRANSITION_ALLOWED_OVERRIDES
        if disallowed:
            msg = f"with_transition does not allow overriding: {sorted(disallowed)}"
            logger.warning(
                SPRINT_LIFECYCLE_TRANSITION_INVALID,
                sprint_id=self.id,
                reason="disallowed_overrides",
                fields=sorted(disallowed),
            )
            raise ValueError(msg)
        validate_sprint_transition(self.status, target)
        return self.model_copy(update={**overrides, "status": target})
