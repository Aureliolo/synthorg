# module-kind: declarative
"""Repository protocol for the operator-declared failover log.

Append-only: one row per dispatch the alternate served. Rows are never
edited, because the question they answer is "what actually happened", and
retention is a bulk sweep rather than a per-row delete.

Concrete implementations live in the backend packages
(``synthorg.persistence.sqlite`` / ``synthorg.persistence.postgres``).
All protocols are ``@runtime_checkable``; all methods are ``async``.
"""

from datetime import datetime
from typing import Protocol, override, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, AppendOnlyRepository
from synthorg.providers.failover_event import ProviderFailoverEvent


class ProviderFailoverEventFilterSpec(BaseModel):
    """Filter spec for :meth:`ProviderFailoverEventRepository.query`.

    Attributes:
        feature: Restrict to one system feature's dispatches. ``None``
            reads every feature.
        declared_provider: Restrict to the connection the operator bound.
            Answers "what has this connection cost me in failovers".
        since: Restrict to events at or after this instant.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    feature: NotBlankStr | None = Field(
        default=None,
        description="System feature whose events to read",
    )
    declared_provider: NotBlankStr | None = Field(
        default=None,
        description="Declared connection whose events to read",
    )
    since: AwareDatetime | None = Field(
        default=None,
        description="Earliest occurrence to include",
    )


@runtime_checkable
class ProviderFailoverEventRepository(
    AppendOnlyRepository[ProviderFailoverEvent, ProviderFailoverEventFilterSpec],
    Protocol,
):
    """Append-only persistence for failover engagements.

    Non-recoverable errors propagate; database errors raise
    :class:`~synthorg.core.persistence_errors.QueryError`.
    """

    @override
    async def append(self, event: ProviderFailoverEvent, /) -> None:
        """Persist one engagement.

        Raises:
            QueryError: If the write fails.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: ProviderFailoverEventFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ProviderFailoverEvent, ...]:
        """Return matching engagements newest-first (paginated).

        Raises:
            QueryError: If the read fails or pagination args are invalid.
        """
        ...

    @override
    async def purge_before(self, threshold: datetime, /) -> int:
        """Delete engagements older than ``threshold``.

        Returns:
            The number of rows removed.

        Raises:
            QueryError: If the delete fails.
        """
        ...


__all__ = [
    "ProviderFailoverEventFilterSpec",
    "ProviderFailoverEventRepository",
]
