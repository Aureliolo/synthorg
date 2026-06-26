"""Persistence protocol for the append-only audit hash chain.

Backs :class:`synthorg.observability.audit_chain.chain.HashChain` (via
its :class:`AuditChainSink`), whose ``_entries`` list was process-local:
the tamper-evident chain and its tail hash were lost on every restart,
so post-restart verification was impossible. Persisting the entries
keeps the chain durable and verifiable across restarts.

Entries are immutable and ordered by their zero-based ``position``, so
this composes :class:`AppendOnlyRepository` plus a bespoke
:meth:`get_tail` (ADR-0001 D7) that returns the highest-position entry
so the in-memory chain can rebuild its tail hash at startup without a
full scan.
"""

from datetime import datetime
from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.observability.audit_chain.chain import ChainEntry
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    AppendOnlyRepository,
)


class AuditChainFilterSpec(BaseModel):
    """Filter spec for :meth:`AuditChainRepository.query`.

    Attributes:
        min_position: Only entries with ``position >= min_position``.
            ``None`` applies no lower bound.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    min_position: int | None = Field(
        default=None,
        ge=0,
        description="Lower bound on chain position (inclusive)",
    )


@runtime_checkable
class AuditChainRepository(
    AppendOnlyRepository[ChainEntry, AuditChainFilterSpec],
    Protocol,
):
    """Append-only audit-chain entry store ordered by ``position``.

    Unlike the generic newest-first :class:`AppendOnlyRepository`,
    :meth:`query` here returns entries oldest-first (ascending
    ``position``) because the consumer rebuilds the chain in causal
    order at startup.
    """

    @override
    async def query(
        self,
        filter_spec: AuditChainFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ChainEntry, ...]:
        """Return entries matching the filter, oldest-first by position.

        Args:
            filter_spec: The optional ``min_position`` predicate.
            limit: Maximum entries to return.
            offset: Rows to skip before returning ``limit`` rows.

        Returns:
            Entries ordered by ascending ``position``.

        Raises:
            QueryError: If the underlying read fails.
        """
        ...

    @override
    async def purge_before(self, threshold: datetime, /) -> int:
        """Delete entries with ``timestamp < threshold``.

        Args:
            threshold: UTC cutoff on the entry timestamp.

        Returns:
            Number of rows removed.

        Raises:
            QueryError: If the DELETE fails.
        """
        ...

    async def get_tail(self) -> ChainEntry | None:
        """Return the highest-position entry, or ``None`` when empty.

        Bespoke per ADR-0001 D7: the in-memory chain rebuilds its tail
        hash and next position from this single row at startup, avoiding
        a full-chain scan when only the head is needed to continue
        appending.

        Returns:
            The newest (highest-position) entry, or ``None``.

        Raises:
            QueryError: If the read fails.
        """
        ...
