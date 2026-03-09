"""Tests for persistence protocol compliance."""

from typing import TYPE_CHECKING

import pytest

from ai_company.core.types import NotBlankStr
from ai_company.persistence.protocol import PersistenceBackend
from ai_company.persistence.repositories import (
    CostRecordRepository,
    MessageRepository,
    TaskRepository,
)

if TYPE_CHECKING:
    from ai_company.budget.cost_record import CostRecord
    from ai_company.communication.message import Message
    from ai_company.core.enums import TaskStatus
    from ai_company.core.task import Task


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

    async def aggregate(self, *, agent_id: str | None = None) -> float:
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
