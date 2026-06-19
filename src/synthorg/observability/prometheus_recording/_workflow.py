# module-kind: code
"""Workflow / blueprint / agent-identity recording."""

from synthorg.observability import get_logger
from synthorg.observability.events.blueprint import BLUEPRINT_INSTANTIATE_OUTCOME
from synthorg.observability.events.metrics import (
    METRICS_SCRAPE_FAILED,
)
from synthorg.observability.prometheus_labels import (
    VALID_BLUEPRINT_OUTCOMES,
    VALID_IDENTITY_CHANGE_TYPES,
    VALID_WORKFLOW_EXECUTION_STATUSES,
    require_label,
    require_non_negative,
    validate_agent_id,
    validate_workflow_definition_id,
)
from synthorg.observability.prometheus_recording._base import (
    _RecordingMetricsBase,
)

logger = get_logger(__name__)


class _WorkflowRecordingMixin(_RecordingMetricsBase):
    """Workflow / blueprint / agent-identity recording."""

    def record_workflow_execution(
        self,
        *,
        workflow_definition_id: str,
        status: str,
        duration_seconds: float,
    ) -> None:
        """Observe a completed workflow execution in the duration histogram.

        ``workflow_definition_id`` must be the stable workflow
        definition id (bounded), NOT a per-run execution id. The
        snapshot validator additionally rejects ids that aren't in
        the active workflow-definition repository so an orphan
        execution can't bloat label cardinality.

        Raises:
            ValueError: If ``workflow_definition_id`` is empty or not in
                the registry snapshot, ``status`` is not a known
                execution status, or ``duration_seconds`` is negative,
                NaN, or infinite.
        """
        if not workflow_definition_id:
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="workflow_execution",
                reason="empty_workflow_definition_id",
            )
            msg = "record_workflow_execution: workflow_definition_id must be non-empty"
            raise ValueError(msg)
        validate_workflow_definition_id(workflow_definition_id)
        require_label(
            "record_workflow_execution: status",
            status,
            VALID_WORKFLOW_EXECUTION_STATUSES,
        )
        require_non_negative(
            "record_workflow_execution: duration_seconds",
            duration_seconds,
        )
        self._workflow_execution_duration.labels(
            workflow_definition_id=workflow_definition_id,
            status=status,
        ).observe(duration_seconds)

    def record_blueprint_instantiation(
        self,
        *,
        outcome: str,
        blueprint_name: str | None = None,
        duration_sec: float | None = None,
    ) -> None:
        """Increment the blueprint-instantiation counter.

        Args:
            outcome: One of :data:`VALID_BLUEPRINT_OUTCOMES`.
            blueprint_name: Blueprint slug (logged, not labelled --
                cardinality is held by the bounded outcome label).
            duration_sec: Wall-clock duration of the instantiation
                (logged, not observed -- this counter is success-rate
                focused; use ``synthorg_workflow_execution_seconds``
                for runtime quantiles).

        Raises:
            ValueError: If *outcome* is not in
                :data:`VALID_BLUEPRINT_OUTCOMES` or *duration_sec* is
                negative.
        """
        require_label("blueprint outcome", outcome, VALID_BLUEPRINT_OUTCOMES)
        if duration_sec is not None:
            require_non_negative(
                "record_blueprint_instantiation: duration_sec",
                duration_sec,
            )
        self._blueprint_instantiations.labels(outcome=outcome).inc()
        logger.info(
            BLUEPRINT_INSTANTIATE_OUTCOME,
            blueprint_name=blueprint_name,
            outcome=outcome,
            duration_sec=duration_sec,
        )

    def record_agent_identity_change(
        self,
        *,
        agent_id: str,
        change_type: str,
    ) -> None:
        """Increment the agent identity change counter.

        ``agent_id`` is validated against the live agent-registry
        snapshot seeded by :meth:`refresh`; unknown ids raise
        ``ValueError`` and are dropped by the metrics-hub safe-record
        decorator. ``change_type`` is bounded by
        :data:`VALID_IDENTITY_CHANGE_TYPES`.

        Raises:
            ValueError: If ``agent_id`` is empty or not in the registry
                snapshot, or ``change_type`` is not a known identity
                change type.
        """
        if not agent_id:
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="agent_identity_change",
                reason="empty_agent_id",
            )
            msg = "record_agent_identity_change: agent_id must be non-empty"
            raise ValueError(msg)
        validate_agent_id(agent_id)
        require_label(
            "record_agent_identity_change: change_type",
            change_type,
            VALID_IDENTITY_CHANGE_TYPES,
        )
        # ``agent_id`` rides as an OpenMetrics exemplar, not a label, so
        # per-agent attribution survives without unbounded label cardinality.
        self._agent_identity_changes.labels(
            change_type=change_type,
        ).inc(exemplar={"agent_id": agent_id})
