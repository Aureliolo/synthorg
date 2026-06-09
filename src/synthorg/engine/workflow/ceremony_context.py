"""Ceremony evaluation context -- rich context passed to strategies.

Provides the ``CeremonyEvalContext`` frozen dataclass that bundles all
information a ``CeremonySchedulingStrategy`` might need to evaluate
whether a ceremony should fire or a sprint should auto-transition.

Uses a frozen dataclass (not Pydantic) for performance -- this type
is created on every task-completion event.
"""

import copy
import math
from dataclasses import dataclass

from synthorg.engine.workflow.sprint_velocity import VelocityRecord
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import (
    SPRINT_CEREMONY_EVAL_CONTEXT_INVALID,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CeremonyEvalContext:
    """Rich context passed to ceremony scheduling strategies.

    All fields represent the state at evaluation time (after the event
    that triggered the evaluation).

    Attributes:
        completions_since_last_trigger: Task completions since this
            ceremony's trigger last fired.  Set to 0 in global
            (non-ceremony-specific) contexts.
        total_completions_this_sprint: Total task completions in the
            current sprint.
        total_tasks_in_sprint: Total tasks in the sprint backlog.
        elapsed_seconds: Monotonic seconds since sprint activation.
        budget_consumed_fraction: Budget consumed as a fraction of
            total (0.0--1.0).  ``0.0`` when budget tracking is not
            active.
        budget_remaining: Remaining budget in base currency.  ``0.0``
            when budget tracking is not active.
        velocity_history: Recent velocity records for rolling average
            calculations (oldest first).
        external_events: Pending external event names that have been
            received but not yet processed.
        sprint_percentage_complete: Fraction of tasks complete by
            task count (0.0--1.0).
        story_points_completed: Story points delivered so far.
        story_points_committed: Story points planned for the sprint.
    """

    completions_since_last_trigger: int
    total_completions_this_sprint: int
    total_tasks_in_sprint: int
    elapsed_seconds: float
    budget_consumed_fraction: float
    budget_remaining: float
    velocity_history: tuple[VelocityRecord, ...]
    external_events: tuple[str, ...]
    sprint_percentage_complete: float
    story_points_completed: float
    story_points_committed: float

    def __post_init__(self) -> None:
        """Validate field constraints and normalize collections."""
        self._normalize_collections()
        self._validate_fields()

    def _normalize_collections(self) -> None:
        """Defensively copy incoming tuples to enforce immutability."""
        object.__setattr__(
            self,
            "velocity_history",
            tuple(copy.deepcopy(self.velocity_history)),
        )
        object.__setattr__(
            self,
            "external_events",
            tuple(self.external_events),
        )

    def _validate_fields(self) -> None:
        """Validate all field constraints."""
        self._check_non_negative_int(
            "completions_since_last_trigger",
            self.completions_since_last_trigger,
        )
        self._check_non_negative_int(
            "total_completions_this_sprint",
            self.total_completions_this_sprint,
        )
        self._check_non_negative_int(
            "total_tasks_in_sprint",
            self.total_tasks_in_sprint,
        )
        self._check_non_negative_float(
            "elapsed_seconds",
            self.elapsed_seconds,
        )
        self._check_fraction(
            "budget_consumed_fraction",
            self.budget_consumed_fraction,
        )
        self._check_non_negative_float(
            "budget_remaining",
            self.budget_remaining,
        )
        self._check_fraction(
            "sprint_percentage_complete",
            self.sprint_percentage_complete,
        )
        self._check_non_negative_float(
            "story_points_completed",
            self.story_points_completed,
        )
        self._check_non_negative_float(
            "story_points_committed",
            self.story_points_committed,
        )

    @staticmethod
    def _check_non_negative_int(name: str, value: int) -> None:
        if value < 0:
            msg = f"{name} must be >= 0"
            logger.warning(
                SPRINT_CEREMONY_EVAL_CONTEXT_INVALID,
                field=name,
                value=value,
            )
            raise ValueError(msg)

    @staticmethod
    def _check_non_negative_float(name: str, value: float) -> None:
        if math.isnan(value) or math.isinf(value) or value < 0.0:
            msg = f"{name} must be >= 0.0 and finite"
            logger.warning(
                SPRINT_CEREMONY_EVAL_CONTEXT_INVALID,
                field=name,
                value=value,
            )
            raise ValueError(msg)

    @staticmethod
    def _check_fraction(name: str, value: float) -> None:
        if math.isnan(value) or math.isinf(value):
            msg = f"{name} must be finite"
            logger.warning(
                SPRINT_CEREMONY_EVAL_CONTEXT_INVALID,
                field=name,
                value=value,
            )
            raise ValueError(msg)
        if not 0.0 <= value <= 1.0:
            msg = f"{name} must be in [0.0, 1.0]"
            logger.warning(
                SPRINT_CEREMONY_EVAL_CONTEXT_INVALID,
                field=name,
                value=value,
            )
            raise ValueError(msg)
