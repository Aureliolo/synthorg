"""AgentState repository protocol."""

from typing import Protocol, override, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_state import AgentRuntimeState
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, IdKeyedRepository


@runtime_checkable
class AgentStateRepository(
    IdKeyedRepository["AgentRuntimeState", NotBlankStr],
    Protocol,
):
    """CRUD + query interface for agent runtime state persistence.

    Composes :class:`IdKeyedRepository` (ADR-0001). Bespoke per D7:
    :meth:`get_active` filters non-idle agents and orders by
    ``last_activity_at`` DESC, which the generic ``list_items`` cannot
    express and which dashboard live views poll on the hot path; and
    :meth:`save_if_execution`, whose guard has to be evaluated by the same
    statement that writes or it is not a guard at all.
    """

    async def save_if_execution(
        self,
        entity: AgentRuntimeState,
        /,
        *,
        expected_execution_id: str,
    ) -> bool:
        """Upsert *entity* only while the row still belongs to an execution.

        The row is keyed by agent while an agent can hold more than one
        dispatch, so clearing it unconditionally blanks a sibling's live row.
        Reading the row and then saving cannot express that: the sibling can
        claim the agent in the gap, and the write that follows destroys a
        state the read said was safe to overwrite. The comparison therefore
        belongs in the write statement, where the row is already locked.

        A row that names no execution is writable: nothing owns it. An absent
        row is writable too, because there is nothing to overwrite.

        Args:
            entity: The agent runtime state to persist.
            expected_execution_id: The execution the caller believes holds the
                row. The write lands only when the stored ``execution_id``
                equals this or is ``NULL``.

        Returns:
            ``True`` when the row was written, ``False`` when a different
            execution holds it and the write was declined.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def save(self, entity: AgentRuntimeState, /) -> None:
        """Upsert an agent runtime state by ``agent_id``.

        Args:
            entity: The agent runtime state to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr, /) -> AgentRuntimeState | None:
        """Retrieve an agent runtime state by agent ID.

        Args:
            entity_id: The agent identifier.

        Returns:
            The agent state, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[AgentRuntimeState, ...]:
        """List all agent runtime states in ``agent_id`` order.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Agent states in ascending ``agent_id`` order.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get_active(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[AgentRuntimeState, ...]:
        """Retrieve a bounded page of non-idle agent states.

        Returns states where ``status != 'idle'``, ordered by
        ``last_activity_at`` descending then ``agent_id`` ascending
        (the stable secondary key makes paging deterministic when
        activity timestamps tie). Callers that need every active
        state drain via
        :func:`synthorg.persistence._shared.collect_all`.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            A page of active agent states.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Delete an agent runtime state by agent ID.

        Args:
            entity_id: The agent identifier.

        Returns:
            ``True`` if deleted, ``False`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
