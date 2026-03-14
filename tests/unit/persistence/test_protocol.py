"""Tests for persistence protocol compliance."""

from typing import TYPE_CHECKING

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.persistence_protocol import (
    CollaborationMetricRepository,
    LifecycleEventRepository,
    TaskMetricRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.repositories import (
    ApiKeyRepository,
    AuditRepository,
    CheckpointRepository,
    CostRecordRepository,
    HeartbeatRepository,
    MessageRepository,
    ParkedContextRepository,
    TaskRepository,
    UserRepository,
)

if TYPE_CHECKING:
    from pydantic import AwareDatetime

    from synthorg.api.auth.models import ApiKey, User
    from synthorg.budget.cost_record import CostRecord
    from synthorg.communication.message import Message
    from synthorg.core.enums import ApprovalRiskLevel, TaskStatus
    from synthorg.core.task import Task
    from synthorg.engine.checkpoint.models import Checkpoint, Heartbeat
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
    ) -> tuple[Task, ...]:
        return ()

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
        agent_id: str | None = None,
        event_type: str | None = None,
        since: str | None = None,
    ) -> tuple[AgentLifecycleEvent, ...]:
        return ()


class _FakeTaskMetricRepository:
    async def save(self, record: TaskMetricRecord) -> None:
        pass

    async def query(
        self,
        *,
        agent_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
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
        agent_id: str | None = None,
        since: str | None = None,
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


class _FakeUserRepository:
    async def save(self, user: User) -> None:
        pass

    async def get(self, user_id: str) -> User | None:
        return None

    async def get_by_username(self, username: str) -> User | None:
        return None

    async def list_users(self) -> tuple[User, ...]:
        return ()

    async def count(self) -> int:
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


class _FakeBackend:
    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def health_check(self) -> bool:
        return True

    async def migrate(self) -> None:
        pass

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

    def test_fake_user_repo_is_user_repository(self) -> None:
        assert isinstance(_FakeUserRepository(), UserRepository)

    def test_fake_api_key_repo_is_api_key_repository(self) -> None:
        assert isinstance(_FakeApiKeyRepository(), ApiKeyRepository)

    def test_fake_checkpoint_repo_is_checkpoint_repository(self) -> None:
        assert isinstance(_FakeCheckpointRepository(), CheckpointRepository)

    def test_fake_heartbeat_repo_is_heartbeat_repository(self) -> None:
        assert isinstance(_FakeHeartbeatRepository(), HeartbeatRepository)
