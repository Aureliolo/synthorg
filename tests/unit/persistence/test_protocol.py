# mypy: disable-error-code="explicit-any"
"""Tests for persistence protocol compliance.

The ``filter_spec: Any`` annotations on the fake repositories below
match the variance pattern used in the repository protocols themselves:
each repo's ``query`` / ``count`` accepts a domain-specific
``FilterSpec`` BaseModel, and the fakes here are duck-typed conformance
stubs that need to satisfy every such protocol without importing every
``FilterSpec`` class. Importing each concrete spec would bloat this
file beyond the module-size budget; the module-level mypy directive
absorbs the volume cleanly.
"""

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Literal

import pytest

from synthorg.core.auth.roles import HumanRole
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
from synthorg.persistence.message_protocol import MessageRepository
from synthorg.persistence.parked_context_protocol import ParkedContextRepository
from synthorg.persistence.preset_protocol import (
    PersonalityPresetRepository,
)
from synthorg.persistence.principle_override_protocol import (
    PrincipleOverrideRepository,
)
from synthorg.persistence.project_environment_protocol import (
    ProjectEnvironmentRepository,
)
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.persistence.project_workspace_protocol import (
    ProjectWorkspaceRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.research_protocol import ResearchRunRepository
from synthorg.persistence.seen_claims_protocol import SeenClaimsRepository
from synthorg.persistence.settings_protocol import SettingsRepository
from synthorg.persistence.ssrf_violation_protocol import SsrfViolationRepository
from synthorg.persistence.task_protocol import TaskRepository
from synthorg.persistence.training_protocol import (
    TrainingPlanRepository,
    TrainingResultRepository,
)
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
from tests.unit.research._fakes import InMemoryResearchRunRepository

if TYPE_CHECKING:
    from pydantic import AwareDatetime

    from synthorg.budget.cost_record import CostRecord
    from synthorg.communication.message import Message
    from synthorg.core.artifact import Artifact
    from synthorg.core.auth.models import ApiKey, User
    from synthorg.core.project import Project
    from synthorg.core.project_environment import ProjectEnvironment
    from synthorg.core.project_workspace import ProjectWorkspace
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
    from synthorg.security.models import AuditEntry
    from synthorg.security.timeout.parked_context import ParkedContext


class _FakeTaskRepository:
    async def save(self, entity: Task) -> None:
        pass

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
        filter_spec: Any,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Task, ...]:
        del filter_spec, limit, offset
        return ()

    async def count(self, filter_spec: Any) -> int:
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
        filter_spec: Any,
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
    ) -> tuple[Message, ...]:
        return ()

    async def query(
        self,
        filter_spec: Any,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Message, ...]:
        return ()

    async def purge_before(self, threshold: Any) -> int:
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


class _FakeAuditRepository:
    async def append(self, entry: AuditEntry) -> None:
        pass

    async def query(
        self,
        filter_spec: Any,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[AuditEntry, ...]:
        del filter_spec, limit, offset
        return ()

    async def purge_before(self, cutoff: AwareDatetime) -> int:
        del cutoff
        return 0


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
    async def append(self, event: Any) -> None:
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
        filter_spec: Any,
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

    async def purge_before(self, threshold: Any) -> int:
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
    async def append(self, frame: Any) -> None:
        pass

    async def append_many(self, frames: Any) -> None:
        pass

    async def query(
        self,
        filter_spec: Any,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Any, ...]:
        return ()

    async def get_aggregate(self, filter_spec: Any) -> Any:
        from synthorg.persistence.flight_recorder_protocol import (
            FlightRecorderFrameAggregate,
        )

        return FlightRecorderFrameAggregate()

    async def purge_before(self, threshold: Any) -> int:
        return 0


class _FakeCheckpointRepository:
    async def append(self, checkpoint: Checkpoint) -> None:
        pass

    async def query(
        self,
        filter_spec: Any,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Checkpoint, ...]:
        return ()

    async def purge_before(self, threshold: Any) -> int:
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
    async def save(self, entity: Any) -> None:
        del entity

    async def get(self, entity_id: tuple[NotBlankStr, NotBlankStr]) -> Any | None:
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
    ) -> tuple[Any, ...]:
        del limit, offset
        return ()

    async def get_namespace(self, namespace: NotBlankStr) -> tuple[Any, ...]:
        del namespace
        return ()

    async def set_if_unchanged(
        self,
        entity: Any,
        *,
        expected_updated_at: str | None = None,
    ) -> bool:
        del entity, expected_updated_at
        return True

    async def set_many(
        self,
        items: Sequence[Any],
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

    async def update(self, project: Project) -> None:
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
        filter_spec: Any,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Project, ...]:
        del filter_spec, limit, offset
        return ()

    async def count(self, filter_spec: Any) -> int:
        del filter_spec
        return 0

    async def delete(self, entity_id: NotBlankStr) -> bool:
        del entity_id
        return False


class _FakeSsrfViolationRepository:
    async def save(self, entity: Any) -> None:
        pass

    async def get(self, entity_id: NotBlankStr) -> Any | None:
        del entity_id
        return None

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Any, ...]:
        del limit, offset
        return ()

    async def list_violations(
        self,
        *,
        status: Any | None = None,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
    ) -> tuple[Any, ...]:
        del status, limit
        return ()

    async def delete(self, entity_id: NotBlankStr) -> bool:
        del entity_id
        return False

    async def update_status(
        self,
        violation_id: NotBlankStr,
        *,
        status: Any,
        resolved_by: NotBlankStr,
        resolved_at: AwareDatetime,
    ) -> bool:
        del violation_id, status, resolved_by, resolved_at
        return False


class _FakePersonalityPresetRepository:
    async def save(self, entity: Any) -> None:
        del entity

    async def get(self, entity_id: NotBlankStr) -> Any | None:
        del entity_id
        return None

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Any, ...]:
        del limit, offset
        return ()

    async def query(
        self,
        filter_spec: Any,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Any, ...]:
        del filter_spec, limit, offset
        return ()

    async def count(self, filter_spec: Any) -> int:
        del filter_spec
        return 0

    async def delete(self, entity_id: NotBlankStr) -> bool:
        del entity_id
        return False


class _FakeKnowledgeSourceRepository:
    async def save(self, entity: Any) -> None:
        del entity

    async def get(self, entity_id: NotBlankStr) -> Any | None:
        del entity_id
        return None

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Any, ...]:
        del limit, offset
        return ()

    async def query(
        self,
        filter_spec: Any,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Any, ...]:
        del filter_spec, limit, offset
        return ()

    async def count(self, filter_spec: Any) -> int:
        del filter_spec
        return 0

    async def delete(self, entity_id: NotBlankStr) -> bool:
        del entity_id
        return False


class _FakeChunkProvenanceRepository:
    async def save(self, entity: Any) -> None:
        del entity

    async def get(self, entity_id: NotBlankStr) -> Any | None:
        del entity_id
        return None

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Any, ...]:
        del limit, offset
        return ()

    async def query(
        self,
        filter_spec: Any,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Any, ...]:
        del filter_spec, limit, offset
        return ()

    async def count(self, filter_spec: Any) -> int:
        del filter_spec
        return 0

    async def delete(self, entity_id: NotBlankStr) -> bool:
        del entity_id
        return False

    async def get_many(self, chunk_ids: tuple[NotBlankStr, ...]) -> tuple[Any, ...]:
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
        filter_spec: Any,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[WorkflowExecution, ...]:
        del filter_spec, limit, offset
        return ()

    async def count(self, filter_spec: Any) -> int:
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
        now: Any,
        ttl_seconds: float,
    ) -> bool:
        del idempotency_key, claim_id, now, ttl_seconds
        return True

    async def prune_expired(self, now: Any) -> int:
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
        now: Any,
    ) -> IdempotencyClaim:
        del scope, key, ttl_seconds, now
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

    async def cleanup_expired(self, now: Any) -> int:
        del now
        return 0


class _FakePrincipleOverrideRepository:
    """Minimal PrincipleOverrideRepository conforming to the protocol shape."""

    async def save(self, entity: Any) -> None:
        del entity

    async def get(self, entity_id: NotBlankStr) -> Any:
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
    ) -> tuple[Any, ...]:
        del limit, offset
        return ()


class _FakeBackend:
    @property
    def kind(self) -> Literal["sqlite", "postgres"]:
        return "sqlite"

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
        # verifies that the ``workflow_versions`` attribute exists,
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
    def ceremony_scheduler_state(self) -> Any:
        return object()

    @property
    def meeting_cooldown(self) -> Any:
        return object()

    @property
    def tracked_containers(self) -> Any:
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
    def idempotency_keys(self) -> _FakeIdempotencyRepository:
        return _FakeIdempotencyRepository()

    @property
    def seen_claims(self) -> _FakeSeenClaimsRepository:
        # Real backends return a concrete repository. Returning ``None``
        # here would hide a regression in either backend's wiring of
        # ``seen_claims`` from the protocol-compliance suite.
        return _FakeSeenClaimsRepository()

    @property
    def principle_overrides(self) -> _FakePrincipleOverrideRepository:
        return _FakePrincipleOverrideRepository()

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
        # catches a regression that swaps the property back to
        # ``None`` (which historically hid backend wiring drift).
        backend = _FakeBackend()
        assert isinstance(backend.seen_claims, SeenClaimsRepository)
        assert isinstance(_FakeSeenClaimsRepository(), SeenClaimsRepository)

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
    async def save(self, entity: Any) -> None:
        del entity

    async def get(self, entity_id: NotBlankStr) -> Any | None:
        del entity_id
        return None

    async def delete(self, entity_id: NotBlankStr) -> bool:
        del entity_id
        return False

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Any, ...]:
        del limit, offset
        return ()

    async def query(
        self,
        filter_spec: Any,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Any, ...]:
        del filter_spec, limit, offset
        return ()

    async def count(self, filter_spec: Any) -> int:
        del filter_spec
        return 0

    async def latest_pending(self, agent_id: NotBlankStr) -> Any | None:
        del agent_id
        return None

    async def latest_by_agent(self, agent_id: NotBlankStr) -> Any | None:
        del agent_id
        return None

    async def list_by_agent(
        self,
        agent_id: NotBlankStr,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
    ) -> tuple[Any, ...]:
        del agent_id, limit
        return ()


class _FakeTrainingResultRepository:
    async def save(self, entity: Any) -> None:
        del entity

    async def get(self, entity_id: NotBlankStr) -> Any | None:
        del entity_id
        return None

    async def delete(self, entity_id: NotBlankStr) -> bool:
        del entity_id
        return False

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[Any, ...]:
        del limit, offset
        return ()

    async def get_by_plan(self, plan_id: NotBlankStr) -> Any | None:
        del plan_id
        return None

    async def get_latest(self, agent_id: NotBlankStr) -> Any | None:
        del agent_id
        return None
