# module-kind: code
"""Injection overrides bundle for the Litestar application factory.

``create_app`` accepts a single optional :class:`AppOverrides` instead of ~27
individual keyword arguments. Every field is an optional dependency that a
caller (chiefly tests and bespoke wiring) may inject; anything left ``None`` is
auto-wired from config and the environment. Bundling them keeps ``create_app``
a thin signature and lets the construction-phase builder take one ``overrides``
parameter rather than two dozen.

The field-type imports are deferred under ``TYPE_CHECKING``: importing this
bundle eagerly at the top of ``api.app`` would otherwise pull the
budget/security/engine/communication graph in a cold order that trips a
pre-existing circular import. PEP 649 keeps the annotations lazy and the frozen
dataclass needs only the field names + ``None`` defaults at runtime.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from synthorg.api.auth.service import AuthService
    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.budget.coordination_store import CoordinationMetricsStore
    from synthorg.budget.tracker import CostTracker
    from synthorg.client.simulation_state import ClientSimulationState
    from synthorg.communication.bus_protocol import MessageBus
    from synthorg.communication.delegation.record_store import DelegationRecordStore
    from synthorg.communication.event_stream.interrupt import InterruptStore
    from synthorg.communication.event_stream.stream import EventStreamHub
    from synthorg.communication.meeting.orchestrator import MeetingOrchestrator
    from synthorg.communication.meeting.scheduler import MeetingScheduler
    from synthorg.engine.coordination.service import MultiAgentCoordinator
    from synthorg.engine.pipeline.entry.protocol import WorkEntryAdapter
    from synthorg.engine.pipeline.entry.task_board_adapter import TaskBoardEntryAdapter
    from synthorg.engine.pipeline.protocol import WorkPipeline
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.hr.performance.tracker import PerformanceTracker
    from synthorg.hr.registry import AgentRegistryService
    from synthorg.hr.training.service import TrainingService
    from synthorg.persistence.artifact_storage import ArtifactStorageBackend
    from synthorg.persistence.protocol import PersistenceBackend
    from synthorg.providers.health import ProviderHealthTracker
    from synthorg.providers.registry import ProviderRegistry
    from synthorg.security.audit import AuditLog
    from synthorg.security.trust.service import TrustService
    from synthorg.settings.service import SettingsService
    from synthorg.tools.invocation_tracker import ToolInvocationTracker


@dataclass(frozen=True)
class AppOverrides:
    """Optional dependency injections for ``create_app`` (all default ``None``).

    Each field, when set, is kept verbatim; an injected double always wins over
    the auto-wired one. A frozen dataclass so the bundle cannot mutate between
    the signature and the construction-phase builder it threads through.
    """

    persistence: PersistenceBackend | None = None
    message_bus: MessageBus | None = None
    cost_tracker: CostTracker | None = None
    approval_store: ApprovalStoreProtocol | None = None
    auth_service: AuthService | None = None
    task_engine: TaskEngine | None = None
    coordinator: MultiAgentCoordinator | None = None
    work_pipeline: WorkPipeline | None = None
    intake_entry_adapter: WorkEntryAdapter[Any] | None = None
    task_board_entry_adapter: TaskBoardEntryAdapter | None = None
    agent_registry: AgentRegistryService | None = None
    meeting_orchestrator: MeetingOrchestrator | None = None
    meeting_scheduler: MeetingScheduler | None = None
    performance_tracker: PerformanceTracker | None = None
    settings_service: SettingsService | None = None
    provider_registry: ProviderRegistry | None = None
    provider_health_tracker: ProviderHealthTracker | None = None
    tool_invocation_tracker: ToolInvocationTracker | None = None
    delegation_record_store: DelegationRecordStore | None = None
    artifact_storage: ArtifactStorageBackend | None = None
    audit_log: AuditLog | None = None
    trust_service: TrustService | None = None
    coordination_metrics_store: CoordinationMetricsStore | None = None
    training_service: TrainingService | None = None
    event_stream_hub: EventStreamHub | None = None
    interrupt_store: InterruptStore | None = None
    client_simulation_state: ClientSimulationState | None = None
