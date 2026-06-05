"""Repository protocols for conversational clarify-and-propose state.

Two durable stores back the Chief of Staff conversational interface:

* :class:`ConversationRepository` -- the conversation header with its
  lifecycle status (``StatefulRepository``: id-keyed CRUD plus an
  atomic compare-and-set so two concurrent turns on one conversation
  cannot both drive the ``ACTIVE -> PROPOSED`` transition).
* :class:`ConversationTurnRepository` -- the immutable ordered turns
  (``AppendOnlyRepository``: append + filtered query + retention
  purge; turns are never mutated once written).

Concrete implementations live in the backend packages
(``synthorg.persistence.sqlite`` / ``synthorg.persistence.postgres``).
All protocols are ``@runtime_checkable``; all methods are ``async``.
"""

from typing import TYPE_CHECKING, Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.enums import ConversationStatus
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.models import Conversation, ConversationTurn
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    AppendOnlyRepository,
    StatefulRepository,
)

if TYPE_CHECKING:
    from datetime import datetime


class ConversationTurnFilterSpec(BaseModel):
    """Filter spec for ``ConversationTurnRepository.query`` (ADR-0001).

    ``conversation_id`` is the only predicate; an empty spec matches
    every turn (used only by retention sweeps, never by the service).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    conversation_id: NotBlankStr | None = Field(default=None)


@runtime_checkable
class ConversationRepository(
    StatefulRepository[Conversation, NotBlankStr, ConversationStatus],
    Protocol,
):
    """CRUD + atomic status transition for conversation headers.

    Composes :class:`StatefulRepository` (ADR-0001). ``transition_if``
    performs the status compare-and-set at the database level so a
    second concurrent turn on the same conversation cannot also flip
    ``ACTIVE -> PROPOSED``.

    Non-recoverable errors (``MemoryError``, ``RecursionError``)
    propagate. Constraint violations raise
    :class:`ConstraintViolationError`; other DB errors raise
    :class:`QueryError`.
    """

    @override
    async def save(self, entity: Conversation) -> None:
        """Upsert a conversation header.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr) -> Conversation | None:
        """Retrieve a conversation by id, or ``None`` when absent.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a conversation by id. ``True`` iff a row existed.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Conversation, ...]:
        """List conversations, newest-first (``created_at DESC, id DESC``).

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        ...

    @override
    async def transition_if(
        self,
        entity_id: NotBlankStr,
        from_state: ConversationStatus,
        to_state: ConversationStatus,
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for the conversation status.

        ``**updates`` carries the status-correlated ``updated_at``
        timestamp (ISO-8601 string); other keys are rejected.

        Returns:
            ``True`` iff the row was in ``from_state`` and is now in
            ``to_state``; ``False`` on state mismatch or missing row.

        Raises:
            QueryError: On database errors, or if ``updates`` carries
                an unsupported key.
        """
        ...


@runtime_checkable
class ConversationTurnRepository(
    AppendOnlyRepository[ConversationTurn, ConversationTurnFilterSpec],
    Protocol,
):
    """Append-only ordered turns for a conversation.

    Composes :class:`AppendOnlyRepository` (ADR-0001). ``query``
    returns turns for one conversation newest-first (the append-only
    invariant); the service reverses the bounded result to
    chronological order for prompt assembly.

    Non-recoverable errors propagate. Constraint violations raise
    :class:`ConstraintViolationError`; other DB errors raise
    :class:`QueryError`.
    """

    @override
    async def append(self, event: ConversationTurn) -> None:
        """Append one turn (immutable once written).

        Raises:
            ConstraintViolationError: On constraint violations (e.g.
                a duplicate ``(conversation_id, sequence)``).
            QueryError: On other database errors.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: ConversationTurnFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ConversationTurn, ...]:
        """Return turns matching the spec, newest-first (paginated).

        Order is ``(sequence DESC, id DESC)`` within a conversation.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        ...

    @override
    async def purge_before(self, threshold: datetime) -> int:
        """Delete turns created before ``threshold``. Returns rows removed.

        ``threshold`` must be timezone-aware; a naive datetime is rejected
        (``QueryError``) rather than silently coerced, so the cut-off
        cannot drift with the backend's session timezone.

        Raises:
            QueryError: If ``threshold`` is naive, or on database errors.
        """
        ...
