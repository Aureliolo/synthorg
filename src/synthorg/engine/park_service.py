"""Park/resume service for agent execution contexts.

Creates ``ParkedContext`` objects by serializing an ``AgentContext`` to JSON,
and restores them by deserializing. Actual persistence (store / delete) is the
responsibility of the calling code via the ``ParkedContextRepository``.

Lives in ``engine`` (not ``security``) because it operates directly on the
engine-owned ``AgentContext``: the (de)serializer belongs alongside the type it
serialises, and its consumers (the approval gate, the engine factories, the boot
wiring) are all engine/API concerns. The serialized form, ``ParkedContext``,
lives in the lighter ``execution`` leaf so the persistence layer can name it
without pulling ``engine``.
"""

import copy
from datetime import UTC, datetime

from pydantic import ValidationError

from synthorg.core.types import NotBlankStr
from synthorg.engine.context import AgentContext
from synthorg.execution.parked_context import ParkedContext
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.timeout import (
    TIMEOUT_CONTEXT_PARKED,
    TIMEOUT_CONTEXT_RESUMED,
)

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
            logger.warning(
                TIMEOUT_CONTEXT_PARKED,
                agent_id=agent_id,
                task_id=task_id,
                approval_id=approval_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
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
        try:
            context = AgentContext.model_validate_json(parked.context_json)
        except (ValidationError, ValueError) as exc:
            logger.warning(
                TIMEOUT_CONTEXT_RESUMED,
                parked_id=parked.id,
                agent_id=parked.agent_id,
                approval_id=parked.approval_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
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
