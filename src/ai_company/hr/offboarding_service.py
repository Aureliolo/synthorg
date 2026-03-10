"""Offboarding service.

Orchestrates the firing/offboarding pipeline: task reassignment,
memory archival, team notification, and agent termination.
"""

import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ai_company.communication.enums import MessageType
from ai_company.communication.message import Message
from ai_company.core.enums import AgentStatus, TaskStatus
from ai_company.core.types import NotBlankStr
from ai_company.hr.archival_protocol import ArchivalResult, MemoryArchivalStrategy
from ai_company.hr.errors import AgentNotFoundError, OffboardingError
from ai_company.hr.models import FiringRequest, OffboardingRecord
from ai_company.observability import get_logger
from ai_company.observability.events.hr import (
    HR_FIRING_COMPLETE,
    HR_FIRING_INITIATED,
    HR_FIRING_TEAM_NOTIFIED,
)

if TYPE_CHECKING:
    from ai_company.communication.bus_protocol import MessageBus
    from ai_company.hr.reassignment_protocol import TaskReassignmentStrategy
    from ai_company.hr.registry import AgentRegistryService
    from ai_company.memory.consolidation.archival import ArchivalStore
    from ai_company.memory.org.protocol import OrgMemoryBackend
    from ai_company.memory.protocol import MemoryBackend
    from ai_company.persistence.repositories import TaskRepository

logger = get_logger(__name__)


class OffboardingService:
    """Orchestrates the firing/offboarding pipeline.

    Pipeline steps:
        1. Get agent's active tasks.
        2. Reassign via task reassignment strategy.
        3. Archive memory via archival strategy.
        4. Notify team via message bus.
        5. Update agent status to TERMINATED.
        6. Return offboarding record.

    Args:
        registry: Agent registry for status updates.
        reassignment_strategy: Strategy for task reassignment.
        archival_strategy: Strategy for memory archival.
        memory_backend: Optional hot memory store.
        archival_store: Optional cold archival storage.
        org_memory_backend: Optional org memory for promotion.
        message_bus: Optional message bus for notifications.
        task_repository: Optional task repository for queries.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        registry: AgentRegistryService,
        reassignment_strategy: TaskReassignmentStrategy,
        archival_strategy: MemoryArchivalStrategy,
        memory_backend: MemoryBackend | None = None,
        archival_store: ArchivalStore | None = None,
        org_memory_backend: OrgMemoryBackend | None = None,
        message_bus: MessageBus | None = None,
        task_repository: TaskRepository | None = None,
    ) -> None:
        self._registry = registry
        self._reassignment_strategy = reassignment_strategy
        self._archival_strategy = archival_strategy
        self._memory_backend = memory_backend
        self._archival_store = archival_store
        self._org_memory_backend = org_memory_backend
        self._message_bus = message_bus
        self._task_repository = task_repository

    async def offboard(
        self,
        request: FiringRequest,
    ) -> OffboardingRecord:
        """Execute the full offboarding pipeline.

        Args:
            request: The firing request to process.

        Returns:
            Record of the completed offboarding.

        Raises:
            OffboardingError: If the offboarding pipeline fails.
        """
        started_at = datetime.now(UTC)
        agent_id = str(request.agent_id)

        logger.info(
            HR_FIRING_INITIATED,
            agent_id=agent_id,
            reason=request.reason.value,
        )

        # Verify agent exists in registry.
        identity = await self._registry.get(agent_id)
        if identity is None:
            msg = f"Agent {agent_id!r} not found in registry"
            raise AgentNotFoundError(msg)

        # Step 1: Get active tasks and reassign.
        tasks_reassigned: tuple[str, ...] = ()
        if self._task_repository is not None:
            try:
                assigned_tasks = await self._task_repository.list_tasks(
                    assigned_to=NotBlankStr(agent_id),
                )
                active_tasks = tuple(
                    t
                    for t in assigned_tasks
                    if t.status in {TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS}
                )
                interrupted = await self._reassignment_strategy.reassign(
                    agent_id=NotBlankStr(agent_id),
                    active_tasks=active_tasks,
                )
                # Persist interrupted tasks.
                for task in interrupted:
                    await self._task_repository.save(task)
                tasks_reassigned = tuple(t.id for t in interrupted)
            except Exception as exc:
                msg = f"Task reassignment failed for agent {agent_id!r}: {exc}"
                logger.exception(HR_FIRING_INITIATED, agent_id=agent_id, error=msg)
                raise OffboardingError(msg) from exc

        # Step 2: Archive memory.
        archival_result = ArchivalResult(
            agent_id=NotBlankStr(agent_id),
            total_archived=0,
            promoted_to_org=0,
            hot_store_cleaned=True,
            strategy_name=NotBlankStr(self._archival_strategy.name),
        )
        if self._memory_backend is not None and self._archival_store is not None:
            try:
                archival_result = await self._archival_strategy.archive(
                    agent_id=NotBlankStr(agent_id),
                    memory_backend=self._memory_backend,
                    archival_store=self._archival_store,
                    org_memory_backend=self._org_memory_backend,
                )
            except Exception as exc:
                msg = f"Memory archival failed for agent {agent_id!r}: {exc}"
                logger.warning(HR_FIRING_INITIATED, agent_id=agent_id, error=msg)
                # Non-fatal: continue with offboarding.

        # Step 3: Notify team.
        team_notified = False
        if self._message_bus is not None:
            try:
                notification = Message(
                    timestamp=datetime.now(UTC),
                    sender=NotBlankStr("hr-system"),
                    to=NotBlankStr(str(identity.department)),
                    type=MessageType.HR_NOTIFICATION,
                    channel=NotBlankStr(f"dept-{identity.department}"),
                    content=NotBlankStr(
                        f"Agent {identity.name} has been offboarded. "
                        f"Reason: {request.reason.value}."
                    ),
                )
                await self._message_bus.publish(notification)
                team_notified = True
                logger.info(
                    HR_FIRING_TEAM_NOTIFIED,
                    agent_id=agent_id,
                    department=str(identity.department),
                )
            except Exception as exc:
                logger.warning(
                    HR_FIRING_TEAM_NOTIFIED,
                    agent_id=agent_id,
                    error=str(exc),
                )

        # Step 4: Terminate agent.
        with contextlib.suppress(AgentNotFoundError):
            await self._registry.update_status(agent_id, AgentStatus.TERMINATED)

        completed_at = datetime.now(UTC)
        record = OffboardingRecord(
            agent_id=NotBlankStr(agent_id),
            agent_name=identity.name,
            firing_request_id=request.id,
            tasks_reassigned=tasks_reassigned,
            memory_archive_id=None,
            org_memories_promoted=archival_result.promoted_to_org,
            team_notification_sent=team_notified,
            started_at=started_at,
            completed_at=completed_at,
        )

        logger.info(
            HR_FIRING_COMPLETE,
            agent_id=agent_id,
            tasks_reassigned=len(tasks_reassigned),
            memories_archived=archival_result.total_archived,
        )
        return record
