# module-kind: declarative
"""Repository protocol for outstanding provider latching failures.

One row per ``(provider, model)`` pair, holding the newest refusal that
still latches. Keyed rather than append-only on purpose: the reader takes
the newest latch for a pair and nothing else, so a log would accumulate a
row per refused call to answer a question only its last entry decides.

Concrete implementations live in the backend packages
(``synthorg.persistence.sqlite`` / ``synthorg.persistence.postgres``).
All protocols are ``@runtime_checkable``; all methods are ``async``.
"""

from datetime import datetime
from typing import Protocol, override, runtime_checkable

from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, IdKeyedRepository
from synthorg.providers.latch import LatchedFailure


@runtime_checkable
class ProviderLatchRepository(
    IdKeyedRepository[LatchedFailure, tuple[str, str]],
    Protocol,
):
    """Durable store for the refusals that outlive their measuring window.

    The id is the ``(provider_name, model)`` pair, so a fresh refusal on a
    pair replaces the one before it.

    Non-recoverable errors propagate; database errors raise
    :class:`~synthorg.core.persistence_errors.QueryError`.
    """

    @override
    async def save(self, entity: LatchedFailure, /) -> None:
        """Insert or replace the latch for this pair.

        Raises:
            QueryError: If the write fails.
        """
        ...

    @override
    async def get(self, entity_id: tuple[str, str], /) -> LatchedFailure | None:
        """Return one pair's outstanding latch, or ``None`` when it has none.

        Raises:
            QueryError: If the read fails.
        """
        ...

    @override
    async def delete(self, entity_id: tuple[str, str], /) -> bool:
        """Drop one pair's latch.

        Returns:
            ``True`` iff a row existed.

        Raises:
            QueryError: If the delete fails.
        """
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[LatchedFailure, ...]:
        """Return every outstanding latch, ordered by pair (paginated).

        Raises:
            QueryError: If the read fails or pagination args are invalid.
        """
        ...

    async def purge_before(self, threshold: datetime, /) -> int:
        """Drop every latch older than *threshold*, in one statement.

        Bespoke under ADR-0001 D7, and load-bearing rather than a
        convenience: releasing by key would delete whatever row the pair
        holds NOW, which is not necessarily the expired one the caller read.
        A refusal landing between the read and the delete would be erased by
        its own housekeeping, and the pair would come back serving on the
        next boot. Predicating on ``occurred_at`` makes the delete describe
        the rows it meant rather than the pairs they belonged to.

        Args:
            threshold: Latches recorded strictly before this are released.

        Returns:
            How many rows were dropped.

        Raises:
            QueryError: If the delete fails.
        """
        ...


__all__ = ["ProviderLatchRepository"]
