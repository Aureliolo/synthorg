"""Repository protocol for agent-initiated conversation invites.

A :class:`ConversationInvite` links one approval-queue item to a
requested membership change in a ``kind='group'`` conversation. The
repository composes :class:`StatefulRepository` (atomic
``PENDING -> ACCEPTED``/``DECLINED`` transitions, keyed off the human
consent decision) and :class:`FilteredQueryRepository` (lookup by
``conversation_id`` / ``approval_id`` / ``target_agent_id`` / status,
so the park path can reject a duplicate pending invite for the same
target).

Concrete implementations live in the backend packages
(``synthorg.persistence.sqlite`` / ``synthorg.persistence.postgres``).
All protocols are ``@runtime_checkable``; all methods are ``async``.
"""

from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.enums import ConversationInviteStatus
from synthorg.meta.chief_of_staff.group_models import ConversationInvite
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    StatefulRepository,
)


class ConversationInviteFilterSpec(BaseModel):
    """Filter spec for ``ConversationInviteRepository.query``.

    All fields optional; an empty spec matches every invite.
    ``approval_id`` is the consent-resume lookup key (the decision
    arrives keyed by approval id); ``conversation_id`` +
    ``target_agent_id`` + ``status`` power the park path's
    "already a pending invite for this target" duplicate check.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    conversation_id: NotBlankStr | None = Field(default=None)
    approval_id: NotBlankStr | None = Field(default=None)
    target_agent_id: NotBlankStr | None = Field(default=None)
    status: ConversationInviteStatus | None = Field(default=None)


@runtime_checkable
class ConversationInviteRepository(
    StatefulRepository[ConversationInvite, NotBlankStr, ConversationInviteStatus],
    FilteredQueryRepository[ConversationInvite, ConversationInviteFilterSpec],
    Protocol,
):
    """CRUD + state-transition + filtered query for agent invites.

    Composes :class:`StatefulRepository` + :class:`FilteredQueryRepository`
    (ADR-0001). No bespoke methods.

    Non-recoverable errors propagate. Constraint violations raise
    :class:`ConstraintViolationError`; other DB errors raise
    :class:`QueryError`.
    """

    @override
    async def save(self, entity: ConversationInvite) -> None:
        """Upsert a conversation invite.

        Raises:
            ConstraintViolationError: On constraint violations (e.g. a
                duplicate ``approval_id``).
            QueryError: On other database errors.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr) -> ConversationInvite | None:
        """Retrieve an invite by id, or ``None`` when absent.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete an invite by id. ``True`` iff a row existed.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ConversationInvite, ...]:
        """List invites, newest-first (``created_at DESC, id DESC``).

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        ...

    @override
    async def transition_if(
        self,
        entity_id: NotBlankStr,
        from_state: ConversationInviteStatus,
        to_state: ConversationInviteStatus,
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for the invite status.

        ``**updates`` is unused (no status-correlated columns); any
        key is rejected.

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
        filter_spec: ConversationInviteFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ConversationInvite, ...]:
        """Return invites matching the spec, newest-first (paginated).

        Order is ``(created_at DESC, id DESC)``.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        ...

    @override
    async def count(self, filter_spec: ConversationInviteFilterSpec) -> int:
        """Count invites matching the filter spec.

        Raises:
            QueryError: If the database query fails.
        """
        ...
