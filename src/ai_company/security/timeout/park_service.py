"""Park/resume service for agent execution contexts.

Serializes an ``AgentContext`` into a ``ParkedContext`` for persistence
when an agent is parked awaiting approval, and deserializes it back
when the approval decision arrives.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ai_company.observability import get_logger

if TYPE_CHECKING:
    from ai_company.engine.context import AgentContext
from ai_company.observability.events.timeout import (
    TIMEOUT_WAITING,
)
from ai_company.security.timeout.parked_context import ParkedContext

logger = get_logger(__name__)


class ParkService:
    """Handles parking and resuming agent execution contexts.

    Parking serializes the full ``AgentContext`` as JSON and stores it
    via the ``ParkedContextRepository``.  Resuming deserializes and
    deletes the parked record.
    """

    def park(
        self,
        *,
        context: AgentContext,
        approval_id: str,
        agent_id: str,
        task_id: str,
        metadata: dict[str, str] | None = None,
    ) -> ParkedContext:
        """Serialize and create a ``ParkedContext`` from an agent context.

        Args:
            context: The agent context to park.
            approval_id: The approval item that triggered parking.
            agent_id: Agent identifier.
            task_id: Task identifier.
            metadata: Optional additional metadata.

        Returns:
            A ``ParkedContext`` ready for persistence.
        """
        context_json = context.model_dump_json()

        parked = ParkedContext(
            execution_id=str(context.execution_id),
            agent_id=agent_id,
            task_id=task_id,
            approval_id=approval_id,
            parked_at=datetime.now(UTC),
            context_json=context_json,
            metadata=metadata or {},
        )

        logger.info(
            TIMEOUT_WAITING,
            parked_id=parked.id,
            agent_id=agent_id,
            task_id=task_id,
            approval_id=approval_id,
        )
        return parked

    def resume(self, parked: ParkedContext) -> AgentContext:
        """Deserialize a ``ParkedContext`` back into an ``AgentContext``.

        Args:
            parked: The parked context to resume.

        Returns:
            The restored ``AgentContext``.
        """
        from ai_company.engine.context import AgentContext  # noqa: PLC0415

        return AgentContext.model_validate_json(parked.context_json)
