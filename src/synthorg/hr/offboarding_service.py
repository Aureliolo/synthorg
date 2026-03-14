"""Offboarding service.

Orchestrates the firing/offboarding pipeline: task reassignment,
memory archival, team notification, and agent termination.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.communication.enums import MessageType
from synthorg.communication.errors import CommunicationError
from synthorg.communication.message import Message
from synthorg.core.enums import AgentStatus, TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.hr.archival_protocol import ArchivalResult, MemoryArchivalStrategy
from synthorg.hr.errors import (
    AgentNotFoundError,
    MemoryArchivalError,
    OffboardingError,
    TaskReassignmentError,
)
from synthorg.hr.models import FiringRequest, OffboardingRecord
from synthorg.observability import get_logger
from synthorg.observability.events.hr import (
    HR_FIRING_ARCHIVAL_FAILED,
    HR_FIRING_COMPLETE,
    HR_FIRING_INITIATED,
    HR_FIRING_NOTIFICATION_FAILED,
    HR_FIRING_REASSIGNMENT_FAILED,
    HR_FIRING_TEAM_NOTIFIED,
)

if TYPE_CHECKING:
    from synthorg.communication.bus_protocol import MessageBus
    from synthorg.core.agent import AgentIdentity
    from synthorg.hr.reassignment_protocol import TaskReassignmentStrategy
    from synthorg.hr.registry import AgentRegistryService
    from synthorg.memory.consolidation.archival import ArchivalStore
    from synthorg.memory.org.protocol import OrgMemoryBackend
    from synthorg.memory.protocol import MemoryBackend
    from synthorg.persistence.repositories import TaskRepository

logger = get_logger(__name__)


class OffboardingService:
    """Orchestrates the firing/offboarding pipeline.

    Pipeline steps:
        1. Get active tasks and reassign via strategy.
        2. Archive memory via archival strategy.
        3. Notify team via message bus.
        4. Update agent status to TERMINATED and return record.

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
            OffboardingError: If task reassignment fails (fatal).
            AgentNotFoundError: If the agent is not in the registry
                (fatal).

        Note:
            Memory archival and team notification failures are logged
            but non-fatal -- offboarding continues.
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
            logger.warning(HR_FIRING_INITIATED, agent_id=agent_id, error=msg)
            raise AgentNotFoundError(msg)

        # Step 1: Get active tasks and reassign.
        tasks_reassigned = await self._reassign_tasks(agent_id)

        # Step 2: Archive memory.
        archival_result = await self._archive_memory(agent_id, identity)

        # Step 3: Notify team.
        team_notified = await self._notify_team(
            agent_id, identity, request.reason.value
        )

        # Step 4: Terminate agent.
        await self._terminate_agent(agent_id)

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

    async def _reassign_tasks(self, agent_id: str) -> tuple[str, ...]:
        """Reassign active tasks from a departing agent.

        Args:
            agent_id: The departing agent's ID.

        Returns:
            Tuple of reassigned task IDs.

        Raises:
            OffboardingError: If task reassignment fails.
        """
        if self._task_repository is None:
            return ()

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
            return tuple(t.id for t in interrupted)
        except (TaskReassignmentError, OSError, ValueError) as exc:
            msg = f"Task reassignment failed for agent {agent_id!r}: {exc}"
            logger.exception(
                HR_FIRING_REASSIGNMENT_FAILED,
                agent_id=agent_id,
                error=msg,
            )
            raise OffboardingError(msg) from exc

    async def _archive_memory(
        self,
        agent_id: str,
        identity: AgentIdentity,
    ) -> ArchivalResult:
        """Archive agent memories to cold storage.

        Memory archival failure is non-fatal.

        Args:
            agent_id: The departing agent's ID.
            identity: The agent's identity (for seniority).

        Returns:
            Archival result (default if archival was skipped/failed).
        """
        default_result = ArchivalResult(
            agent_id=NotBlankStr(agent_id),
            total_archived=0,
            promoted_to_org=0,
            hot_store_cleaned=False,
            strategy_name=NotBlankStr(self._archival_strategy.name),
        )
        if self._memory_backend is None or self._archival_store is None:
            return default_result

        try:
            return await self._archival_strategy.archive(
                agent_id=NotBlankStr(agent_id),
                memory_backend=self._memory_backend,
                archival_store=self._archival_store,
                org_memory_backend=self._org_memory_backend,
                agent_seniority=identity.level,
            )
        except (MemoryArchivalError, OSError, ValueError) as exc:
            msg = f"Memory archival failed for agent {agent_id!r}: {exc}"
            logger.warning(
                HR_FIRING_ARCHIVAL_FAILED,
                agent_id=agent_id,
                error=msg,
            )
            # Non-fatal: continue with offboarding.
            return default_result

    async def _notify_team(
        self,
        agent_id: str,
        identity: AgentIdentity,
        reason: str,
    ) -> bool:
        """Notify the team about an agent's departure.

        Notification failure is non-fatal.

        Args:
            agent_id: The departing agent's ID.
            identity: The agent's identity.
            reason: The offboarding reason.

        Returns:
            Whether the notification was sent successfully.
        """
        if self._message_bus is None:
            return False

        try:
            notification = Message(
                timestamp=datetime.now(UTC),
                sender=NotBlankStr("hr-system"),
                to=NotBlankStr(str(identity.department)),
                type=MessageType.HR_NOTIFICATION,
                channel=NotBlankStr(f"dept-{identity.department}"),
                content=NotBlankStr(
                    f"Agent {identity.name} has been offboarded. Reason: {reason}."
                ),
            )
            await self._message_bus.publish(notification)
            logger.info(
                HR_FIRING_TEAM_NOTIFIED,
                agent_id=agent_id,
                department=str(identity.department),
            )
        except (OSError, ValueError, RuntimeError, CommunicationError) as exc:
            logger.warning(
                HR_FIRING_NOTIFICATION_FAILED,
                agent_id=agent_id,
                error=str(exc),
            )
            return False
        else:
            return True

    async def _terminate_agent(self, agent_id: str) -> None:
        """Terminate an agent in the registry.

        Args:
            agent_id: The agent to terminate.

        Raises:
            OffboardingError: If termination fails for reasons other
                than the agent not being found.
        """
        try:
            await self._registry.update_status(agent_id, AgentStatus.TERMINATED)
        except AgentNotFoundError:
            logger.warning(
                HR_FIRING_COMPLETE,
                agent_id=agent_id,
                warning="agent_not_found_during_termination",
            )
        except (OSError, ValueError) as exc:
            msg = f"Failed to terminate agent {agent_id!r} in registry: {exc}"
            logger.exception(HR_FIRING_COMPLETE, agent_id=agent_id, error=msg)
            raise OffboardingError(msg) from exc
