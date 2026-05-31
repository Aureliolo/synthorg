"""Repository protocol for group-chat participant rosters (#1970).

A :class:`ConversationParticipant` row records one agent's membership
in a ``kind='group'`` conversation. The repository composes
:class:`StatefulRepository` (atomic ``active`` <-> ``removed``
compare-and-set, so the agent-invite consent flow flips membership
without a read-modify-write race) and :class:`FilteredQueryRepository`
(roster lookup scoped to a conversation, optionally by membership
status).

Concrete implementations live in the backend packages
(``synthorg.persistence.sqlite`` / ``synthorg.persistence.postgres``).
All protocols are ``@runtime_checkable``; all methods are ``async``.
"""

from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.enums import ConversationParticipantStatus
from synthorg.meta.chief_of_staff.group_models import ConversationParticipant
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    StatefulRepository,
)


class ConversationParticipantFilterSpec(BaseModel):
    """Filter spec for ``ConversationParticipantRepository.query``.

    ``conversation_id`` scopes to one conversation's roster; ``status``
    narrows to active or removed members. An empty spec matches every
    participant (used only by maintenance sweeps, never by the service).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    conversation_id: NotBlankStr | None = Field(default=None)
    status: ConversationParticipantStatus | None = Field(default=None)


@runtime_checkable
class ConversationParticipantRepository(
    StatefulRepository[
        ConversationParticipant, NotBlankStr, ConversationParticipantStatus
    ],
    FilteredQueryRepository[ConversationParticipant, ConversationParticipantFilterSpec],
    Protocol,
):
    """CRUD + membership CAS + filtered roster query for participants.

    Composes :class:`StatefulRepository` + :class:`FilteredQueryRepository`
    (ADR-0001). No bespoke methods beyond the generic surface.

    Non-recoverable errors propagate. Constraint violations raise
    :class:`ConstraintViolationError`; other DB errors raise
    :class:`QueryError`.
    """

    @override
    async def save(self, entity: ConversationParticipant) -> None:
        """Upsert a participant row keyed by ``id``.

        Raises:
            ConstraintViolationError: On constraint violations (e.g. a
                duplicate ``(conversation_id, agent_id)`` pair).
            QueryError: On other database errors.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr) -> ConversationParticipant | None:
        """Retrieve a participant by ``id``, or ``None`` when absent.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a participant by id. ``True`` iff a row existed.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    @override
    async def transition_if(
        self,
        entity_id: NotBlankStr,
        from_state: ConversationParticipantStatus,
        to_state: ConversationParticipantStatus,
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for participant membership status.

        Participants carry no status-correlated columns, so ``**updates``
        accepts NO keys; passing any key raises :class:`QueryError`.

        Returns:
            ``True`` iff the row was in ``from_state`` and is now in
            ``to_state``; ``False`` on state mismatch or missing row.

        Raises:
            QueryError: On database errors, or if ``updates`` is
                non-empty.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: ConversationParticipantFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ConversationParticipant, ...]:
        """Return participants matching the spec, oldest-first (paginated).

        Order is ``(added_at ASC, id ASC)`` so a group-chat round walks
        the roster in stable enrolment order.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        ...

    @override
    async def count(self, filter_spec: ConversationParticipantFilterSpec) -> int:
        """Count participants matching the filter spec.

        Raises:
            QueryError: If the database query fails.
        """
        ...
