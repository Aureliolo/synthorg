# module-kind: code
"""Construction-phase feature wiring for the composition root.

At build time -- before the Litestar app exists and before persistence
connects -- ``create_app`` constructs the services that do not need a
connected backend and bundles them into a :class:`ConstructionDeps`. The
composition root then calls :func:`run_construction_wiring`, which hands the
bundle to every discovered feature's ``construction_wirer`` so each feature
populates its OWN state slice from the shared services.

This is the construction-phase sibling of the startup-phase
``FeatureLifecycleRunner``: construction wiring runs synchronously at build
time (its results feed construction-time controller predicates), whereas
lifecycle hooks run asynchronously once the lifespan opens. A feature whose
construction wiring reads another feature's slice declares a ``depends_on``
edge so dependency-ordered discovery runs the producer first (e.g.
``communication`` reads ``settings``'s config resolver).
"""

from dataclasses import dataclass

from synthorg._core.features import discover_features
from synthorg.api.auth.service import AuthService
from synthorg.api.auto_wire import MeetingWireResult, Phase1Result
from synthorg.api.cursor import CursorSecret
from synthorg.api.integrations_wiring import IntegrationsBundle
from synthorg.api.state import AppState
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.budget.coordination_store import CoordinationMetricsStore
from synthorg.client.models import ClientRequest
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.communication.delegation.record_store import DelegationRecordStore
from synthorg.communication.event_stream.interrupt import InterruptStore
from synthorg.communication.event_stream.stream import EventStreamHub
from synthorg.config.schema import RootConfig
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.pipeline.entry.protocol import WorkEntryAdapter
from synthorg.engine.pipeline.entry.task_board_adapter import TaskBoardEntryAdapter
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.training.service import TrainingService
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.artifact_storage import ArtifactStorageBackend
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.security.audit import AuditLog
from synthorg.security.autonomy.protocol import AutonomyChangeStrategy
from synthorg.security.trust.service import TrustService
from synthorg.settings.service import SettingsService
from synthorg.tools.invocation_tracker import ToolInvocationTracker

logger = get_logger(__name__)


@dataclass(frozen=True)
class ConstructionDeps:
    """The construction-time service bundle handed to every feature wirer.

    Built once by ``create_app`` from the construction-phase auto-wiring
    (the persistence-independent services) and passed to
    :func:`run_construction_wiring`. Each feature's ``construction_wirer``
    plucks the fields it needs and swaps its populated slice in. The three
    auto-wire result bundles (``phase1`` / ``meeting_wire`` / ``integrations``)
    are nested so a wirer reads them by provenance.

    A frozen dataclass (not a Pydantic model) so it carries the plain
    service references -- including the ``Phase1Result`` / ``MeetingWireResult``
    NamedTuples -- without Pydantic introspecting their nested forward refs;
    ``frozen=True`` keeps the bundle immutable between wirers.
    """

    effective_config: RootConfig
    phase1: Phase1Result
    meeting_wire: MeetingWireResult
    integrations: IntegrationsBundle
    approval_store: ApprovalStoreProtocol
    autonomy_change_strategy: AutonomyChangeStrategy
    notification_dispatcher: NotificationDispatcher
    event_stream_hub: EventStreamHub
    interrupt_store: InterruptStore
    cursor_secret: CursorSecret
    persistence: PersistenceBackend | None = None
    persistence_expected: bool = False
    settings_service: SettingsService | None = None
    auth_service: AuthService | None = None
    audit_log: AuditLog | None = None
    trust_service: TrustService | None = None
    coordination_metrics_store: CoordinationMetricsStore | None = None
    performance_tracker: PerformanceTracker | None = None
    agent_registry: AgentRegistryService | None = None
    training_service: TrainingService | None = None
    delegation_record_store: DelegationRecordStore | None = None
    tool_invocation_tracker: ToolInvocationTracker | None = None
    artifact_storage: ArtifactStorageBackend | None = None
    coordinator: MultiAgentCoordinator | None = None
    work_pipeline: WorkPipeline | None = None
    intake_entry_adapter: WorkEntryAdapter[ClientRequest] | None = None
    task_board_entry_adapter: TaskBoardEntryAdapter | None = None
    client_simulation_state: ClientSimulationState | None = None


def run_construction_wiring(app_state: AppState, deps: ConstructionDeps) -> None:
    """Run every discovered feature's construction-phase wiring hook.

    Iterates the dependency-ordered feature manifests and invokes each
    feature's ``construction_wirer(app_state, deps)`` when one is declared, so
    a feature populates its own state slice at build time from the shared
    construction service bundle. Features with no construction wiring (the
    default) are skipped. Dependency order guarantees a feature that reads
    another feature's slice (declared via ``depends_on``) runs after that
    slice is populated.

    Args:
        app_state: The application state whose feature slices are populated.
        deps: The construction-time service bundle.
    """
    wired = 0
    for feature in discover_features():
        wirer = feature.construction_wirer
        if wirer is None:
            continue
        wirer(app_state, deps)
        wired += 1
    if wired:
        logger.info(
            API_APP_STARTUP,
            action="construction_wiring_complete",
            wired=wired,
        )
