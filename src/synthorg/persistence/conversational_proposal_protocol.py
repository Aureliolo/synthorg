"""Repository protocol for conversational work proposals.

A :class:`ConversationalProposal` links one approval-queue item to
the serialised ``WorkItem`` that runs if and only if the human
approves. The repository composes :class:`StatefulRepository` (atomic
``PENDING -> EXECUTED``/``REJECTED`` transitions, keyed off the
approval decision) and :class:`FilteredQueryRepository` (lookup by
``conversation_id`` / ``approval_id``).

Concrete implementations live in the backend packages
(``synthorg.persistence.sqlite`` / ``synthorg.persistence.postgres``).
All protocols are ``@runtime_checkable``; all methods are ``async``.
"""

from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.enums import ConversationalProposalStatus
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.models import ConversationalProposal
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    StatefulRepository,
)


class ConversationalProposalFilterSpec(BaseModel):
    """Filter spec for ``ConversationalProposalRepository.query``.

    All fields optional; an empty spec matches every proposal.
    ``approval_id`` is the dispatcher's lookup key (decision arrives
    keyed by approval id); ``conversation_id`` powers the per-thread
    proposal listing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    conversation_id: NotBlankStr | None = Field(default=None)
    approval_id: NotBlankStr | None = Field(default=None)
    status: ConversationalProposalStatus | None = Field(default=None)


@runtime_checkable
class ConversationalProposalRepository(
    StatefulRepository[
        ConversationalProposal, NotBlankStr, ConversationalProposalStatus
    ],
    FilteredQueryRepository[ConversationalProposal, ConversationalProposalFilterSpec],
    Protocol,
):
    """CRUD + state-transition + filtered query for work proposals.

    Composes :class:`StatefulRepository` + :class:`FilteredQueryRepository`
    (ADR-0001). No bespoke methods.

    Non-recoverable errors propagate. Constraint violations raise
    :class:`ConstraintViolationError`; other DB errors raise
    :class:`QueryError`.
    """

    @override
    async def save(self, entity: ConversationalProposal) -> None:
        """Upsert a conversational proposal.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr) -> ConversationalProposal | None:
        """Retrieve a proposal by id, or ``None`` when absent.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a proposal by id. ``True`` iff a row existed.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ConversationalProposal, ...]:
        """List proposals, newest-first (``created_at DESC, id DESC``).

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        ...

    @override
    async def transition_if(
        self,
        entity_id: NotBlankStr,
        from_state: ConversationalProposalStatus,
        to_state: ConversationalProposalStatus,
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for the proposal status.

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
        filter_spec: ConversationalProposalFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ConversationalProposal, ...]:
        """Return proposals matching the spec, newest-first (paginated).

        Order is ``(created_at DESC, id DESC)``.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        ...

    @override
    async def count(self, filter_spec: ConversationalProposalFilterSpec) -> int:
        """Count proposals matching the filter spec.

        Raises:
            QueryError: If the database query fails.
        """
        ...
