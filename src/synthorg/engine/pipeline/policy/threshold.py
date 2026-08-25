"""Leaf-threshold routing policy (the shipped safe default).

Classifies the task structure with the existing
:class:`TaskStructureClassifier`. Sequential work whose expected
artifact count does not exceed the configured threshold is a leaf
(single agent); everything else is splittable (coordinator).

The threshold is read per decision rather than captured at wiring time. It
answers "is this objective a team's work", which an operator revises against
the objectives they are actually filing, and a value that applied only from the
next restart would be a knob they could turn with no effect until then.
"""

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStructure
from synthorg.engine.decomposition.classifier import TaskStructureClassifier
from synthorg.engine.pipeline.models import RoutingVerdict
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.pipeline import PIPELINE_ROUTING_DECIDED
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)


class LeafThresholdRoutingPolicy:
    """Route by task structure plus an expected-artifact threshold.

    Args:
        threshold: Maximum expected-artifact count for a sequential
            task to still be treated as a single-agent leaf. In force when
            there is no resolver or the setting cannot answer, so a harness
            with no settings backend still routes.
        classifier: Structure classifier (defaults to the shared
            heuristic classifier).
        config_resolver: The live settings resolver, or ``None`` in a harness.
    """

    __slots__ = ("_classifier", "_config_resolver", "_threshold")

    def __init__(
        self,
        *,
        threshold: int,
        classifier: TaskStructureClassifier | None = None,
        config_resolver: ConfigResolverProtocol | None = None,
    ) -> None:
        if threshold <= 0:
            msg = f"threshold must be positive, got {threshold}"
            raise ValueError(msg)
        self._threshold = threshold
        self._classifier = (
            classifier if classifier is not None else TaskStructureClassifier()
        )
        self._config_resolver = config_resolver

    async def _live_threshold(self) -> int:
        """Read the threshold in force for this decision.

        Returns:
            The operator's current value, else the one this policy was built
            with. A threshold nobody can read still routes rather than
            refusing every objective.
        """
        if self._config_resolver is None:
            return self._threshold
        try:
            return await self._config_resolver.get_int(
                "coordination", "leaf_subtask_threshold"
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort settings read; the wiring
            # value still bounds the decision, so routing is unaffected in
            # kind and only the operator's latest revision is missed
            reraise_critical(exc)
            logger.warning(
                PIPELINE_ROUTING_DECIDED,
                policy="leaf-threshold",
                note="threshold unreadable; using the wired value",
                threshold=self._threshold,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return self._threshold

    async def decide(
        self,
        *,
        task: Task,
        available_agents: tuple[AgentIdentity, ...],
    ) -> RoutingVerdict:
        """Return ``LEAF`` for small sequential work, else ``SPLITTABLE``."""
        del available_agents  # threshold policy is pool-independent
        threshold = await self._live_threshold()
        structure = self._classifier.classify(task)
        artifact_count = len(task.artifacts_expected)
        is_leaf = structure is TaskStructure.SEQUENTIAL and artifact_count <= threshold
        verdict = RoutingVerdict.LEAF if is_leaf else RoutingVerdict.SPLITTABLE
        logger.info(
            PIPELINE_ROUTING_DECIDED,
            task_id=task.id,
            policy="leaf-threshold",
            structure=structure.value,
            artifact_count=artifact_count,
            threshold=threshold,
            verdict=verdict.value,
        )
        return verdict
