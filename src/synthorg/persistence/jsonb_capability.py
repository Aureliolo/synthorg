"""Optional JSONB-native query extension for Postgres backends.

Defines the ``JsonbQueryCapability`` runtime-checkable protocol that
Postgres repositories may implement.  SQLite repositories do not
implement this protocol; call sites check with ``isinstance()``
before using JSONB-specific query methods.

All query methods use parameterised SQL internally to prevent
injection.
"""

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from synthorg.persistence._shared import DEFAULT_LIST_LIMIT

if TYPE_CHECKING:
    from datetime import datetime


@runtime_checkable
class JsonbQueryCapability(Protocol):
    """Optional JSONB-native query extension for Postgres backends.

    Repositories implementing this protocol support GIN-indexed
    queries on JSONB columns using Postgres-native operators.

    Call sites should check ``isinstance(repo, JsonbQueryCapability)``
    before invoking these methods.  Non-Postgres backends (SQLite)
    do not implement this protocol.
    """

    async def query_jsonb_contains(  # noqa: PLR0913
        self,
        column: str,
        value: dict[str, Any] | list[Any],
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[tuple[Any, ...], int]:
        """Query rows where a JSONB column contains the given value.

        Uses the Postgres ``@>`` containment operator, which is
        GIN-indexed for efficient lookups.

        Args:
            column: JSONB column name to query.
            value: JSON value that the column must contain.
            since: Only return rows at or after this timestamp.
            until: Only return rows at or before this timestamp.
            limit: Maximum rows to return.
            offset: Number of rows to skip.

        Returns:
            Tuple of (matching rows, total count before pagination).
        """
        ...

    async def query_jsonb_key_exists(  # noqa: PLR0913
        self,
        column: str,
        key: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[tuple[Any, ...], int]:
        """Query rows where a JSONB column has the given top-level key.

        Uses the Postgres ``?`` existence operator, which is
        GIN-indexed for efficient lookups.

        Args:
            column: JSONB column name to query.
            key: Top-level key that must exist in the JSONB value.
            since: Only return rows at or after this timestamp.
            until: Only return rows at or before this timestamp.
            limit: Maximum rows to return.
            offset: Number of rows to skip.

        Returns:
            Tuple of (matching rows, total count before pagination).
        """
        ...
