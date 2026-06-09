"""Deterministic heuristic stakes assessor.

Combines three signals into a single :class:`~synthorg.core.task_enums.Stakes`
level: a base level from task complexity, a conservative bump for
critical-priority work, and keyword signals over the title/description
that elevate stakes for consequential or irreversible work. The result
is the highest level any signal produces (fail-safe upward bias).
"""

from synthorg.core.task import Task
from synthorg.core.task_enums import Complexity, Priority, Stakes, compare_stakes
from synthorg.engine.decomposition.models import SubtaskDefinition
from synthorg.engine.stakes.config import StakesAssessmentConfig


def _max_stakes(a: Stakes, b: Stakes) -> Stakes:
    """Return the higher-stakes of two levels."""
    return a if compare_stakes(a, b) >= 0 else b


class DefaultStakesAssessor:
    """Heuristic :class:`~synthorg.engine.stakes.protocol.StakesAssessor`.

    Deterministic and side-effect free. The same inputs always yield the
    same stakes level, which the routing comparison test relies on.

    Args:
        config: The rubric (complexity rules + keyword sets). Defaults to
            the conservative built-in rubric.
    """

    def __init__(self, config: StakesAssessmentConfig | None = None) -> None:
        self._config = config or StakesAssessmentConfig()
        self._base_by_complexity: dict[Complexity, Stakes] = {
            rule.complexity: rule.stakes for rule in self._config.complexity_rules
        }
        self._high_keywords = tuple(
            kw.lower() for kw in self._config.high_stakes_keywords
        )
        self._critical_keywords = tuple(
            kw.lower() for kw in self._config.critical_stakes_keywords
        )

    def assess_subtask(self, subtask: SubtaskDefinition) -> Stakes:
        """Return the stakes level for *subtask*.

        Subtasks carry no priority of their own; priority elevation
        applies only to the task-level path.
        """
        return self._assess(
            title=subtask.title,
            description=subtask.description,
            complexity=subtask.estimated_complexity,
            priority=None,
        )

    def assess_task(self, task: Task) -> Stakes:
        """Return the stakes level for *task* (single-agent / LEAF path)."""
        return self._assess(
            title=task.title,
            description=task.description,
            complexity=task.estimated_complexity,
            priority=task.priority,
        )

    def _assess(
        self,
        *,
        title: str,
        description: str,
        complexity: Complexity,
        priority: Priority | None,
    ) -> Stakes:
        """Combine complexity, priority, and keyword signals (upward bias).

        Returns:
            The resolved :class:`Stakes` after applying the upward
            bias from complexity, priority, and keyword scans.
        """
        # Unknown complexity biases upward (fail-safe) rather than to LOW.
        stakes = self._base_by_complexity.get(complexity, Stakes.HIGH)

        if priority is Priority.CRITICAL and self._config.elevate_on_critical_priority:
            stakes = _max_stakes(stakes, Stakes.HIGH)

        text = f"{title}\n{description}".lower()
        if any(kw in text for kw in self._critical_keywords):
            stakes = _max_stakes(stakes, Stakes.CRITICAL)
        elif any(kw in text for kw in self._high_keywords):
            stakes = _max_stakes(stakes, Stakes.HIGH)

        return stakes
