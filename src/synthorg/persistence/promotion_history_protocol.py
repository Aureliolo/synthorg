"""Persistence protocol for the append-only promotion/demotion history.

Durably backs :class:`synthorg.hr.promotion.service.PromotionService`'s
per-agent cooldown: the cooldown is recomputed from the most recent
record per agent at load, so persisting the history keeps a crashloop
from re-enabling a promotion the previous run had gated. The records are
immutable once written, so this composes :class:`AppendOnlyRepository`.

The ``PromotionRecord`` carries a deep nested ``PromotionEvaluation``;
the repository stores it as a single JSON ``payload`` column with the
``agent_id`` / ``direction`` / ``effective_at`` fields promoted to
their own columns for filtering and recency ordering.
"""

from datetime import datetime
from typing import Protocol, override, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import PromotionDirection
from synthorg.hr.promotion.models import PromotionRecord
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    AppendOnlyRepository,
)


class PromotionHistoryFilterSpec(BaseModel):
    """Filter spec for :meth:`PromotionHistoryRepository.query`.

    Attributes:
        agent_id: Restrict to one agent. ``None`` reads every agent.
        direction: Restrict to promotions or demotions. ``None`` reads
            both.
        since: Only records with ``effective_at >= since``. ``None``
            applies no lower bound.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    agent_id: NotBlankStr | None = Field(
        default=None,
        description="Agent whose promotion history to read; None reads all",
    )
    direction: PromotionDirection | None = Field(
        default=None,
        description="Restrict to promotions or demotions; None reads both",
    )
    since: AwareDatetime | None = Field(
        default=None,
        description="Lower bound on effective_at (inclusive); must be tz-aware",
    )


@runtime_checkable
class PromotionHistoryRepository(
    AppendOnlyRepository[PromotionRecord, PromotionHistoryFilterSpec],
    Protocol,
):
    """Append-only promotion/demotion record store, newest-first.

    :class:`PromotionService` appends one record per applied change and
    recomputes per-agent cooldown from the newest record on load.
    """

    @override
    async def query(
        self,
        filter_spec: PromotionHistoryFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[PromotionRecord, ...]:
        """Return records matching the filter, newest-first (by effective_at).

        Args:
            filter_spec: The agent/direction/since predicates.
            limit: Maximum records to return.
            offset: Rows to skip before returning ``limit`` rows.

        Returns:
            Matching records, newest-first.

        Raises:
            QueryError: If the underlying read fails.
        """
        ...

    @override
    async def purge_before(self, threshold: datetime, /) -> int:
        """Delete records with ``effective_at < threshold``.

        Args:
            threshold: UTC cutoff.

        Returns:
            Number of rows removed.

        Raises:
            QueryError: If the DELETE fails.
        """
        ...
