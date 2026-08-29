# module-kind: code
"""Wiring for the task-activity observer the dashboard's live feed reads.

Its own module because it is the one wiring function on the startup spine that
carries a whole graph with it: a build/test oracle, a performance tracker, an
artifact reader, a quality lookup and an agent resolver, each with its own local
import. Left inline it is most of the file it sits in.

Each collaborator is built by its own function here, named for what it reads
rather than for the argument it fills. That keeps the wiring function a list of
what the observer needs, and keeps each reader's local imports next to the one
closure that uses them: the heavy engine and HR modules are imported inside the
function that needs them, which is what keeps this module's cold import light.
"""

from collections.abc import Sequence

from synthorg.api.state import AppState
from synthorg.api.task_activity_observer import (
    AgentRefResolver,
    ArtifactLister,
    MetricQualityResolver,
    OracleBlockResolver,
    TaskActivityObserver,
)
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP, API_SERVICE_AUTO_WIRED
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)


def _artifact_lister(persistence: PersistenceBackend) -> ArtifactLister:
    """Build the observer's read of a task's artifacts.

    Returns:
        A callable answering one task's artifacts.
    """
    from synthorg.core.artifact import Artifact  # noqa: PLC0415
    from synthorg.persistence.artifact_protocol import (  # noqa: PLC0415
        ArtifactFilterSpec,
    )

    async def _list_artifacts(task_id: str) -> Sequence[Artifact]:
        return await persistence.artifacts.query(ArtifactFilterSpec(task_id=task_id))

    return _list_artifacts


def _oracle_block_resolver(
    persistence: PersistenceBackend, app_state: AppState
) -> OracleBlockResolver:
    """Build the observer's read of whether the build/test oracle blocks a task.

    Returns:
        A callable answering whether one task's run outcome blocks completion.
    """
    from synthorg.core.task import Task  # noqa: PLC0415
    from synthorg.engine.completion_oracle.evaluator import (  # noqa: PLC0415
        BuildTestOracle,
    )
    from synthorg.engine.workspace.state import (  # noqa: PLC0415
        agent_workspace_root_of,
    )

    # The same workspace the enforcing gate reads, so a badge and the gate
    # cannot disagree about a skeleton whose suite fails by design.
    build_test_oracle = BuildTestOracle(
        workspace_root=agent_workspace_root_of(app_state),
        plans=persistence.plans,
    )

    async def _oracle_block_for(task: Task) -> bool:
        # Re-source the run outcome for the live feed the same way the approvals
        # queue does, so a code task whose tests failed / never ran shows FAILED
        # on both surfaces rather than only in the queue.
        evaluation = await build_test_oracle.verdict_for(
            task, records=persistence.code_execution_records
        )
        return evaluation.blocks_completion

    return _oracle_block_for


def _quality_resolver(persistence: PersistenceBackend) -> MetricQualityResolver:
    """Build the observer's read of a finished task's review quality.

    Returns:
        A callable answering one task's quality score, or ``None``.
    """
    from synthorg.core.task import Task  # noqa: PLC0415
    from synthorg.hr.performance.oracle_quality import (  # noqa: PLC0415
        quality_score_for,
    )
    from synthorg.persistence.completion_oracle_report_protocol import (  # noqa: PLC0415
        CompletionOracleReportFilterSpec,
    )

    async def _resolve_quality(task: Task) -> float | None:
        # Newest-first, limit 1: a deliverable re-opened and re-reviewed
        # archives a row per review, and the ledger records the verdict the
        # run ended on. ``None`` when nothing reviewed it.
        records = await persistence.completion_oracle_reports.query(
            CompletionOracleReportFilterSpec(task_id=str(task.id)),
            limit=1,
        )
        if not records:
            return None
        return quality_score_for(records[0].report)

    return _resolve_quality


def _agent_resolver(app_state: AppState) -> AgentRefResolver:
    """Build the observer's read of an assignee's display identity.

    Returns:
        A callable answering one agent's display reference, or ``None``.
    """
    from synthorg.api.task_activity_observer import ActivityAgentRef  # noqa: PLC0415
    from synthorg.core.normalization import (  # noqa: PLC0415
        normalize_ascii_lowercase,
    )

    async def _resolve_agent(agent_id: str) -> ActivityAgentRef | None:
        # Resolve the assignee's display identity at event time so a live
        # config change (renamed agent, moved department) is reflected without
        # reconstructing the observer. ``get_agents`` is config-resolver cached.
        agents = await config_resolver_of(app_state).get_agents()
        target = normalize_ascii_lowercase(agent_id)
        for agent in agents:
            if normalize_ascii_lowercase(str(agent.id)) == target:
                return ActivityAgentRef(
                    name=agent.name, role=agent.role, department=agent.department
                )
        return None

    return _resolve_agent


def wire_task_activity_observer(
    task_engine: object,
    persistence: PersistenceBackend,
    app_state: AppState,
    channels_plugin: object,
) -> None:
    """Register the task-activity observer on ``task_engine`` once.

    Publishes every persisted task transition to the ``tasks`` WS channel and
    records a terminal run's outcome as a task metric, so the dashboard Live
    Activity feed and org-health derive from real execution. Logs and skips
    when a prerequisite (task engine / channels plugin / performance tracker)
    is absent; the feed then simply lacks live task rows rather than the boot
    failing. Idempotent: never double-registers.
    """
    from litestar.channels import ChannelsPlugin  # noqa: PLC0415

    from synthorg.hr.state import HrStateSlice  # noqa: PLC0415

    if task_engine is None or not isinstance(channels_plugin, ChannelsPlugin):
        logger.warning(
            API_APP_STARTUP,
            component="task_activity_observer",
            note=(
                "task engine or channels plugin absent; skipping observer "
                "wiring (dashboard live-activity feed lacks live task rows)"
            ),
        )
        return
    tracker = app_state.slice(HrStateSlice).performance_tracker
    if tracker is None:
        logger.warning(
            API_APP_STARTUP,
            component="task_activity_observer",
            note=(
                "performance tracker absent; skipping observer wiring "
                "(org health and live-activity task rows lack real data)"
            ),
        )
        return
    if task_engine.has_observer_type(TaskActivityObserver):  # type: ignore[attr-defined]
        return
    observer = TaskActivityObserver(
        # ``wait_published`` (direct backend delivery), NOT ``publish`` (background
        # pub-queue): a transition can publish while the channels plugin is being
        # torn down, and the plugin's pub worker races its own shutdown; the
        # direct path has no such worker. See TaskActivityObserver.PublishFn.
        publish=channels_plugin.wait_published,
        list_artifacts=_artifact_lister(persistence),
        record_metric=tracker.record_task_metric,
        resolve_agent=_agent_resolver(app_state),
        oracle_block_for=_oracle_block_resolver(persistence, app_state),
        resolve_quality=_quality_resolver(persistence),
    )
    task_engine.register_observer(observer)  # type: ignore[attr-defined]
    logger.info(API_SERVICE_AUTO_WIRED, service="task_activity_observer")


__all__ = ["wire_task_activity_observer"]
