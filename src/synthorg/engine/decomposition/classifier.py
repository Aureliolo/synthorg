"""Task structure classifier.

Infers ``TaskStructure`` from task properties using heuristics
based on the Engine design page.
"""

import re
from typing import TYPE_CHECKING

from synthorg.core.enums import TaskStructure
from synthorg.observability import get_logger
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_STRUCTURE_CLASSIFIED,
)

if TYPE_CHECKING:
    from synthorg.core.task import Task

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

# Artifact count at or below which sequential is favoured
_ARTIFACT_COUNT_THRESHOLD = 4


class TaskStructureClassifier:
    """Classifies task structure based on heuristic analysis.

    Examines task description, acceptance criteria, and artifact types
    to determine whether subtasks are sequential, parallel, or mixed.
    Defaults to sequential (safest per research) when uncertain.

    The MIXED classification is triggered only when **language patterns**
    from both sequential and parallel categories appear. Structural
    signals (artifact count, dependency presence) act as tiebreakers
    but do not influence MIXED detection.
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

        seq_language = self._score_language_sequential(task)
        par_language = self._score_language_parallel(task)
        seq_structural = self._score_structural_sequential(task)
        par_structural = self._score_structural_parallel(task)

        sequential_score = seq_language + seq_structural
        parallel_score = par_language + par_structural

        # MIXED only when both language categories have signals
        if seq_language > 0 and par_language > 0:
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

    def _score_language_sequential(self, task: Task) -> int:
        """Count sequential language pattern matches.

        Args:
            task: The task to analyse.

        Returns:
            Number of sequential language signals found.
        """
        score = 0
        text = f"{task.title} {task.description}"

        for pattern in _SEQUENTIAL_PATTERNS:
            if pattern.search(text):
                score += 1

        for criterion in task.acceptance_criteria:
            for pattern in _SEQUENTIAL_PATTERNS:
                if pattern.search(criterion.description):
                    score += 1

        return score

    def _score_language_parallel(self, task: Task) -> int:
        """Count parallel language pattern matches.

        Args:
            task: The task to analyse.

        Returns:
            Number of parallel language signals found.
        """
        score = 0
        text = f"{task.title} {task.description}"

        for pattern in _PARALLEL_PATTERNS:
            if pattern.search(text):
                score += 1

        for criterion in task.acceptance_criteria:
            for pattern in _PARALLEL_PATTERNS:
                if pattern.search(criterion.description):
                    score += 1

        return score

    def _score_structural_sequential(self, task: Task) -> int:
        """Count structural signals favouring sequential execution.

        Args:
            task: The task to analyse.

        Returns:
            Number of structural sequential signals found.
        """
        score = 0

        # Few artifacts suggest sequential workflow
        if len(task.artifacts_expected) <= _ARTIFACT_COUNT_THRESHOLD:
            score += 1

        # Ordered dependencies suggest sequential structure
        if task.dependencies:
            score += 1

        return score

    def _score_structural_parallel(self, task: Task) -> int:
        """Count structural signals favouring parallel execution.

        Args:
            task: The task to analyse.

        Returns:
            Number of structural parallel signals found.
        """
        score = 0

        # Many expected artifacts suggest parallel work
        if len(task.artifacts_expected) > _ARTIFACT_COUNT_THRESHOLD:
            score += 1

        # No dependencies suggest potential parallelism
        if not task.dependencies:
            score += 1

        return score
