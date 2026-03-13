"""Park/resume service for agent execution contexts.

Creates ``ParkedContext`` objects by serializing an ``AgentContext`` to
JSON, and restores them by deserializing.  Actual persistence (store /
delete) is the responsibility of the calling code via the
``ParkedContextRepository``.
"""

import copy
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ai_company.core.types import NotBlankStr  # noqa: TC001
from ai_company.observability import get_logger

if TYPE_CHECKING:
    from ai_company.engine.context import AgentContext
from ai_company.observability.events.timeout import (
    TIMEOUT_CONTEXT_PARKED,
    TIMEOUT_CONTEXT_RESUMED,
)
from ai_company.security.timeout.parked_context import ParkedContext

logger = get_logger(__name__)


class ParkService:
    """Handles creating and deserializing parked agent execution contexts.

    The ``park`` method serializes an ``AgentContext`` into a
    ``ParkedContext`` for the caller to persist.  The ``resume`` method
    deserializes a ``ParkedContext`` back into an ``AgentContext``.
    """

    def park(
        self,
        *,
        context: AgentContext,
        approval_id: NotBlankStr,
        agent_id: NotBlankStr,
        task_id: NotBlankStr | None = None,
        metadata: dict[str, str] | None = None,
    ) -> ParkedContext:
        """Serialize and create a ``ParkedContext`` from an agent context.

        Args:
            context: The agent context to park.
            approval_id: The approval item that triggered parking.
            agent_id: Agent identifier.
            task_id: Task identifier, or ``None`` for taskless agents.
            metadata: Optional additional metadata.

        Returns:
            A ``ParkedContext`` ready for persistence.

        Raises:
            ValueError: If the agent context cannot be serialized.
        """
        try:
            context_json = context.model_dump_json()
        except (ValueError, TypeError) as exc:
            logger.exception(
                TIMEOUT_CONTEXT_PARKED,
                agent_id=agent_id,
                task_id=task_id,
                approval_id=approval_id,
                error=str(exc),
                note="Failed to serialize agent context",
            )
            msg = f"Failed to serialize agent context for agent {agent_id!r}"
            raise ValueError(msg) from exc

        parked = ParkedContext(
            execution_id=str(context.execution_id),
            agent_id=agent_id,
            task_id=task_id,
            approval_id=approval_id,
            parked_at=datetime.now(UTC),
            context_json=context_json,
            metadata=copy.deepcopy(metadata) if metadata else {},
        )

        # Validate that metadata IDs match serialized context IDs.
        if parked.agent_id != agent_id:
            msg = (
                f"ParkedContext agent_id {parked.agent_id!r} does not "
                f"match provided agent_id {agent_id!r}"
            )
            raise ValueError(msg)
        if parked.task_id != task_id:
            msg = (
                f"ParkedContext task_id {parked.task_id!r} does not "
                f"match provided task_id {task_id!r}"
            )
            raise ValueError(msg)

        logger.info(
            TIMEOUT_CONTEXT_PARKED,
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

        Raises:
            ValueError: If the parked context cannot be deserialized.
        """
        from ai_company.engine.context import AgentContext  # noqa: PLC0415

        try:
            context = AgentContext.model_validate_json(parked.context_json)
        except (ValidationError, ValueError) as exc:
            logger.exception(
                TIMEOUT_CONTEXT_RESUMED,
                parked_id=parked.id,
                agent_id=parked.agent_id,
                approval_id=parked.approval_id,
                error=str(exc),
                note="Failed to deserialize parked agent context",
            )
            msg = (
                f"Failed to resume parked context {parked.id!r} "
                f"for agent {parked.agent_id!r}"
            )
            raise ValueError(msg) from exc

        logger.info(
            TIMEOUT_CONTEXT_RESUMED,
            parked_id=parked.id,
            agent_id=parked.agent_id,
        )
        return context
