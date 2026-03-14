"""Tests for OffboardingService."""

import asyncio
from typing import Any

import pytest

from synthorg.communication.channel import Channel
from synthorg.communication.message import Message
from synthorg.core.enums import AgentStatus, TaskStatus
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.hr.archival_protocol import ArchivalResult
from synthorg.hr.errors import (
    AgentNotFoundError,
    MemoryArchivalError,
    OffboardingError,
    TaskReassignmentError,
)
from synthorg.hr.models import OffboardingRecord
from synthorg.hr.offboarding_service import OffboardingService
from synthorg.hr.registry import AgentRegistryService
from tests.unit.hr.conftest import (
    make_agent_identity,
    make_firing_request,
    make_task,
)

# ── Fake Collaborators ─────────────────────────────────────────


class FakeTaskRepository:
    """In-memory task repository for offboarding tests."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    async def save(self, task: Task) -> None:
        self._tasks[task.id] = task

    async def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    async def list_tasks(
        self,
        *,
        status: TaskStatus | None = None,
        assigned_to: str | None = None,
        project: str | None = None,
    ) -> tuple[Task, ...]:
        result = list(self._tasks.values())
        if status is not None:
            result = [t for t in result if t.status == status]
        if assigned_to is not None:
            result = [t for t in result if t.assigned_to == assigned_to]
        if project is not None:
            result = [t for t in result if t.project == project]
        return tuple(result)

    async def delete(self, task_id: str) -> bool:
        return self._tasks.pop(task_id, None) is not None


class FakeReassignmentStrategy:
    """Fake task reassignment strategy that interrupts active tasks."""

    @property
    def name(self) -> str:
        return "fake_return"

    async def reassign(
        self,
        *,
        agent_id: NotBlankStr,
        active_tasks: tuple[Task, ...],
    ) -> tuple[Task, ...]:
        result: list[Task] = []
        for task in active_tasks:
            if task.status in {TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS}:
                updated = task.with_transition(
                    TaskStatus.INTERRUPTED,
                    assigned_to=None,
                )
                result.append(updated)
        return tuple(result)


class FakeArchivalStrategy:
    """Fake archival strategy that returns a default result."""

    @property
    def name(self) -> str:
        return "fake_archival"

    async def archive(
        self,
        *,
        agent_id: NotBlankStr,
        memory_backend: Any,
        archival_store: Any,
        org_memory_backend: Any = None,
        agent_seniority: Any = None,
    ) -> ArchivalResult:
        return ArchivalResult(
            agent_id=agent_id,
            total_archived=3,
            promoted_to_org=1,
            hot_store_cleaned=True,
            strategy_name=NotBlankStr(self.name),
        )


class FakeMessageBus:
    """In-memory message bus that records published messages."""

    def __init__(self) -> None:
        self._running = False
        self._channels: list[Channel] = []
        self.published: list[Message] = []

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def publish(self, message: Message) -> None:
        self.published.append(message)

    async def send_direct(self, message: Message, *, recipient: str) -> None:
        pass

    async def subscribe(self, channel_name: str, subscriber_id: str) -> Any:
        return None

    async def unsubscribe(self, channel_name: str, subscriber_id: str) -> None:
        pass

    async def receive(
        self,
        channel_name: str,
        subscriber_id: str,
        *,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> Any:
        if timeout is not None:
            await asyncio.sleep(min(timeout, 0.01))
        return None

    async def create_channel(self, channel: Channel) -> Channel:
        self._channels.append(channel)
        return channel

    async def get_channel(self, channel_name: str) -> Channel:
        for ch in self._channels:
            if ch.name == channel_name:
                return ch
        msg = f"Channel {channel_name!r} not found"
        raise ValueError(msg)

    async def list_channels(self) -> tuple[Channel, ...]:
        return tuple(self._channels)

    async def get_channel_history(
        self,
        channel_name: str,
        *,
        limit: int | None = None,
    ) -> tuple[Message, ...]:
        return ()


# ── Tests ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestOffboardingServiceFullPipeline:
    """OffboardingService.offboard full pipeline tests."""

    async def test_full_pipeline(
        self,
        registry: AgentRegistryService,
    ) -> None:
        """Full offboarding: reassign + archive + notify + terminate."""
        identity = make_agent_identity(name="departing-agent")
        await registry.register(identity)
        agent_id = str(identity.id)

        task_repo = FakeTaskRepository()
        task = make_task(
            task_id="task-active",
            status=TaskStatus.ASSIGNED,
            assigned_to=agent_id,
        )
        await task_repo.save(task)

        bus = FakeMessageBus()
        await bus.start()

        service = OffboardingService(
            registry=registry,
            reassignment_strategy=FakeReassignmentStrategy(),
            archival_strategy=FakeArchivalStrategy(),
            memory_backend=object(),  # type: ignore[arg-type]
            archival_store=object(),  # type: ignore[arg-type]
            message_bus=bus,
            task_repository=task_repo,
        )

        request = make_firing_request(
            agent_id=agent_id,
            agent_name=str(identity.name),
        )
        record = await service.offboard(request)

        assert len(record.tasks_reassigned) == 1
        assert record.team_notification_sent is True
        assert len(bus.published) == 1

        # Agent should be TERMINATED.
        agent = await registry.get(agent_id)
        assert agent is not None
        assert agent.status == AgentStatus.TERMINATED

    async def test_no_tasks_empty_reassignment(
        self,
        registry: AgentRegistryService,
    ) -> None:
        identity = make_agent_identity(name="idle-agent")
        await registry.register(identity)
        agent_id = str(identity.id)

        task_repo = FakeTaskRepository()  # empty

        service = OffboardingService(
            registry=registry,
            reassignment_strategy=FakeReassignmentStrategy(),
            archival_strategy=FakeArchivalStrategy(),
            task_repository=task_repo,
        )

        request = make_firing_request(
            agent_id=agent_id,
            agent_name=str(identity.name),
        )
        record = await service.offboard(request)
        assert record.tasks_reassigned == ()

    async def test_no_memory_backend_skip_archival(
        self,
        registry: AgentRegistryService,
    ) -> None:
        identity = make_agent_identity(name="no-memory-agent")
        await registry.register(identity)
        agent_id = str(identity.id)

        service = OffboardingService(
            registry=registry,
            reassignment_strategy=FakeReassignmentStrategy(),
            archival_strategy=FakeArchivalStrategy(),
            memory_backend=None,
            archival_store=None,
        )

        request = make_firing_request(
            agent_id=agent_id,
            agent_name=str(identity.name),
        )
        record = await service.offboard(request)
        assert record.org_memories_promoted == 0

    async def test_no_message_bus_skip_notification(
        self,
        registry: AgentRegistryService,
    ) -> None:
        identity = make_agent_identity(name="quiet-agent")
        await registry.register(identity)
        agent_id = str(identity.id)

        service = OffboardingService(
            registry=registry,
            reassignment_strategy=FakeReassignmentStrategy(),
            archival_strategy=FakeArchivalStrategy(),
            message_bus=None,
        )

        request = make_firing_request(
            agent_id=agent_id,
            agent_name=str(identity.name),
        )
        record = await service.offboard(request)
        assert record.team_notification_sent is False

    async def test_agent_not_found_raises(
        self,
        registry: AgentRegistryService,
    ) -> None:
        service = OffboardingService(
            registry=registry,
            reassignment_strategy=FakeReassignmentStrategy(),
            archival_strategy=FakeArchivalStrategy(),
        )

        request = make_firing_request(
            agent_id="nonexistent-agent",
            agent_name="ghost",
        )
        with pytest.raises(AgentNotFoundError, match="not found"):
            await service.offboard(request)

    async def test_no_task_repository_skip_reassignment(
        self,
        registry: AgentRegistryService,
    ) -> None:
        identity = make_agent_identity(name="no-repo-agent")
        await registry.register(identity)
        agent_id = str(identity.id)

        service = OffboardingService(
            registry=registry,
            reassignment_strategy=FakeReassignmentStrategy(),
            archival_strategy=FakeArchivalStrategy(),
            task_repository=None,
        )

        request = make_firing_request(
            agent_id=agent_id,
            agent_name=str(identity.name),
        )
        record = await service.offboard(request)
        assert record.tasks_reassigned == ()

    async def test_reassignment_failure_propagates(
        self,
        registry: AgentRegistryService,
    ) -> None:
        """TaskReassignmentError from strategy -> OffboardingError."""
        identity = make_agent_identity(name="fail-reassign-agent")
        await registry.register(identity)
        agent_id = str(identity.id)

        class FailingReassignmentStrategy:
            @property
            def name(self) -> str:
                return "failing"

            async def reassign(
                self,
                *,
                agent_id: NotBlankStr,
                active_tasks: tuple[Task, ...],
            ) -> tuple[Task, ...]:
                msg = "reassignment boom"
                raise TaskReassignmentError(msg)

        task_repo = FakeTaskRepository()
        task = make_task(
            task_id="task-fail",
            status=TaskStatus.ASSIGNED,
            assigned_to=agent_id,
        )
        await task_repo.save(task)

        service = OffboardingService(
            registry=registry,
            reassignment_strategy=FailingReassignmentStrategy(),
            archival_strategy=FakeArchivalStrategy(),
            task_repository=task_repo,
        )

        request = make_firing_request(
            agent_id=agent_id,
            agent_name=str(identity.name),
        )
        with pytest.raises(OffboardingError, match="reassignment"):
            await service.offboard(request)

    async def test_archival_failure_is_non_fatal(
        self,
        registry: AgentRegistryService,
    ) -> None:
        """MemoryArchivalError from archival strategy is non-fatal."""
        identity = make_agent_identity(name="fail-archival-agent")
        await registry.register(identity)
        agent_id = str(identity.id)

        class FailingArchivalStrategy:
            @property
            def name(self) -> str:
                return "failing_archival"

            async def archive(
                self,
                *,
                agent_id: NotBlankStr,
                memory_backend: Any,
                archival_store: Any,
                org_memory_backend: Any = None,
                agent_seniority: Any = None,
            ) -> ArchivalResult:
                msg = "archival boom"
                raise MemoryArchivalError(msg)

        service = OffboardingService(
            registry=registry,
            reassignment_strategy=FakeReassignmentStrategy(),
            archival_strategy=FailingArchivalStrategy(),
            memory_backend=object(),  # type: ignore[arg-type]
            archival_store=object(),  # type: ignore[arg-type]
        )

        request = make_firing_request(
            agent_id=agent_id,
            agent_name=str(identity.name),
        )
        record = await service.offboard(request)
        assert isinstance(record, OffboardingRecord)
        assert record.org_memories_promoted == 0

    async def test_notification_failure_is_non_fatal(
        self,
        registry: AgentRegistryService,
    ) -> None:
        """Message bus publish failure -> team_notification_sent=False."""
        identity = make_agent_identity(name="fail-notify-agent")
        await registry.register(identity)
        agent_id = str(identity.id)

        class FailingMessageBus(FakeMessageBus):
            async def publish(self, message: Message) -> None:
                msg = "publish boom"
                raise OSError(msg)

        bus = FailingMessageBus()
        await bus.start()

        service = OffboardingService(
            registry=registry,
            reassignment_strategy=FakeReassignmentStrategy(),
            archival_strategy=FakeArchivalStrategy(),
            message_bus=bus,
        )

        request = make_firing_request(
            agent_id=agent_id,
            agent_name=str(identity.name),
        )
        record = await service.offboard(request)
        assert record.team_notification_sent is False
