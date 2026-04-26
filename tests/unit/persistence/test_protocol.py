"""Tests for persistence protocol compliance."""

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import pytest

from synthorg.api.guards import HumanRole
from synthorg.core.enums import WorkflowExecutionStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.execution_models import WorkflowExecution
from synthorg.hr.persistence_protocol import (
    CollaborationMetricRepository,
    LifecycleEventRepository,
    TaskMetricRepository,
)
from synthorg.persistence.agent_state_protocol import AgentStateRepository
from synthorg.persistence.artifact_protocol import ArtifactRepository
from synthorg.persistence.audit_protocol import AuditRepository
from synthorg.persistence.checkpoint_protocol import (
    CheckpointRepository,
    HeartbeatRepository,
)
from synthorg.persistence.cost_record_protocol import CostRecordRepository
from synthorg.persistence.decision_protocol import DecisionRepository
from synthorg.persistence.message_protocol import MessageRepository
from synthorg.persistence.parked_context_protocol import ParkedContextRepository
from synthorg.persistence.preset_repository import (
    PersonalityPresetRepository,
    PresetListRow,
    PresetRow,
)
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.settings_protocol import SettingsRepository
from synthorg.persistence.ssrf_violation_protocol import SsrfViolationRepository
from synthorg.persistence.task_protocol import TaskRepository
from synthorg.persistence.training_repos import (
    TrainingPlanRepository,
    TrainingResultRepository,
)
from synthorg.persistence.user_protocol import (
    ApiKeyRepository,
    UserRepository,
)
from synthorg.persistence.workflow_definition_repo import WorkflowDefinitionRepository
from synthorg.persistence.workflow_execution_repo import WorkflowExecutionRepository

if TYPE_CHECKING:
    from pydantic import AwareDatetime

    from synthorg.api.auth.models import ApiKey, User
    from synthorg.budget.cost_record import CostRecord
    from synthorg.communication.message import Message
    from synthorg.core.artifact import Artifact
    from synthorg.core.enums import (
        ApprovalRiskLevel,
        ArtifactType,
        ProjectStatus,
        TaskStatus,
        WorkflowType,
    )
    from synthorg.core.project import Project
    from synthorg.core.task import Task
    from synthorg.engine.agent_state import AgentRuntimeState
    from synthorg.engine.checkpoint.models import Checkpoint, Heartbeat
    from synthorg.engine.decisions import DecisionRecord
    from synthorg.engine.workflow.definition import WorkflowDefinition
    from synthorg.hr.enums import LifecycleEventType
    from synthorg.hr.models import AgentLifecycleEvent
    from synthorg.hr.performance.models import (
        CollaborationMetricRecord,
        TaskMetricRecord,
    )
    from synthorg.security.models import AuditEntry, AuditVerdictStr
    from synthorg.security.timeout.parked_context import ParkedContext


class _FakeTaskRepository:
    async def save(self, task: Task) -> None:
        pass

    async def get(self, task_id: str) -> Task | None:
        return None

    async def list_tasks(
        self,
        *,
        status: TaskStatus | None = None,
        assigned_to: str | None = None,
        project: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[Task, ...]:
        return ()

    async def count_tasks(
        self,
        *,
        status: TaskStatus | None = None,
        assigned_to: str | None = None,
        project: str | None = None,
    ) -> int:
        return 0

    async def delete(self, task_id: str) -> bool:
        return False


class _FakeCostRecordRepository:
    async def save(self, record: CostRecord) -> None:
        pass

    async def query(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
    ) -> tuple[CostRecord, ...]:
        return ()

    async def aggregate(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
    ) -> float:
        return 0.0


class _FakeMessageRepository:
    async def save(self, message: Message) -> None:
        pass

    async def get_history(
        self,
        channel: str,
        *,
        limit: int | None = None,
    ) -> tuple[Message, ...]:
        return ()


class _FakeLifecycleEventRepository:
    async def save(self, event: AgentLifecycleEvent) -> None:
        pass

    async def list_events(
        self,
        *,
        agent_id: NotBlankStr | None = None,
        event_type: LifecycleEventType | None = None,
        since: AwareDatetime | None = None,
        limit: int | None = None,
    ) -> tuple[AgentLifecycleEvent, ...]:
        return ()


class _FakeTaskMetricRepository:
    async def save(self, record: TaskMetricRecord) -> None:
        pass

    async def query(
        self,
        *,
        agent_id: NotBlankStr | None = None,
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
    ) -> tuple[TaskMetricRecord, ...]:
        return ()


class _FakeCollaborationMetricRepository:
    async def save(
        self,
        record: CollaborationMetricRecord,
    ) -> None:
        pass

    async def query(
        self,
        *,
        agent_id: NotBlankStr | None = None,
        since: AwareDatetime | None = None,
    ) -> tuple[CollaborationMetricRecord, ...]:
        return ()


class _FakeParkedContextRepository:
    async def save(self, context: ParkedContext) -> None:
        pass

    async def get(self, parked_id: str) -> ParkedContext | None:
        return None

    async def get_by_approval(self, approval_id: str) -> ParkedContext | None:
        return None

    async def get_by_agent(self, agent_id: str) -> tuple[ParkedContext, ...]:
        return ()

    async def delete(self, parked_id: str) -> bool:
        return False


class _FakeAuditRepository:
    async def save(self, entry: AuditEntry) -> None:
        pass

    async def query(  # noqa: PLR0913
        self,
        *,
        agent_id: str | None = None,
        action_type: str | None = None,
        verdict: AuditVerdictStr | None = None,
        risk_level: ApprovalRiskLevel | None = None,
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
        limit: int = 100,
    ) -> tuple[AuditEntry, ...]:
        return ()

    async def purge_before(self, cutoff: AwareDatetime) -> int:
        return 0


class _FakeDecisionRepository:
    async def append_with_next_version(
        self,
        **_kwargs: object,
    ) -> DecisionRecord:
        raise NotImplementedError

    async def get(self, record_id: str) -> DecisionRecord | None:
        return None

    async def list_by_task(self, task_id: str) -> tuple[DecisionRecord, ...]:
        return ()

    async def list_by_agent(
        self,
        agent_id: str,
        *,
        role: str,
    ) -> tuple[DecisionRecord, ...]:
        return ()


class _FakeUserRepository:
    async def save(self, user: User) -> None:
        pass

    async def get(self, user_id: str) -> User | None:
        return None

    async def get_by_username(self, username: str) -> User | None:
        return None

    async def list_users(self) -> tuple[User, ...]:
        return ()

    async def list_users_paginated(
        self,
        *,
        after_id: str | None,
        limit: int,
    ) -> tuple[User, ...]:
        del after_id, limit
        return ()

    async def count(self) -> int:
        return 0

    async def count_by_role(self, role: HumanRole) -> int:
        return 0

    async def delete(self, user_id: str) -> bool:
        return False


class _FakeApiKeyRepository:
    async def save(self, key: ApiKey) -> None:
        pass

    async def get(self, key_id: str) -> ApiKey | None:
        return None

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        return None

    async def list_by_user(self, user_id: str) -> tuple[ApiKey, ...]:
        return ()

    async def delete(self, key_id: str) -> bool:
        return False


class _FakeCheckpointRepository:
    async def save(self, checkpoint: Checkpoint) -> None:
        pass

    async def get_latest(
        self,
        *,
        execution_id: str | None = None,
        task_id: str | None = None,
    ) -> Checkpoint | None:
        return None

    async def delete_by_execution(self, execution_id: str) -> int:
        return 0


class _FakeHeartbeatRepository:
    async def save(self, heartbeat: Heartbeat) -> None:
        pass

    async def get(self, execution_id: str) -> Heartbeat | None:
        return None

    async def get_stale(
        self,
        threshold: AwareDatetime,
    ) -> tuple[Heartbeat, ...]:
        return ()

    async def delete(self, execution_id: str) -> bool:
        return False


class _FakeAgentStateRepository:
    async def save(self, state: AgentRuntimeState) -> None:
        pass

    async def get(self, agent_id: str) -> AgentRuntimeState | None:
        return None

    async def get_active(self) -> tuple[AgentRuntimeState, ...]:
        return ()

    async def delete(self, agent_id: str) -> bool:
        return False


class _FakeSettingsRepository:
    async def get(self, namespace: str, key: str) -> tuple[str, str] | None:
        return None

    async def get_namespace(self, namespace: str) -> tuple[tuple[str, str, str], ...]:
        return ()

    async def get_all(self) -> tuple[tuple[str, str, str, str], ...]:
        return ()

    async def set(self, namespace: str, key: str, value: str, updated_at: str) -> None:
        pass

    async def set_many(
        self,
        items: Sequence[tuple[NotBlankStr, NotBlankStr, str, str]],
        *,
        expected_updated_at_map: Mapping[tuple[str, str], str] | None = None,
    ) -> bool:
        return True

    async def delete(self, namespace: str, key: str) -> bool:
        return False

    async def delete_namespace(self, namespace: str) -> int:
        return 0

    async def delete_namespace_returning_keys(
        self,
        namespace: str,
    ) -> tuple[NotBlankStr, ...]:
        return ()


class _FakeArtifactRepository:
    async def save(self, artifact: Artifact) -> bool:
        return True

    async def get(self, artifact_id: NotBlankStr) -> Artifact | None:
        return None

    async def list_artifacts(
        self,
        *,
        task_id: NotBlankStr | None = None,
        created_by: NotBlankStr | None = None,
        artifact_type: ArtifactType | None = None,
    ) -> tuple[Artifact, ...]:
        return ()

    async def delete(self, artifact_id: NotBlankStr) -> bool:
        return False


class _FakeProjectRepository:
    async def create(self, project: Project) -> None:
        pass

    async def update(self, project: Project) -> None:
        pass

    async def save(self, project: Project) -> None:
        pass

    async def get(self, project_id: NotBlankStr) -> Project | None:
        return None

    async def list_projects(
        self,
        *,
        status: ProjectStatus | None = None,
        lead: NotBlankStr | None = None,
    ) -> tuple[Project, ...]:
        return ()

    async def delete(self, project_id: NotBlankStr) -> bool:
        return False


class _FakeSsrfViolationRepository:
    async def save(self, violation: Any) -> None:
        pass

    async def get(self, violation_id: NotBlankStr) -> Any | None:
        return None

    async def list_violations(
        self,
        *,
        status: Any | None = None,
        limit: int = 100,
    ) -> tuple[Any, ...]:
        return ()

    async def update_status(
        self,
        violation_id: NotBlankStr,
        *,
        status: Any,
        resolved_by: NotBlankStr,
        resolved_at: AwareDatetime,
    ) -> bool:
        return False


class _FakePersonalityPresetRepository:
    async def save(
        self,
        name: NotBlankStr,
        config_json: str,
        description: str,
        created_at: str,
        updated_at: str,
    ) -> None:
        pass

    async def get(
        self,
        name: NotBlankStr,
    ) -> PresetRow | None:
        return None

    async def list_all(self) -> tuple[PresetListRow, ...]:
        return ()

    async def delete(self, name: NotBlankStr) -> bool:
        return False

    async def count(self) -> int:
        return 0


class _FakeWorkflowDefinitionRepository:
    async def save(self, definition: WorkflowDefinition) -> None:
        pass

    async def create_if_absent(self, definition: WorkflowDefinition) -> bool:
        return True

    async def update_if_exists(self, definition: WorkflowDefinition) -> bool:
        return True

    async def get(self, definition_id: NotBlankStr) -> WorkflowDefinition | None:
        return None

    async def list_definitions(
        self,
        *,
        workflow_type: WorkflowType | None = None,
    ) -> tuple[WorkflowDefinition, ...]:
        return ()

    async def delete(self, definition_id: NotBlankStr) -> bool:
        return False


class _FakeWorkflowExecutionRepository:
    async def save(self, execution: WorkflowExecution) -> None:
        pass

    async def get(
        self,
        execution_id: NotBlankStr,
    ) -> WorkflowExecution | None:
        return None

    async def list_by_definition(
        self,
        definition_id: NotBlankStr,
    ) -> tuple[WorkflowExecution, ...]:
        return ()

    async def list_by_status(
        self,
        status: WorkflowExecutionStatus,
    ) -> tuple[WorkflowExecution, ...]:
        return ()

    async def find_by_task_id(
        self,
        task_id: NotBlankStr,
    ) -> WorkflowExecution | None:
        return None

    async def delete(self, execution_id: NotBlankStr) -> bool:
        return False


class _FakeBackend:
    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def health_check(self) -> bool:
        return True

    async def migrate(self) -> None:
        pass

    def get_db(self) -> object:
        msg = "Not supported"
        raise NotImplementedError(msg)

    @property
    def is_connected(self) -> bool:
        return True

    @property
    def backend_name(self) -> NotBlankStr:
        return NotBlankStr("fake")

    @property
    def tasks(self) -> _FakeTaskRepository:
        return _FakeTaskRepository()

    @property
    def cost_records(self) -> _FakeCostRecordRepository:
        return _FakeCostRecordRepository()

    @property
    def messages(self) -> _FakeMessageRepository:
        return _FakeMessageRepository()

    @property
    def lifecycle_events(self) -> _FakeLifecycleEventRepository:
        return _FakeLifecycleEventRepository()

    @property
    def task_metrics(self) -> _FakeTaskMetricRepository:
        return _FakeTaskMetricRepository()

    @property
    def parked_contexts(self) -> _FakeParkedContextRepository:
        return _FakeParkedContextRepository()

    @property
    def collaboration_metrics(self) -> _FakeCollaborationMetricRepository:
        return _FakeCollaborationMetricRepository()

    @property
    def audit_entries(self) -> _FakeAuditRepository:
        return _FakeAuditRepository()

    @property
    def decision_records(self) -> _FakeDecisionRepository:
        return _FakeDecisionRepository()

    @property
    def users(self) -> _FakeUserRepository:
        return _FakeUserRepository()

    @property
    def api_keys(self) -> _FakeApiKeyRepository:
        return _FakeApiKeyRepository()

    @property
    def checkpoints(self) -> _FakeCheckpointRepository:
        return _FakeCheckpointRepository()

    @property
    def heartbeats(self) -> _FakeHeartbeatRepository:
        return _FakeHeartbeatRepository()

    @property
    def agent_states(self) -> _FakeAgentStateRepository:
        return _FakeAgentStateRepository()

    @property
    def settings(self) -> _FakeSettingsRepository:
        return _FakeSettingsRepository()

    @property
    def artifacts(self) -> _FakeArtifactRepository:
        return _FakeArtifactRepository()

    @property
    def projects(self) -> _FakeProjectRepository:
        return _FakeProjectRepository()

    @property
    def custom_presets(self) -> _FakePersonalityPresetRepository:
        return _FakePersonalityPresetRepository()

    @property
    def workflow_definitions(self) -> _FakeWorkflowDefinitionRepository:
        return _FakeWorkflowDefinitionRepository()

    @property
    def workflow_executions(self) -> _FakeWorkflowExecutionRepository:
        return _FakeWorkflowExecutionRepository()

    @property
    def subworkflows(self) -> object:
        # ``PersistenceBackend`` is ``@runtime_checkable``, which only
        # verifies that the attribute exists -- returning a bare
        # ``object()`` is enough for the isinstance conformance check.
        return object()

    @property
    def workflow_versions(self) -> object:
        # ``PersistenceBackend`` is ``@runtime_checkable``, which only
        # verifies that the ``workflow_versions`` attribute exists --
        # not that its value implements the full
        # ``VersionRepository[WorkflowDefinition]`` interface.
        # Returning a bare ``object()`` is enough to satisfy the
        # isinstance conformance test in this module.
        return object()

    @property
    def risk_overrides(self) -> Any:
        return object()

    @property
    def ssrf_violations(self) -> _FakeSsrfViolationRepository:
        # Real fake repo (not ``object()``) so backend-level contract
        # access actually exposes an ``SsrfViolationRepository``-shaped
        # object -- callers can exercise SSRF wiring without bypassing
        # the protocol.  Return type is the concrete fake (not ``Any``)
        # so the runtime-protocol check at the test site can verify
        # the backend's exposure path against ``SsrfViolationRepository``.
        return _FakeSsrfViolationRepository()

    @property
    def identity_versions(self) -> Any:
        return object()

    @property
    def evaluation_config_versions(self) -> Any:
        return object()

    @property
    def budget_config_versions(self) -> Any:
        return object()

    @property
    def company_versions(self) -> Any:
        return object()

    @property
    def role_versions(self) -> Any:
        return object()

    @property
    def circuit_breaker_state(self) -> Any:
        return object()

    @property
    def connections(self) -> Any:
        return object()

    @property
    def connection_secrets(self) -> Any:
        return object()

    @property
    def oauth_states(self) -> Any:
        return object()

    @property
    def webhook_receipts(self) -> Any:
        return object()

    @property
    def training_plans(self) -> Any:
        return _FakeTrainingPlanRepository()

    @property
    def training_results(self) -> Any:
        return _FakeTrainingResultRepository()

    @property
    def custom_rules(self) -> Any:
        return None

    @property
    def sessions(self) -> Any:
        return None

    @property
    def refresh_tokens(self) -> Any:
        return None

    @property
    def idempotency_keys(self) -> Any:
        return None

    @property
    def mcp_installations(self) -> Any:
        return None

    @property
    def org_facts(self) -> Any:
        return None

    @property
    def ontology_entities(self) -> Any:
        return None

    @property
    def ontology_drift(self) -> Any:
        return None

    @property
    def project_cost_aggregates(self) -> Any:
        # Same contract as the real backends: raise rather than silently
        # returning ``None`` so misuse of this fake fails at the
        # protocol boundary instead of deep inside a service.
        msg = "project_cost_aggregates not supported by the protocol-compliance fake"
        raise NotImplementedError(msg)

    @property
    def fine_tune_checkpoints(self) -> Any:
        # Match the contract of the real backends: if the backend does
        # not implement fine-tune persistence it must raise, not silently
        # hand back ``None`` that would fail later with a NoneType error
        # somewhere deep in a service call.
        msg = "fine_tune_checkpoints not supported by the protocol-compliance fake"
        raise NotImplementedError(msg)

    @property
    def fine_tune_runs(self) -> Any:
        msg = "fine_tune_runs not supported by the protocol-compliance fake"
        raise NotImplementedError(msg)

    def build_lockouts(self, auth_config: Any) -> Any:
        return None

    def build_escalations(self, *, notify_channel: str | None = None) -> Any:
        return None

    def build_ontology_versioning(self) -> Any:
        return None

    async def get_setting(self, key: str) -> str | None:
        return None

    async def set_setting(self, key: str, value: str) -> None:
        pass


@pytest.mark.unit
class TestProtocolCompliance:
    def test_fake_backend_is_persistence_backend(self) -> None:
        assert isinstance(_FakeBackend(), PersistenceBackend)

    def test_fake_task_repo_is_task_repository(self) -> None:
        assert isinstance(_FakeTaskRepository(), TaskRepository)

    def test_fake_cost_repo_is_cost_record_repository(self) -> None:
        assert isinstance(_FakeCostRecordRepository(), CostRecordRepository)

    def test_fake_message_repo_is_message_repository(self) -> None:
        assert isinstance(_FakeMessageRepository(), MessageRepository)

    def test_fake_lifecycle_repo_is_lifecycle_event_repository(self) -> None:
        assert isinstance(_FakeLifecycleEventRepository(), LifecycleEventRepository)

    def test_fake_task_metric_repo_is_task_metric_repository(self) -> None:
        assert isinstance(_FakeTaskMetricRepository(), TaskMetricRepository)

    def test_fake_collab_metric_repo_is_collaboration_metric_repository(
        self,
    ) -> None:
        assert isinstance(
            _FakeCollaborationMetricRepository(),
            CollaborationMetricRepository,
        )

    def test_fake_parked_context_repo_is_parked_context_repository(
        self,
    ) -> None:
        assert isinstance(
            _FakeParkedContextRepository(),
            ParkedContextRepository,
        )

    def test_fake_audit_repo_is_audit_repository(self) -> None:
        assert isinstance(_FakeAuditRepository(), AuditRepository)

    def test_fake_decision_repo_is_decision_repository(self) -> None:
        assert isinstance(_FakeDecisionRepository(), DecisionRepository)

    def test_fake_user_repo_is_user_repository(self) -> None:
        assert isinstance(_FakeUserRepository(), UserRepository)

    def test_fake_api_key_repo_is_api_key_repository(self) -> None:
        assert isinstance(_FakeApiKeyRepository(), ApiKeyRepository)

    def test_fake_checkpoint_repo_is_checkpoint_repository(self) -> None:
        assert isinstance(_FakeCheckpointRepository(), CheckpointRepository)

    def test_fake_heartbeat_repo_is_heartbeat_repository(self) -> None:
        assert isinstance(_FakeHeartbeatRepository(), HeartbeatRepository)

    def test_fake_agent_state_repo_is_agent_state_repository(self) -> None:
        assert isinstance(_FakeAgentStateRepository(), AgentStateRepository)

    def test_fake_settings_repo_is_settings_repository(self) -> None:
        assert isinstance(_FakeSettingsRepository(), SettingsRepository)

    def test_fake_artifact_repo_is_artifact_repository(self) -> None:
        assert isinstance(_FakeArtifactRepository(), ArtifactRepository)

    def test_fake_project_repo_is_project_repository(self) -> None:
        assert isinstance(_FakeProjectRepository(), ProjectRepository)

    def test_fake_ssrf_violation_repo_is_ssrf_violation_repository(self) -> None:
        # Route through the backend property so the test exercises the
        # contract-fidelity path (backend.ssrf_violations -> repo) rather
        # than constructing the fake directly -- catches regressions where
        # ``_FakeBackend.ssrf_violations`` drifts back to ``object()``.
        backend = _FakeBackend()
        assert isinstance(backend.ssrf_violations, SsrfViolationRepository)

    def test_fake_preset_repo_is_personality_preset_repository(self) -> None:
        assert isinstance(
            _FakePersonalityPresetRepository(),
            PersonalityPresetRepository,
        )

    def test_fake_workflow_def_repo_is_workflow_definition_repository(
        self,
    ) -> None:
        assert isinstance(
            _FakeWorkflowDefinitionRepository(),
            WorkflowDefinitionRepository,
        )

    def test_fake_workflow_exec_repo_is_workflow_execution_repository(
        self,
    ) -> None:
        assert isinstance(
            _FakeWorkflowExecutionRepository(),
            WorkflowExecutionRepository,
        )

    def test_fake_training_plan_repo_is_training_plan_repository(
        self,
    ) -> None:
        assert isinstance(
            _FakeTrainingPlanRepository(),
            TrainingPlanRepository,
        )

    def test_fake_training_result_repo_is_training_result_repository(
        self,
    ) -> None:
        assert isinstance(
            _FakeTrainingResultRepository(),
            TrainingResultRepository,
        )


class _FakeTrainingPlanRepository:
    async def save(self, plan: Any) -> None:
        pass

    async def get(self, plan_id: Any) -> Any | None:
        return None

    async def latest_pending(self, agent_id: Any) -> Any | None:
        return None

    async def latest_by_agent(self, agent_id: Any) -> Any | None:
        return None

    async def list_by_agent(self, agent_id: Any) -> tuple[Any, ...]:
        return ()


class _FakeTrainingResultRepository:
    async def save(self, result: Any) -> None:
        pass

    async def get_by_plan(self, plan_id: Any) -> Any | None:
        return None

    async def get_latest(self, agent_id: Any) -> Any | None:
        return None
