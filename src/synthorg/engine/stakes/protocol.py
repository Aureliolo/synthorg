"""Pluggable stakes-assessment protocol."""

from typing import Protocol, runtime_checkable

from synthorg.core.task import Task
from synthorg.core.task_enums import Stakes
from synthorg.engine.decomposition.models import SubtaskDefinition


@runtime_checkable
class StakesAssessor(Protocol):
    """Classifies how consequential a subtask or task is.

    Implementations must be deterministic and side-effect free: the
    routing layer treats assessment as a pure function so the
    cost/quality comparison test is reproducible.
    """

    def assess_subtask(self, subtask: SubtaskDefinition) -> Stakes:
        """Return the stakes level for *subtask*."""
        ...

    def assess_task(self, task: Task) -> Stakes:
        """Return the stakes level for *task* (single-agent / LEAF path)."""
        ...
