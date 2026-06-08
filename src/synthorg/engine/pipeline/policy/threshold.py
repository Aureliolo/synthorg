"""Leaf-threshold routing policy (the shipped safe default).

Classifies the task structure with the existing
:class:`TaskStructureClassifier`. Sequential work whose expected
artifact count does not exceed the configured threshold is a leaf
(single agent); everything else is splittable (coordinator).
"""

from typing import TYPE_CHECKING

from synthorg.core.task_enums import TaskStructure
from synthorg.engine.decomposition.classifier import TaskStructureClassifier
from synthorg.engine.pipeline.models import RoutingVerdict
from synthorg.observability import get_logger
from synthorg.observability.events.pipeline import PIPELINE_ROUTING_DECIDED

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task

logger = get_logger(__name__)


class LeafThresholdRoutingPolicy:
    """Route by task structure plus an expected-artifact threshold.

    Args:
        threshold: Maximum expected-artifact count for a sequential
            task to still be treated as a single-agent leaf.
        classifier: Structure classifier (defaults to the shared
            heuristic classifier).
    """

    __slots__ = ("_classifier", "_threshold")

    def __init__(
        self,
        *,
        threshold: int,
        classifier: TaskStructureClassifier | None = None,
    ) -> None:
        if threshold <= 0:
            msg = f"threshold must be positive, got {threshold}"
            raise ValueError(msg)
        self._threshold = threshold
        self._classifier = (
            classifier if classifier is not None else TaskStructureClassifier()
        )

    async def decide(
        self,
        *,
        task: Task,
        available_agents: tuple[AgentIdentity, ...],
    ) -> RoutingVerdict:
        """Return ``LEAF`` for small sequential work, else ``SPLITTABLE``."""
        del available_agents  # threshold policy is pool-independent
        structure = self._classifier.classify(task)
        artifact_count = len(task.artifacts_expected)
        is_leaf = (
            structure is TaskStructure.SEQUENTIAL and artifact_count <= self._threshold
        )
        verdict = RoutingVerdict.LEAF if is_leaf else RoutingVerdict.SPLITTABLE
        logger.info(
            PIPELINE_ROUTING_DECIDED,
            task_id=task.id,
            policy="leaf-threshold",
            structure=structure.value,
            artifact_count=artifact_count,
            threshold=self._threshold,
            verdict=verdict.value,
        )
        return verdict
