"""Tests for persistence protocol compliance.

The fake repositories below are duck-typed conformance stubs checked
against the ``@runtime_checkable`` repository protocols via
``isinstance`` (method-name presence, not static signatures). Their
discarded protocol parameters are typed ``object`` rather than each
repo's concrete ``FilterSpec`` / entity type: the stubs never use the
values, ``object`` accepts the protocol's narrower types
contravariantly, and importing every concrete spec would bloat this
file beyond the module-size budget.
"""

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Literal

import pytest

from synthorg.core.auth.roles import HumanRole
from synthorg.core.decomposition_progress import DecompositionProgress
from synthorg.core.deleted_entity import DeletedEntity
from synthorg.core.lifecycle_transition import LifecycleTransition
from synthorg.core.resume_intent import ResumeIntent
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.execution_models import WorkflowExecution
from synthorg.hr.persistence_protocol import (
    LifecycleEventRepository,
    TaskMetricRepository,
)
from synthorg.persistence.agent_state_protocol import AgentStateRepository
from synthorg.persistence.artifact_protocol import ArtifactRepository
from synthorg.persistence.audit_protocol import AuditRepository
from synthorg.persistence.capability_source_status_protocol import (
    CapabilitySourceStatusRepository,
)
from synthorg.persistence.checkpoint_protocol import (
    CheckpointRepository,
    HeartbeatRepository,
)
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionRecordRepository,
)
from synthorg.persistence.completion_oracle_report_protocol import (
    CompletionOracleReportArchiveRepository,
)
from synthorg.persistence.config import PostgresConfig, SQLiteConfig
from synthorg.persistence.cost_record_protocol import CostRecordRepository
from synthorg.persistence.decision_protocol import DecisionRepository
from synthorg.persistence.deleted_entity_protocol import (
    DeletedEntityFilterSpec,
)
from synthorg.persistence.deliverable_receipt_protocol import (
    DeliverableReceiptRepository,
)
from synthorg.persistence.evaluation_report_protocol import (
    EvaluationReportRepository,
)
from synthorg.persistence.flight_recorder_protocol import FlightRecorderFrameRepository
from synthorg.persistence.idempotency_protocol import (
    IdempotencyClaim,
    IdempotencyOutcome,
    IdempotencyRecord,
    IdempotencyRepository,
)
from synthorg.persistence.knowledge_protocol import (
    ChunkProvenanceRepository,
    KnowledgeSourceRepository,
)
from synthorg.persistence.knowledge_usage_protocol import (
    KnowledgeUsageRecordRepository,
)
from synthorg.persistence.lifecycle_transition_protocol import (
    LifecycleTransitionFilterSpec,
)
from synthorg.persistence.message_protocol import MessageRepository
from synthorg.persistence.model_capability_score_protocol import (
    ModelCapabilityScoreRepository,
)
from synthorg.persistence.model_tool_call_signal_protocol import (
    ModelToolCallSignal,
    ModelToolCallSignalKey,
    ModelToolCallSignalRepository,
)
from synthorg.persistence.parked_context_protocol import ParkedContextRepository
from synthorg.persistence.plan_comment_protocol import PlanItemCommentRepository
from synthorg.persistence.plan_protocol import PlanDeleteOutcome, PlanRepository
from synthorg.persistence.principle_override_protocol import (
    PrincipleOverrideRepository,
)
from synthorg.persistence.project_cost_claim_seen_protocol import (
    ProjectCostClaimSeenRepository,
)
from synthorg.persistence.project_environment_protocol import (
    ProjectEnvironmentRepository,
)
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.persistence.project_workspace_protocol import (
    ProjectWorkspaceRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.provider_failover_event_protocol import (
    ProviderFailoverEventRepository,
)
from synthorg.persistence.red_team_report_protocol import (
    RedTeamReportArchiveRepository,
)
from synthorg.persistence.research_protocol import ResearchRunRepository
from synthorg.persistence.resume_intent_protocol import ResumeIntentRepository
from synthorg.persistence.seen_claims_protocol import SeenClaimsRepository
from synthorg.persistence.settings_protocol import SettingsRepository
from synthorg.persistence.ssrf_violation_protocol import SsrfViolationRepository
from synthorg.persistence.task_protocol import TaskRepository
from synthorg.persistence.user_protocol import (
    ApiKeyFilterSpec,
    ApiKeyRepository,
    UserFilterSpec,
    UserRepository,
)
from synthorg.persistence.workflow_definition_protocol import (
    WorkflowDefinitionFilterSpec,
    WorkflowDefinitionRepository,
)
from synthorg.persistence.workflow_execution_protocol import (
    WorkflowExecutionRepository,
)
from synthorg.providers.capability_sources.models import (
    CapabilityScore,
    CapabilityScoreKey,
)
from synthorg.providers.capability_sources.status import CapabilitySourceStatus
from tests.unit.deliverable_receipts._fakes import (
    InMemoryCodeExecutionRecordRepository,
    InMemoryDeliverableReceiptRepository,
    InMemoryEvaluationReportRepository,
    InMemoryKnowledgeUsageRecordRepository,
)
from tests.unit.research._fakes import InMemoryResearchRunRepository

if TYPE_CHECKING:
    from pydantic import AwareDatetime

    from synthorg.budget.cost_record import CostRecord
    from synthorg.communication.message import Message
    from synthorg.core.artifact import Artifact
    from synthorg.core.auth.models import ApiKey, User
    from synthorg.core.plan import Plan
    from synthorg.core.plan_comment import PlanItemComment
    from synthorg.core.project import Project
    from synthorg.core.project_environment import ProjectEnvironment
    from synthorg.core.project_workspace import ProjectWorkspace
    from synthorg.core.task import Task
    from synthorg.engine.agent_state import AgentRuntimeState
    from synthorg.engine.checkpoint.models import Checkpoint, Heartbeat
    from synthorg.engine.completion_oracle.review_models import (
        CompletionOracleReportRecord,
    )
    from synthorg.engine.decisions import DecisionRecord
    from synthorg.engine.workflow.definition import WorkflowDefinition
    from synthorg.execution.parked_context import ParkedContext
    from synthorg.hr.enums import LifecycleEventType
    from synthorg.hr.models import AgentLifecycleEvent
    from synthorg.hr.performance.models import TaskMetricRecord
    from synthorg.knowledge.models import ChunkProvenanceRow, KnowledgeSource
    from synthorg.persistence.flight_recorder_protocol import (
        FlightRecorderFrame,
        FlightRecorderFrameAggregate,
    )
    from synthorg.persistence.principle_override_protocol import PrincipleOverride
    from synthorg.providers.failover_event import ProviderFailoverEvent
    from synthorg.security.models import AuditEntry
    from synthorg.security.redteam.models import RedTeamReportRecord
    from synthorg.security.ssrf_violation import SsrfViolation


class _FakeTaskRepository:
    async def save(self, entity: Task) -> None:
        pass

    async def save_many(self, entities: tuple[Task, ...]) -> None:
        del entities

    async def get(self, entity_id: NotBlankStr) -> Task | None:
        return None

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Task, ...]:
        del limit, offset
        return ()

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Task, ...]:
        del filter_spec, limit, offset
        return ()

    async def count(self, filter_spec: object) -> int:
        del filter_spec
        return 0

    async def delete(self, entity_id: NotBlankStr) -> bool:
        del entity_id
        return False


class _FakeCostRecordRepository:
    async def append(self, event: CostRecord) -> None:
        pass

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[CostRecord, ...]:
        del filter_spec, limit, offset
        return ()

    async def purge_before(self, threshold: AwareDatetime) -> int:
        del threshold
        return 0

    async def aggregate(
        self,
        *,
        agent_id: NotBlankStr | None = None,
        task_id: NotBlankStr | None = None,
    ) -> float:
        del agent_id, task_id
        return 0.0


class _FakeMessageRepository:
    async def append(self, message: Message) -> None:
        pass

    async def get_history(
        self,
        channel: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[Message, ...]:
        return ()

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Message, ...]:
        return ()

    async def purge_before(self, threshold: object) -> int:
        return 0

    async def get_by_id(
        self,
        channel: str,
        message_id: str,
    ) -> Message | None:
        return None

    async def delete(self, message_id: str) -> bool:
        return False


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


class _FakeLifecycleTransitionRepository:
    async def append(self, event: LifecycleTransition) -> None:
        pass

    async def query(
        self,
        filter_spec: LifecycleTransitionFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[LifecycleTransition, ...]:
        return ()

    async def purge_before(self, threshold: AwareDatetime) -> int:
        return 0


class _FakeDeletedEntityRepository:
    async def append(self, event: DeletedEntity) -> None:
        pass

    async def query(
        self,
        filter_spec: DeletedEntityFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[DeletedEntity, ...]:
        return ()

    async def purge_before(self, threshold: AwareDatetime) -> int:
        return 0


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


class _FakeParkedContextRepository:
    async def save(self, context: ParkedContext) -> None:
        pass

    async def get(self, parked_id: str) -> ParkedContext | None:
        return None

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ParkedContext, ...]:
        del limit, offset
        return ()

    async def get_by_approval(self, approval_id: str) -> ParkedContext | None:
        return None

    async def get_by_agent(self, agent_id: str) -> tuple[ParkedContext, ...]:
        return ()

    async def delete(self, parked_id: str) -> bool:
        return False


class _FakeResumeIntentRepository:
    async def save(self, intent: ResumeIntent) -> None:
        pass

    async def get(self, approval_id: str) -> ResumeIntent | None:
        return None

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ResumeIntent, ...]:
        del limit, offset
        return ()

    async def delete(self, approval_id: str) -> bool:
        return False


class _FakeAuditRepository:
    async def append(self, entry: AuditEntry) -> None:
        pass

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[AuditEntry, ...]:
        del filter_spec, limit, offset
        return ()

    async def purge_before(self, cutoff: AwareDatetime) -> int:
        del cutoff
        return 0

    async def query_jsonb_contains(
        self,
        column: str,
        value: dict[str, object] | list[object],
        *,
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[tuple[AuditEntry, ...], int]:
        del column, value, since, until, limit, offset
        return ((), 0)

    async def query_jsonb_key_exists(
        self,
        column: str,
        key: str,
        *,
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[tuple[AuditEntry, ...], int]:
        del column, key, since, until, limit, offset
        return ((), 0)


class _FakeProviderAuditRepo:
    """Stub conforming to the ``ProviderAuditRepo`` protocol."""

    async def record(self, event: object) -> object:
        return event

    async def list(
        self,
        *,
        provider_name: str,
        after_id: int | None = None,
        limit: int = 50,
    ) -> tuple[tuple[object, ...], bool]:
        return ((), False)

    async def purge_before_id(self, *, before_id: int) -> int:
        return 0


class _FakePresetOverrideRepo:
    """Stub conforming to the ``PresetOverrideRepo`` protocol."""

    async def get(self, preset_name: str) -> object | None:
        return None

    async def upsert(self, override: object) -> object:
        return override

    async def delete(self, preset_name: str) -> bool:
        return False


class _FakeDecisionRepository:
    async def append(self, event: object) -> None:
        pass

    async def append_with_next_version(
        self,
        **_kwargs: object,
    ) -> DecisionRecord:
        raise NotImplementedError

    async def get(self, record_id: str) -> DecisionRecord | None:
        return None

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[DecisionRecord, ...]:
        del limit, offset
        return ()

    async def list_by_task(self, task_id: str) -> tuple[DecisionRecord, ...]:
        return ()

    async def list_by_agent(
        self,
        agent_id: str,
        *,
        role: str,
    ) -> tuple[DecisionRecord, ...]:
        return ()

    async def purge_before(self, threshold: object) -> int:
        return 0


class _FakeUserRepository:
    async def save(self, entity: User) -> None:
        pass

    async def get(self, entity_id: str) -> User | None:
        return None

    async def get_by_username(self, username: str) -> User | None:
        return None

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[User, ...]:
        return ()

    async def list_after_id(
        self,
        *,
        after_id: str | None = None,
        limit: int = 100,
    ) -> tuple[User, ...]:
        return ()

    async def query(
        self,
        filter_spec: UserFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[User, ...]:
        return ()

    async def count(self, filter_spec: UserFilterSpec) -> int:
        return 0

    async def count_by_role(self, role: HumanRole) -> int:
        return 0

    async def delete(self, entity_id: str) -> bool:
        return False


class _FakeApiKeyRepository:
    async def save(self, entity: ApiKey) -> None:
        pass

    async def get(self, entity_id: str) -> ApiKey | None:
        return None

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        return None

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ApiKey, ...]:
        return ()

    async def query(
        self,
        filter_spec: ApiKeyFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ApiKey, ...]:
        return ()

    async def count(self, filter_spec: ApiKeyFilterSpec) -> int:
        return 0

    async def delete(self, entity_id: str) -> bool:
        return False


class _FakeFlightRecorderRepository:
    async def append(self, frame: object) -> None:
        pass

    async def append_many(self, frames: object) -> None:
        pass

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[FlightRecorderFrame, ...]:
        return ()

    async def get_aggregate(self, filter_spec: object) -> FlightRecorderFrameAggregate:
        from synthorg.persistence.flight_recorder_protocol import (
            FlightRecorderFrameAggregate,
        )

        return FlightRecorderFrameAggregate()

    async def purge_before(self, threshold: object) -> int:
        return 0


class _FakeRedTeamReportArchiveRepository:
    async def append(self, record: object) -> None:
        pass

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[RedTeamReportRecord, ...]:
        return ()

    async def count(self, filter_spec: object) -> int:
        return 0

    async def count_by_verdict(self, filter_spec: object) -> Mapping[str, int]:
        return {}

    async def purge_before(self, threshold: object) -> int:
        return 0


class _FakeCompletionOracleReportArchiveRepository:
    async def append(self, record: object) -> None:
        pass

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[CompletionOracleReportRecord, ...]:
        return ()

    async def count(self, filter_spec: object) -> int:
        return 0

    async def count_by_verdict(self, filter_spec: object) -> Mapping[str, int]:
        return {}

    async def purge_before(self, threshold: object) -> int:
        return 0


class _FakeCheckpointRepository:
    async def append(self, checkpoint: Checkpoint) -> None:
        pass

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Checkpoint, ...]:
        return ()

    async def purge_before(self, threshold: object) -> int:
        return 0

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

    async def save_if_execution(
        self,
        state: AgentRuntimeState,
        *,
        expected_execution_id: str,
    ) -> bool:
        del state, expected_execution_id
        return True

    async def get(self, agent_id: str) -> AgentRuntimeState | None:
        return None

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[AgentRuntimeState, ...]:
        del limit, offset
        return ()

    async def get_active(self) -> tuple[AgentRuntimeState, ...]:
        return ()

    async def delete(self, agent_id: str) -> bool:
        return False


class _FakeSettingsRepository:
    async def save(self, entity: object) -> None:
        del entity

    async def get(self, entity_id: tuple[NotBlankStr, NotBlankStr]) -> object | None:
        del entity_id
        return None

    async def delete(self, entity_id: tuple[NotBlankStr, NotBlankStr]) -> bool:
        del entity_id
        return False

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[object, ...]:
        del limit, offset
        return ()

    async def get_namespace(self, namespace: NotBlankStr) -> tuple[object, ...]:
        del namespace
        return ()

    async def set_if_unchanged(
        self,
        entity: object,
        *,
        expected_updated_at: str | None = None,
    ) -> bool:
        del entity, expected_updated_at
        return True

    async def set_many(
        self,
        items: Sequence[object],
        *,
        expected_updated_at_map: (
            Mapping[tuple[NotBlankStr, NotBlankStr], str] | None
        ) = None,
    ) -> bool:
        del items, expected_updated_at_map
        return True

    async def delete_namespace(self, namespace: NotBlankStr) -> int:
        del namespace
        return 0

    async def delete_namespace_returning_keys(
        self,
        namespace: NotBlankStr,
    ) -> tuple[NotBlankStr, ...]:
        del namespace
        return ()


class _FakeArtifactRepository:
    async def save(self, entity: Artifact) -> None:
        pass

    async def save_returning_outcome(self, artifact: Artifact) -> bool:
        return True

    async def get(self, entity_id: NotBlankStr) -> Artifact | None:
        return None

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Artifact, ...]:
        return ()

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Artifact, ...]:
        return ()

    async def count(self, filter_spec: object) -> int:
        return 0

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return False


class _FakeProjectWorkspaceRepository:
    async def save(self, entity: ProjectWorkspace) -> None:
        del entity

    async def get(self, entity_id: NotBlankStr) -> ProjectWorkspace | None:
        del entity_id
        return None

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[ProjectWorkspace, ...]:
        del limit, offset
        return ()

    async def delete(self, entity_id: NotBlankStr) -> bool:
        del entity_id
        return False


class _FakeProjectEnvironmentRepository:
    async def save(self, entity: ProjectEnvironment) -> None:
        del entity

    async def get(self, entity_id: NotBlankStr) -> ProjectEnvironment | None:
        del entity_id
        return None

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[ProjectEnvironment, ...]:
        del limit, offset
        return ()

    async def delete(self, entity_id: NotBlankStr) -> bool:
        del entity_id
        return False


class _FakeProjectRepository:
    async def create(self, project: Project) -> None:
        pass

    async def update(
        self, project: Project, *, expected_version: int | None = None
    ) -> None:
        pass

    async def save(self, entity: Project) -> None:
        pass

    async def get(self, entity_id: NotBlankStr) -> Project | None:
        del entity_id
        return None

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Project, ...]:
        del limit, offset
        return ()

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Project, ...]:
        del filter_spec, limit, offset
        return ()

    async def count(self, filter_spec: object) -> int:
        del filter_spec
        return 0

    async def delete(self, entity_id: NotBlankStr) -> bool:
        del entity_id
        return False


class _FakePlanRepository:
    async def create(self, plan: Plan) -> None:
        pass

    async def update(self, plan: Plan, *, expected_version: int | None = None) -> None:
        pass

    async def save(self, entity: Plan) -> None:
        pass

    async def get(self, entity_id: NotBlankStr) -> Plan | None:
        del entity_id
        return None

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Plan, ...]:
        del limit, offset
        return ()

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Plan, ...]:
        del filter_spec, limit, offset
        return ()

    async def count(self, filter_spec: object) -> int:
        del filter_spec
        return 0

    async def delete(self, entity_id: NotBlankStr) -> bool:
        del entity_id
        return False

    async def delete_if_no_live_tasks(
        self,
        entity_id: NotBlankStr,
        /,
        *,
        terminal_statuses: frozenset[str],
    ) -> PlanDeleteOutcome:
        del entity_id, terminal_statuses
        return PlanDeleteOutcome(deleted=False)

    async def record_decomposition_progress(
        self,
        parent_task_id: NotBlankStr,
        /,
        *,
        progress: DecompositionProgress,
    ) -> Plan | None:
        del parent_task_id, progress
        return None


class _FakePlanItemCommentRepository:
    async def append(self, event: PlanItemComment) -> None:
        del event

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[PlanItemComment, ...]:
        del filter_spec, limit, offset
        return ()

    async def purge_before(self, threshold: AwareDatetime) -> int:
        del threshold
        return 0


class _FakeSsrfViolationRepository:
    async def save(self, entity: object) -> None:
        pass

    async def get(self, entity_id: NotBlankStr) -> SsrfViolation | None:
        del entity_id
        return None

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[SsrfViolation, ...]:
        del limit, offset
        return ()

    async def list_violations(
        self,
        *,
        status: object | None = None,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[SsrfViolation, ...]:
        del status, limit, offset
        return ()

    async def update_status(
        self,
        violation_id: NotBlankStr,
        *,
        status: object,
        resolved_by: NotBlankStr,
        resolved_at: object,
    ) -> bool:
        del violation_id, status, resolved_by, resolved_at
        return False

    async def delete(self, entity_id: NotBlankStr) -> bool:
        del entity_id
        return False


class _FakeCapabilitySourceStatusRepository:
    async def save(self, entity: CapabilitySourceStatus, /) -> None:
        del entity

    async def get(self, entity_id: NotBlankStr, /) -> CapabilitySourceStatus | None:
        del entity_id
        return None

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[CapabilitySourceStatus, ...]:
        del limit, offset
        return ()

    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        del entity_id
        return False


class _FakeProviderFailoverEventRepository:
    async def append(self, event: object, /) -> None:
        del event

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[ProviderFailoverEvent, ...]:
        del filter_spec, limit, offset
        return ()

    async def purge_before(self, threshold: object, /) -> int:
        del threshold
        return 0


class _FakeModelCapabilityScoreRepository:
    async def save(self, entity: CapabilityScore, /) -> None:
        del entity

    async def save_many(self, entities: Sequence[CapabilityScore], /) -> None:
        del entities

    async def get(self, entity_id: CapabilityScoreKey, /) -> CapabilityScore | None:
        del entity_id
        return None

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[CapabilityScore, ...]:
        del limit, offset
        return ()

    async def delete(self, entity_id: CapabilityScoreKey, /) -> bool:
        del entity_id
        return False


class _FakeModelToolCallSignalRepository:
    async def save(self, entity: ModelToolCallSignal, /) -> None:
        del entity

    async def get(
        self, entity_id: ModelToolCallSignalKey, /
    ) -> ModelToolCallSignal | None:
        del entity_id
        return None

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[ModelToolCallSignal, ...]:
        del limit, offset
        return ()

    async def delete(self, entity_id: ModelToolCallSignalKey, /) -> bool:
        del entity_id
        return False

    async def update_status(
        self,
        violation_id: NotBlankStr,
        *,
        status: object,
        resolved_by: NotBlankStr,
        resolved_at: AwareDatetime,
    ) -> bool:
        del violation_id, status, resolved_by, resolved_at
        return False


class _FakeKnowledgeSourceRepository:
    async def save(self, entity: object) -> None:
        del entity

    async def get(self, entity_id: NotBlankStr) -> KnowledgeSource | None:
        del entity_id
        return None

    async def get_many(
        self, source_ids: tuple[NotBlankStr, ...]
    ) -> tuple[KnowledgeSource, ...]:
        del source_ids
        return ()

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[KnowledgeSource, ...]:
        del limit, offset
        return ()

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[KnowledgeSource, ...]:
        del filter_spec, limit, offset
        return ()

    async def count(self, filter_spec: object) -> int:
        del filter_spec
        return 0

    async def delete(self, entity_id: NotBlankStr) -> bool:
        del entity_id
        return False


class _FakeChunkProvenanceRepository:
    async def save(self, entity: object) -> None:
        del entity

    async def get(self, entity_id: NotBlankStr) -> ChunkProvenanceRow | None:
        del entity_id
        return None

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[ChunkProvenanceRow, ...]:
        del limit, offset
        return ()

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[ChunkProvenanceRow, ...]:
        del filter_spec, limit, offset
        return ()

    async def count(self, filter_spec: object) -> int:
        del filter_spec
        return 0

    async def delete(self, entity_id: NotBlankStr) -> bool:
        del entity_id
        return False

    async def get_many(
        self, chunk_ids: tuple[NotBlankStr, ...]
    ) -> tuple[ChunkProvenanceRow, ...]:
        del chunk_ids
        return ()

    async def delete_by_source(self, source_id: NotBlankStr) -> int:
        del source_id
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

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[WorkflowDefinition, ...]:
        del limit, offset
        return ()

    async def query(
        self,
        filter_spec: WorkflowDefinitionFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[WorkflowDefinition, ...]:
        del filter_spec, limit, offset
        return ()

    async def count(self, filter_spec: WorkflowDefinitionFilterSpec) -> int:
        del filter_spec
        return 0

    async def delete(self, definition_id: NotBlankStr) -> bool:
        return False


class _FakeWorkflowExecutionRepository:
    async def save(self, entity: WorkflowExecution) -> None:
        del entity

    async def get(self, entity_id: NotBlankStr) -> WorkflowExecution | None:
        del entity_id
        return None

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[WorkflowExecution, ...]:
        del limit, offset
        return ()

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[WorkflowExecution, ...]:
        del filter_spec, limit, offset
        return ()

    async def count(self, filter_spec: object) -> int:
        del filter_spec
        return 0

    async def find_by_task_id(
        self,
        task_id: NotBlankStr,
    ) -> WorkflowExecution | None:
        del task_id
        return None

    async def delete(self, entity_id: NotBlankStr) -> bool:
        del entity_id
        return False


class _FakeSeenClaimsRepository:
    """Minimal SeenClaimsRepository conforming to the protocol shape."""

    async def is_completed(
        self,
        *,
        idempotency_key: NotBlankStr,
    ) -> bool:
        del idempotency_key
        return False

    async def mark_seen(
        self,
        *,
        idempotency_key: NotBlankStr,
        claim_id: NotBlankStr,
        now: object,
        ttl_seconds: float,
    ) -> bool:
        del idempotency_key, claim_id, now, ttl_seconds
        return True

    async def prune_expired(self, now: object) -> int:
        del now
        return 0


class _FakeProjectCostClaimSeenRepository:
    """Minimal ProjectCostClaimSeenRepository conforming to the protocol shape."""

    async def has_seen(
        self,
        *,
        claim_id: NotBlankStr,
    ) -> bool:
        del claim_id
        return False

    async def mark_seen(
        self,
        *,
        claim_id: NotBlankStr,
        project_id: NotBlankStr,
        now: object,
        ttl_seconds: float,
    ) -> bool:
        del claim_id, project_id, now, ttl_seconds
        return True

    async def prune_expired(self, now: object) -> int:
        del now
        return 0


class _FakeIdempotencyRepository:
    """Minimal IdempotencyRepository conforming to the protocol shape."""

    async def claim(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        ttl_seconds: int,
        now: object,
        request_fingerprint: str | None = None,
    ) -> IdempotencyClaim:
        del scope, key, ttl_seconds, now, request_fingerprint
        return IdempotencyClaim(
            outcome=IdempotencyOutcome.FRESH,
            claim_token="fake-token",
        )

    async def complete(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        response_body: str,
        response_hash: str,
        claim_token: str,
    ) -> bool:
        del scope, key, response_body, response_hash, claim_token
        return True

    async def fail(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        claim_token: str,
    ) -> bool:
        del scope, key, claim_token
        return True

    async def get(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
    ) -> IdempotencyRecord | None:
        del scope, key
        return None

    async def cleanup_expired(self, now: object) -> int:
        del now
        return 0


class _FakePrincipleOverrideRepository:
    """Minimal PrincipleOverrideRepository conforming to the protocol shape."""

    async def save(self, entity: object) -> None:
        del entity

    async def get(self, entity_id: NotBlankStr) -> PrincipleOverride | None:
        del entity_id
        return None

    async def delete(self, entity_id: NotBlankStr) -> bool:
        del entity_id
        return False

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[PrincipleOverride, ...]:
        del limit, offset
        return ()


class _FakeAppendOnlyRepository:
    """Minimal AppendOnlyRepository conforming to the protocol shape."""

    async def append(self, event: object, /) -> None:
        del event

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[object, ...]:
        del filter_spec, limit, offset
        return ()

    async def purge_before(self, threshold: object, /) -> int:
        del threshold
        return 0


class _FakeAuditChainRepository(_FakeAppendOnlyRepository):
    """Minimal AuditChainRepository: append-only plus ``get_tail``."""

    async def get_tail(self) -> object | None:
        return None


class _FakeHiringRequestRepository:
    """Minimal HiringRequestRepository conforming to the protocol shape."""

    async def save(self, entity: object, /) -> None:
        del entity

    async def get(self, entity_id: NotBlankStr, /) -> object | None:
        del entity_id
        return None

    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        del entity_id
        return False

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[object, ...]:
        del limit, offset
        return ()

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[object, ...]:
        del filter_spec, limit, offset
        return ()

    async def count(self, filter_spec: object) -> int:
        del filter_spec
        return 0


class _FakeBackend:
    @property
    def kind(self) -> Literal["sqlite", "postgres"]:
        return "sqlite"

    @property
    def config(self) -> SQLiteConfig | PostgresConfig:
        return SQLiteConfig(path=":memory:")

    @property
    def supports_conversational_approvals(self) -> bool:
        return False

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

    @asynccontextmanager
    async def write_context(self) -> AsyncIterator[None]:
        yield

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
    def lifecycle_transitions(self) -> _FakeLifecycleTransitionRepository:
        return _FakeLifecycleTransitionRepository()

    @property
    def deleted_entities(self) -> _FakeDeletedEntityRepository:
        return _FakeDeletedEntityRepository()

    @property
    def task_metrics(self) -> _FakeTaskMetricRepository:
        return _FakeTaskMetricRepository()

    @property
    def parked_contexts(self) -> _FakeParkedContextRepository:
        return _FakeParkedContextRepository()

    @property
    def resume_intents(self) -> _FakeResumeIntentRepository:
        return _FakeResumeIntentRepository()

    @property
    def audit_entries(self) -> _FakeAuditRepository:
        return _FakeAuditRepository()

    @property
    def provider_audit_events(self) -> _FakeProviderAuditRepo:
        return _FakeProviderAuditRepo()

    @property
    def preset_overrides(self) -> _FakePresetOverrideRepo:
        return _FakePresetOverrideRepo()

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
    def flight_recorder_frames(self) -> _FakeFlightRecorderRepository:
        return _FakeFlightRecorderRepository()

    @property
    def red_team_reports(self) -> _FakeRedTeamReportArchiveRepository:
        return _FakeRedTeamReportArchiveRepository()

    @property
    def completion_oracle_reports(
        self,
    ) -> _FakeCompletionOracleReportArchiveRepository:
        return _FakeCompletionOracleReportArchiveRepository()

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
    def plans(self) -> _FakePlanRepository:
        return _FakePlanRepository()

    @property
    def plan_comments(self) -> _FakePlanItemCommentRepository:
        return _FakePlanItemCommentRepository()

    @property
    def project_workspaces(self) -> _FakeProjectWorkspaceRepository:
        return _FakeProjectWorkspaceRepository()

    @property
    def codebase_structure_maps(self) -> object:
        # ``PersistenceBackend`` is ``@runtime_checkable``; the isinstance
        # conformance check only verifies the attribute exists.
        return object()

    @property
    def project_environments(self) -> _FakeProjectEnvironmentRepository:
        # Concrete fake so the protocol-conformance suite catches drift
        # between ``ProjectEnvironmentRepository`` and the backend's
        # exposure path.
        return _FakeProjectEnvironmentRepository()

    @property
    def project_docs(self) -> object:
        # Living-doc metadata repo. ``PersistenceBackend`` is
        # ``@runtime_checkable``, which only verifies attribute presence;
        # returning ``object()`` is enough for the isinstance check.
        return object()

    @property
    def project_brain(self) -> object:
        # Long-horizon project-brain repo; attribute presence is all the
        # runtime_checkable protocol verifies.
        return object()

    @property
    def knowledge_sources(self) -> _FakeKnowledgeSourceRepository:
        # Concrete fake (not ``object()``) so backend-routed type checks
        # actually exercise the protocol contract and catch drift between
        # ``KnowledgeSourceRepository`` and the backend's exposure path.
        return _FakeKnowledgeSourceRepository()

    @property
    def knowledge_provenance(self) -> _FakeChunkProvenanceRepository:
        # Concrete fake so the protocol-conformance suite catches drift
        # between the bespoke ADR-0001 D7 methods (``get_many``,
        # ``delete_by_source``) and the backend's exposure path.
        return _FakeChunkProvenanceRepository()

    @property
    def research_runs(self) -> InMemoryResearchRunRepository:
        # Concrete fake so the protocol-conformance suite catches drift
        # between ``ResearchRunRepository`` and the backend's exposure path.
        return InMemoryResearchRunRepository()

    @property
    def deliverable_receipts(self) -> InMemoryDeliverableReceiptRepository:
        return InMemoryDeliverableReceiptRepository()

    @property
    def knowledge_usage_records(self) -> InMemoryKnowledgeUsageRecordRepository:
        return InMemoryKnowledgeUsageRecordRepository()

    @property
    def code_execution_records(self) -> InMemoryCodeExecutionRecordRepository:
        return InMemoryCodeExecutionRecordRepository()

    @property
    def evaluation_reports(self) -> InMemoryEvaluationReportRepository:
        return InMemoryEvaluationReportRepository()

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
        # verifies that the ``workflow_versions`` attribute exists,
        # not that its value implements the full
        # ``VersionRepository[WorkflowDefinition]`` interface.
        # Returning a bare ``object()`` is enough to satisfy the
        # isinstance conformance test in this module.
        return object()

    @property
    def risk_overrides(self) -> object:
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
    def identity_versions(self) -> object:
        return object()

    @property
    def budget_config_versions(self) -> object:
        return object()

    @property
    def company_versions(self) -> object:
        return object()

    @property
    def role_versions(self) -> object:
        return object()

    @property
    def circuit_breaker_state(self) -> object:
        return object()

    @property
    def model_tool_call_signals(self) -> _FakeModelToolCallSignalRepository:
        # Real fake repo (not ``object()``) so backend-level contract access
        # actually exposes a ``ModelToolCallSignalRepository``-shaped object.
        return _FakeModelToolCallSignalRepository()

    @property
    def model_capability_scores(self) -> _FakeModelCapabilityScoreRepository:
        # Real fake repo for the same reason as the sibling above.
        return _FakeModelCapabilityScoreRepository()

    @property
    def capability_source_statuses(self) -> _FakeCapabilitySourceStatusRepository:
        # Real fake repo for the same reason as the sibling above.
        return _FakeCapabilitySourceStatusRepository()

    @property
    def provider_failover_events(self) -> _FakeProviderFailoverEventRepository:
        # Real fake repo for the same reason as the sibling above.
        return _FakeProviderFailoverEventRepository()

    @property
    def tracked_containers(self) -> object:
        return object()

    @property
    def background_jobs(self) -> object:
        return object()

    @property
    def connections(self) -> object:
        return object()

    @property
    def connection_secrets(self) -> object:
        return object()

    @property
    def oauth_states(self) -> object:
        return object()

    @property
    def webhook_receipts(self) -> object:
        return object()

    @property
    def custom_rules(self) -> object:
        return None

    @property
    def sessions(self) -> object:
        return None

    @property
    def refresh_tokens(self) -> object:
        return None

    @property
    def idempotency_keys(self) -> _FakeIdempotencyRepository:
        return _FakeIdempotencyRepository()

    @property
    def seen_claims(self) -> _FakeSeenClaimsRepository:
        # Real backends return a concrete repository. Returning ``None``
        # here would hide a regression in either backend's wiring of
        # ``seen_claims`` from the protocol-compliance suite.
        return _FakeSeenClaimsRepository()

    @property
    def project_cost_claim_seen(self) -> _FakeProjectCostClaimSeenRepository:
        return _FakeProjectCostClaimSeenRepository()

    @property
    def principle_overrides(self) -> _FakePrincipleOverrideRepository:
        return _FakePrincipleOverrideRepository()

    @property
    def hiring_requests(self) -> _FakeHiringRequestRepository:
        return _FakeHiringRequestRepository()

    @property
    def agent_contributions(self) -> _FakeAppendOnlyRepository:
        return _FakeAppendOnlyRepository()

    @property
    def audit_chain_entries(self) -> _FakeAuditChainRepository:
        return _FakeAuditChainRepository()

    @property
    def mcp_installations(self) -> object:
        return None

    @property
    def org_facts(self) -> object:
        return None

    @property
    def memory_vectors(self) -> object:
        return None

    @property
    def ontology_entities(self) -> object:
        return None

    @property
    def ontology_drift(self) -> object:
        return None

    @property
    def project_cost_aggregates(self) -> object:
        # Same contract as the real backends: raise rather than silently
        # returning ``None`` so misuse of this fake fails at the
        # protocol boundary instead of deep inside a service.
        msg = "project_cost_aggregates not supported by the protocol-compliance fake"
        raise NotImplementedError(msg)

    @property
    def fine_tune_checkpoints(self) -> object:
        # Match the contract of the real backends: if the backend does
        # not implement fine-tune persistence it must raise, not silently
        # hand back ``None`` that would fail later with a NoneType error
        # somewhere deep in a service call.
        msg = "fine_tune_checkpoints not supported by the protocol-compliance fake"
        raise NotImplementedError(msg)

    @property
    def fine_tune_runs(self) -> object:
        msg = "fine_tune_runs not supported by the protocol-compliance fake"
        raise NotImplementedError(msg)

    def build_lockouts(self, auth_config: object) -> object:
        return None

    def build_ontology_versioning(self) -> object:
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

    def test_fake_plan_repo_is_plan_repository(self) -> None:
        backend = _FakeBackend()
        assert isinstance(backend.plans, PlanRepository)
        assert isinstance(_FakePlanRepository(), PlanRepository)

    def test_fake_plan_comment_repo_is_plan_comment_repository(self) -> None:
        backend = _FakeBackend()
        assert isinstance(backend.plan_comments, PlanItemCommentRepository)
        assert isinstance(_FakePlanItemCommentRepository(), PlanItemCommentRepository)

    def test_fake_cost_repo_is_cost_record_repository(self) -> None:
        assert isinstance(_FakeCostRecordRepository(), CostRecordRepository)

    def test_fake_message_repo_is_message_repository(self) -> None:
        assert isinstance(_FakeMessageRepository(), MessageRepository)

    def test_fake_idempotency_repo_is_idempotency_repository(self) -> None:
        # Assert through the backend so a regression that nulls or
        # mistypes ``_FakeBackend.idempotency_keys`` fails here, not
        # only the standalone-class check that would happily pass even
        # if the backend forgot to wire the repo at all.
        backend = _FakeBackend()
        assert isinstance(backend.idempotency_keys, IdempotencyRepository)
        assert isinstance(_FakeIdempotencyRepository(), IdempotencyRepository)

    def test_fake_seen_claims_repo_is_seen_claims_repository(self) -> None:
        # Same backend-routed assertion as ``idempotency_keys``:
        # catches a regression where the property returns ``None`` and
        # silently hides backend wiring drift.
        backend = _FakeBackend()
        assert isinstance(backend.seen_claims, SeenClaimsRepository)
        assert isinstance(_FakeSeenClaimsRepository(), SeenClaimsRepository)

    def test_fake_cost_claim_seen_repo_is_cost_claim_seen_repository(self) -> None:
        backend = _FakeBackend()
        assert isinstance(
            backend.project_cost_claim_seen, ProjectCostClaimSeenRepository
        )
        assert isinstance(
            _FakeProjectCostClaimSeenRepository(), ProjectCostClaimSeenRepository
        )

    def test_fake_principle_overrides_repo_is_principle_override_repository(
        self,
    ) -> None:
        # Same backend-routed assertion: catches a regression that
        # swaps the property back to ``None`` or removes it.
        backend = _FakeBackend()
        assert isinstance(backend.principle_overrides, PrincipleOverrideRepository)
        assert isinstance(
            _FakePrincipleOverrideRepository(),
            PrincipleOverrideRepository,
        )

    def test_fake_lifecycle_repo_is_lifecycle_event_repository(self) -> None:
        assert isinstance(_FakeLifecycleEventRepository(), LifecycleEventRepository)

    def test_fake_task_metric_repo_is_task_metric_repository(self) -> None:
        assert isinstance(_FakeTaskMetricRepository(), TaskMetricRepository)

    def test_fake_parked_context_repo_is_parked_context_repository(
        self,
    ) -> None:
        assert isinstance(
            _FakeParkedContextRepository(),
            ParkedContextRepository,
        )

    def test_fake_resume_intent_repo_is_resume_intent_repository(self) -> None:
        # Assert through the backend too, so a regression that nulls or
        # mistypes ``_FakeBackend.resume_intents`` fails here rather than
        # slipping past on the standalone-class check alone.
        backend = _FakeBackend()
        assert isinstance(backend.resume_intents, ResumeIntentRepository)
        assert isinstance(
            _FakeResumeIntentRepository(),
            ResumeIntentRepository,
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

    def test_fake_flight_recorder_repo_is_flight_recorder_repository(self) -> None:
        # Backend-routed assertion mirrors the idempotency / seen-claims
        # pattern above: a regression that swaps the property to ``None``
        # or removes ``flight_recorder_frames`` from ``_FakeBackend``
        # fails here, not only on the standalone-class check that would
        # happily pass even if the backend forgot to wire the repo.
        backend = _FakeBackend()
        assert isinstance(backend.flight_recorder_frames, FlightRecorderFrameRepository)
        assert isinstance(
            _FakeFlightRecorderRepository(), FlightRecorderFrameRepository
        )

    def test_fake_red_team_report_repo_is_archive_repository(self) -> None:
        # Backend-routed assertion mirrors the flight-recorder pattern above:
        # a regression that swaps the property to ``None`` or removes
        # ``red_team_reports`` from ``_FakeBackend`` fails here, not only on
        # the standalone-class check.
        backend = _FakeBackend()
        assert isinstance(backend.red_team_reports, RedTeamReportArchiveRepository)
        assert isinstance(
            _FakeRedTeamReportArchiveRepository(), RedTeamReportArchiveRepository
        )

    def test_fake_completion_oracle_report_repo_is_archive_repository(self) -> None:
        # Backend-routed assertion, twin of the red-team check above.
        backend = _FakeBackend()
        assert isinstance(
            backend.completion_oracle_reports, CompletionOracleReportArchiveRepository
        )
        assert isinstance(
            _FakeCompletionOracleReportArchiveRepository(),
            CompletionOracleReportArchiveRepository,
        )

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

    def test_fake_project_workspace_repo_is_project_workspace_repository(
        self,
    ) -> None:
        # Route through the backend property AND construct the fake
        # directly, mirroring the pattern used for other repositories
        # so a future drift in either path is caught.
        backend = _FakeBackend()
        assert isinstance(backend.project_workspaces, ProjectWorkspaceRepository)
        assert isinstance(
            _FakeProjectWorkspaceRepository(),
            ProjectWorkspaceRepository,
        )

    def test_fake_project_environment_repo_is_project_environment_repository(
        self,
    ) -> None:
        # Route through the backend property AND construct the fake
        # directly so drift in either path is caught.
        backend = _FakeBackend()
        assert isinstance(backend.project_environments, ProjectEnvironmentRepository)
        assert isinstance(
            _FakeProjectEnvironmentRepository(),
            ProjectEnvironmentRepository,
        )

    def test_fake_ssrf_violation_repo_is_ssrf_violation_repository(self) -> None:
        # Route through the backend property so the test exercises the
        # contract-fidelity path (backend.ssrf_violations -> repo) rather
        # than constructing the fake directly -- catches regressions where
        # ``_FakeBackend.ssrf_violations`` drifts back to ``object()``.
        backend = _FakeBackend()
        assert isinstance(backend.ssrf_violations, SsrfViolationRepository)

    def test_fake_model_tool_call_signals_repo_is_signal_repository(self) -> None:
        # Route through the backend property so a regression that drifts
        # ``model_tool_call_signals`` back to ``object()`` surfaces here.
        backend = _FakeBackend()
        assert isinstance(
            backend.model_tool_call_signals, ModelToolCallSignalRepository
        )

    def test_fake_model_capability_scores_repo_is_score_repository(self) -> None:
        # Same routing as the sibling above, for the same reason.
        backend = _FakeBackend()
        assert isinstance(
            backend.model_capability_scores, ModelCapabilityScoreRepository
        )

    def test_fake_capability_source_statuses_repo_is_status_repository(self) -> None:
        # Same routing as the sibling above, for the same reason.
        backend = _FakeBackend()
        assert isinstance(
            backend.capability_source_statuses, CapabilitySourceStatusRepository
        )

    def test_fake_provider_failover_events_repo_is_append_only(self) -> None:
        # Same routing as the sibling above, for the same reason.
        backend = _FakeBackend()
        assert isinstance(
            backend.provider_failover_events, ProviderFailoverEventRepository
        )

    def test_fake_knowledge_sources_repo_is_knowledge_source_repository(
        self,
    ) -> None:
        # Route through the backend property so a regression that drifts
        # ``knowledge_sources`` back to ``object()`` surfaces here.
        backend = _FakeBackend()
        assert isinstance(backend.knowledge_sources, KnowledgeSourceRepository)

    def test_fake_knowledge_provenance_repo_is_chunk_provenance_repository(
        self,
    ) -> None:
        backend = _FakeBackend()
        assert isinstance(backend.knowledge_provenance, ChunkProvenanceRepository)

    def test_fake_research_runs_repo_is_research_run_repository(self) -> None:
        # Route through the backend property so a regression that drifts
        # ``research_runs`` back to ``object()`` surfaces here.
        backend = _FakeBackend()
        assert isinstance(backend.research_runs, ResearchRunRepository)

    def test_fake_deliverable_receipts_repo_is_deliverable_receipt_repository(
        self,
    ) -> None:
        # Route through the backend property so a regression that drifts
        # ``deliverable_receipts`` away from the protocol surfaces here.
        backend = _FakeBackend()
        assert isinstance(backend.deliverable_receipts, DeliverableReceiptRepository)

    def test_fake_knowledge_usage_records_repo_is_knowledge_usage_repository(
        self,
    ) -> None:
        backend = _FakeBackend()
        assert isinstance(
            backend.knowledge_usage_records, KnowledgeUsageRecordRepository
        )

    def test_fake_code_execution_records_repo_is_code_execution_repository(
        self,
    ) -> None:
        backend = _FakeBackend()
        assert isinstance(backend.code_execution_records, CodeExecutionRecordRepository)

    def test_fake_evaluation_reports_repo_is_evaluation_report_repository(
        self,
    ) -> None:
        backend = _FakeBackend()
        assert isinstance(backend.evaluation_reports, EvaluationReportRepository)

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
