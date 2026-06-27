"""Persistence protocols for progressive trust state and change history.

Trust state is keyed by ``agent_id`` and mutated in place as an agent
is promoted, demoted, evaluated, or decayed: it composes
:class:`IdKeyedRepository`. The change history is an immutable audit
trail of every level transition and composes
:class:`AppendOnlyRepository`, newest-first.

Both durably back :class:`synthorg.security.trust.service.TrustService`
so elevated-trust decisions and their audit trail survive a restart.
"""

from datetime import datetime
from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    AppendOnlyRepository,
    IdKeyedRepository,
)
from synthorg.security.trust.models import TrustChangeRecord, TrustState


@runtime_checkable
class TrustStateRepository(
    IdKeyedRepository[TrustState, NotBlankStr],
    Protocol,
):
    """CRUD by ``agent_id`` for the current per-agent trust state.

    ``save`` is an idempotent upsert keyed on ``agent_id``;
    :class:`TrustService` write-through-persists on every state
    mutation and hydrates its in-memory cache from :meth:`list_items`
    at startup.
    """


class TrustChangeHistoryFilterSpec(BaseModel):
    """Filter spec for :meth:`TrustChangeHistoryRepository.query`.

    Attributes:
        agent_id: Restrict to one agent's history. ``None`` enumerates
            every agent's change records (newest-first across agents).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    agent_id: NotBlankStr | None = Field(
        default=None,
        description="Agent whose change history to read; None reads all",
    )


@runtime_checkable
class TrustChangeHistoryRepository(
    AppendOnlyRepository[TrustChangeRecord, TrustChangeHistoryFilterSpec],
    Protocol,
):
    """Append-only audit trail of trust level transitions, newest-first.

    Records are immutable once written; ``purge_before`` is the only
    deletion primitive (retention sweeps). :class:`TrustService`
    appends one record per applied change and reads the history back
    for an agent via :meth:`query`.
    """

    @override
    async def query(
        self,
        filter_spec: TrustChangeHistoryFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[TrustChangeRecord, ...]:
        """Return change records matching the filter, newest-first.

        Args:
            filter_spec: Carries the optional ``agent_id`` predicate.
            limit: Maximum records to return.
            offset: Rows to skip before returning ``limit`` rows.

        Returns:
            Change records ordered newest-first, paginated.

        Raises:
            QueryError: If the underlying read fails.
        """
        ...

    @override
    async def purge_before(self, threshold: datetime, /) -> int:
        """Delete change records older than ``threshold``.

        Args:
            threshold: UTC timestamp; records with ``timestamp <
                threshold`` are removed.

        Returns:
            Number of rows removed.

        Raises:
            QueryError: If the DELETE fails.
        """
        ...
