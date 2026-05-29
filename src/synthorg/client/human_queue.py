"""Human input queue protocol and in-memory reference implementation.

Used by ``HumanClient`` to delegate requirement submission and
deliverable review decisions to a human operator. The protocol is
transport-agnostic: PR 1 ships the in-memory implementation used by
unit tests; PR 2 wires a backend-backed queue to the API and
dashboard drop-box.
"""

import asyncio
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from synthorg.client.models import (
    ClientFeedback,
    GenerationContext,
    ReviewContext,
    TaskRequirement,
)
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger

logger = get_logger(__name__)


class PendingRequirement(BaseModel):
    """A requirement request awaiting human response."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    ticket_id: NotBlankStr = Field(description="Queue ticket id")
    client_id: NotBlankStr = Field(description="Requesting client id")
    context: GenerationContext = Field(description="Generation context")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Enqueue timestamp",
    )


class PendingReview(BaseModel):
    """A review request awaiting human response."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    ticket_id: NotBlankStr = Field(description="Queue ticket id")
    client_id: NotBlankStr = Field(description="Requesting client id")
    context: ReviewContext = Field(description="Review context")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Enqueue timestamp",
    )


@runtime_checkable
class HumanInputQueue(Protocol):
    """Transport for human-in-the-loop requirement and review input.

    Every method is async. ``enqueue_*`` returns a ticket id that
    uniquely identifies the pending request. ``await_*`` blocks
    until the ticket is resolved or the timeout elapses.
    ``resolve_*`` is called by the human-facing side (tests,
    dashboard, etc.) to supply the answer.
    """

    async def enqueue_requirement(
        self,
        *,
        client_id: NotBlankStr,
        context: GenerationContext,
    ) -> str:
        """Enqueue a requirement request and return a ticket id."""
        ...

    async def enqueue_review(
        self,
        *,
        client_id: NotBlankStr,
        context: ReviewContext,
    ) -> str:
        """Enqueue a review request and return a ticket id."""
        ...

    async def await_requirement(
        self,
        ticket_id: str,
        *,
        timeout: float,  # noqa: ASYNC109
    ) -> TaskRequirement | None:
        """Block until the ticket is resolved or the timeout expires."""
        ...

    async def await_review(
        self,
        ticket_id: str,
        *,
        timeout: float,  # noqa: ASYNC109
    ) -> ClientFeedback | None:
        """Block until the ticket is resolved or the timeout expires."""
        ...

    async def resolve_requirement(
        self,
        ticket_id: str,
        result: TaskRequirement | None,
    ) -> None:
        """Resolve a pending requirement ticket."""
        ...

    async def resolve_review(
        self,
        ticket_id: str,
        feedback: ClientFeedback,
    ) -> None:
        """Resolve a pending review ticket."""
        ...

    async def list_pending_requirements(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[PendingRequirement, ...]:
        """Return unresolved requirement tickets, optionally paginated.

        ``limit=None`` (the default) preserves the historical "return
        everything" behaviour callers relied on before pagination was
        added. When ``limit`` is set, the implementation MUST honour
        ``offset`` and slice the snapshot consistently with the queue's
        intrinsic ordering (insertion order in the reference impl).
        """
        ...

    async def list_pending_reviews(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[PendingReview, ...]:
        """Return unresolved review tickets, optionally paginated.

        See :meth:`list_pending_requirements` for the limit/offset
        contract.
        """
        ...

    async def count_pending_requirements(self) -> int:
        """Return the unfiltered count of pending requirement tickets."""
        ...

    async def count_pending_reviews(self) -> int:
        """Return the unfiltered count of pending review tickets."""
        ...


class InMemoryHumanInputQueue:
    """In-memory reference implementation of ``HumanInputQueue``.

    Backed by ``asyncio.Future`` objects keyed by ticket id, guarded
    by an ``asyncio.Lock`` to serialize mutations. Used by tests and
    as the reference backend until PR 2 introduces persistence.
    """

    def __init__(self) -> None:
        """Initialize an empty queue."""
        self._lock = asyncio.Lock()
        self._requirement_futures: dict[
            str, asyncio.Future[TaskRequirement | None]
        ] = {}
        self._review_futures: dict[str, asyncio.Future[ClientFeedback]] = {}
        self._pending_requirements: dict[str, PendingRequirement] = {}
        self._pending_reviews: dict[str, PendingReview] = {}

    async def enqueue_requirement(
        self,
        *,
        client_id: NotBlankStr,
        context: GenerationContext,
    ) -> str:
        """Enqueue a pending requirement ticket.

        Returns:
            The new ticket id awaiting a human-supplied requirement.
        """
        ticket_id = str(uuid4())
        loop = asyncio.get_running_loop()
        async with self._lock:
            self._requirement_futures[ticket_id] = loop.create_future()
            self._pending_requirements[ticket_id] = PendingRequirement(
                ticket_id=ticket_id,
                client_id=client_id,
                context=context,
            )
        return ticket_id

    async def enqueue_review(
        self,
        *,
        client_id: NotBlankStr,
        context: ReviewContext,
    ) -> str:
        """Enqueue a pending review ticket.

        Returns:
            The new ticket id awaiting a human review.
        """
        ticket_id = str(uuid4())
        loop = asyncio.get_running_loop()
        async with self._lock:
            self._review_futures[ticket_id] = loop.create_future()
            self._pending_reviews[ticket_id] = PendingReview(
                ticket_id=ticket_id,
                client_id=client_id,
                context=context,
            )
        return ticket_id

    async def await_requirement(
        self,
        ticket_id: str,
        *,
        timeout: float,  # noqa: ASYNC109
    ) -> TaskRequirement | None:
        """Wait for the ticket to be resolved.

        Returns:
            The resolved requirement, or ``None`` on timeout (never raises
            ``asyncio.TimeoutError``) so callers can convert to a
            decline-to-participate signal without wrapping in try/except.

        Raises:
            KeyError: When ``ticket_id`` is not a known requirement ticket.
        """
        async with self._lock:
            future = self._requirement_futures.get(ticket_id)
        if future is None:
            msg = f"Unknown requirement ticket: {ticket_id!r}"
            raise KeyError(msg)
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            async with self._lock:
                self._requirement_futures.pop(ticket_id, None)
                self._pending_requirements.pop(ticket_id, None)
            return None
        async with self._lock:
            self._requirement_futures.pop(ticket_id, None)
        return result

    async def await_review(
        self,
        ticket_id: str,
        *,
        timeout: float,  # noqa: ASYNC109
    ) -> ClientFeedback | None:
        """Wait for a review ticket to be resolved; ``None`` on timeout.

        Returns:
            The resolved feedback, or ``None`` on timeout.

        Raises:
            KeyError: When ``ticket_id`` is not a known review ticket.
        """
        async with self._lock:
            future = self._review_futures.get(ticket_id)
        if future is None:
            msg = f"Unknown review ticket: {ticket_id!r}"
            raise KeyError(msg)
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            async with self._lock:
                self._review_futures.pop(ticket_id, None)
                self._pending_reviews.pop(ticket_id, None)
            return None
        async with self._lock:
            self._review_futures.pop(ticket_id, None)
        return result

    async def resolve_requirement(
        self,
        ticket_id: str,
        result: TaskRequirement | None,
    ) -> None:
        """Resolve a pending requirement with the supplied result.

        The future is left in place so a concurrent ``await_*`` call
        can still observe the result; it is removed when the waiter
        finishes. Removing the ticket from the pending index is
        immediate so listings reflect the resolved state.

        Raises:
            KeyError: When ``ticket_id`` is not a known requirement ticket.
        """
        async with self._lock:
            future = self._requirement_futures.get(ticket_id)
            if future is None:
                msg = f"Unknown requirement ticket: {ticket_id!r}"
                raise KeyError(msg)
            self._pending_requirements.pop(ticket_id, None)
            if not future.done():
                future.set_result(result)

    async def resolve_review(
        self,
        ticket_id: str,
        feedback: ClientFeedback,
    ) -> None:
        """Resolve a pending review with the supplied feedback.

        Raises:
            KeyError: When ``ticket_id`` is not a known review ticket.
        """
        async with self._lock:
            future = self._review_futures.get(ticket_id)
            if future is None:
                msg = f"Unknown review ticket: {ticket_id!r}"
                raise KeyError(msg)
            self._pending_reviews.pop(ticket_id, None)
            if not future.done():
                future.set_result(feedback)

    async def list_pending_requirements(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[PendingRequirement, ...]:
        """Return a snapshot tuple of pending requirement tickets."""
        async with self._lock:
            snapshot = tuple(self._pending_requirements.values())
        if limit is None and offset == 0:
            return snapshot
        offset = max(0, offset)
        end = None if limit is None else offset + max(0, limit)
        return snapshot[offset:end]

    async def list_pending_reviews(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[PendingReview, ...]:
        """Return a snapshot tuple of pending review tickets."""
        async with self._lock:
            snapshot = tuple(self._pending_reviews.values())
        if limit is None and offset == 0:
            return snapshot
        offset = max(0, offset)
        end = None if limit is None else offset + max(0, limit)
        return snapshot[offset:end]

    async def count_pending_requirements(self) -> int:
        """Return the unfiltered count of pending requirement tickets."""
        async with self._lock:
            return len(self._pending_requirements)

    async def count_pending_reviews(self) -> int:
        """Return the unfiltered count of pending review tickets."""
        async with self._lock:
            return len(self._pending_reviews)
