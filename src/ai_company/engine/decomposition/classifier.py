"""Task structure classifier.

Infers ``TaskStructure`` from task properties using heuristics
based on DESIGN_SPEC Section 6.9 and Kim et al. research.
"""

import re
from typing import TYPE_CHECKING

from ai_company.core.enums import TaskStructure
from ai_company.observability import get_logger
from ai_company.observability.events.decomposition import (
    DECOMPOSITION_STRUCTURE_CLASSIFIED,
)

if TYPE_CHECKING:
    from ai_company.core.task import Task

logger = get_logger(__name__)

# Language patterns indicating sequential structure
_SEQUENTIAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bthen\b", re.IGNORECASE),
    re.compile(r"\bafter\b", re.IGNORECASE),
    re.compile(r"\bbefore\b", re.IGNORECASE),
    re.compile(r"\bfirst\b", re.IGNORECASE),
    re.compile(r"\bnext\b", re.IGNORECASE),
    re.compile(r"\bfinally\b", re.IGNORECASE),
    re.compile(r"\bstep\s+\d+", re.IGNORECASE),
    re.compile(r"\bphase\s+\d+", re.IGNORECASE),
)

# Language patterns indicating parallel structure
_PARALLEL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bindependently\b", re.IGNORECASE),
    re.compile(r"\bin\s+parallel\b", re.IGNORECASE),
    re.compile(r"\bconcurrently\b", re.IGNORECASE),
    re.compile(r"\bsimultaneously\b", re.IGNORECASE),
    re.compile(r"\bseparately\b", re.IGNORECASE),
)

# Maximum tool count threshold for sequential classification
_SEQUENTIAL_TOOL_THRESHOLD = 4


class TaskStructureClassifier:
    """Classifies task structure based on heuristic analysis.

    Examines task description, acceptance criteria, and artifact types
    to determine whether subtasks are sequential, parallel, or mixed.
    Defaults to sequential (safest per research) when uncertain.
    """

    def classify(self, task: Task) -> TaskStructure:
        """Classify the task structure.

        Args:
            task: The task to classify.

        Returns:
            The inferred task structure.
        """
        if task.task_structure is not None:
            logger.debug(
                DECOMPOSITION_STRUCTURE_CLASSIFIED,
                task_id=task.id,
                structure=task.task_structure.value,
                source="explicit",
            )
            return task.task_structure

        sequential_score = self._score_sequential(task)
        parallel_score = self._score_parallel(task)

        if sequential_score > 0 and parallel_score > 0:
            structure = TaskStructure.MIXED
        elif parallel_score > sequential_score:
            structure = TaskStructure.PARALLEL
        elif sequential_score > 0:
            structure = TaskStructure.SEQUENTIAL
        else:
            # Default fallback: sequential (safest per research)
            structure = TaskStructure.SEQUENTIAL

        logger.debug(
            DECOMPOSITION_STRUCTURE_CLASSIFIED,
            task_id=task.id,
            structure=structure.value,
            source="heuristic",
            sequential_score=sequential_score,
            parallel_score=parallel_score,
        )
        return structure

    def _score_sequential(self, task: Task) -> int:
        """Count sequential signals in the task."""
        score = 0
        text = f"{task.title} {task.description}"

        for pattern in _SEQUENTIAL_PATTERNS:
            if pattern.search(text):
                score += 1

        # Check acceptance criteria for step-like language
        for criterion in task.acceptance_criteria:
            for pattern in _SEQUENTIAL_PATTERNS:
                if pattern.search(criterion.description):
                    score += 1

        # Few tools suggest sequential workflow
        if len(task.artifacts_expected) <= _SEQUENTIAL_TOOL_THRESHOLD:
            score += 1

        # Ordered dependencies suggest sequential structure
        if task.dependencies:
            score += 1

        return score

    def _score_parallel(self, task: Task) -> int:
        """Count parallel signals in the task."""
        score = 0
        text = f"{task.title} {task.description}"

        for pattern in _PARALLEL_PATTERNS:
            if pattern.search(text):
                score += 1

        # Check acceptance criteria for parallel language
        for criterion in task.acceptance_criteria:
            for pattern in _PARALLEL_PATTERNS:
                if pattern.search(criterion.description):
                    score += 1

        # Multiple distinct artifact types suggest parallel work
        if len(task.artifacts_expected) > _SEQUENTIAL_TOOL_THRESHOLD:
            score += 1

        # No dependencies suggest potential parallelism
        if not task.dependencies:
            score += 1

        return score
