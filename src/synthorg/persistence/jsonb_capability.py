"""JSONB-native query extension contract.

Defines the ``JsonbQueryCapability`` runtime-checkable protocol.
Repositories that compose it (e.g. ``AuditRepository``) make the JSONB
query methods part of their contract: the Postgres implementation runs
the GIN-indexed native query, while non-Postgres backends (SQLite, test
fakes) raise :class:`JsonbQueryUnsupportedError`. Call sites invoke the
methods directly instead of probing the backend with ``isinstance()``.

All query methods use parameterised SQL internally to prevent
injection.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from synthorg.persistence._shared import DEFAULT_LIST_LIMIT


@runtime_checkable
class JsonbQueryCapability[RowT](Protocol):
    """Optional JSONB-native query extension for Postgres backends.

    Repositories composing this protocol support GIN-indexed queries on
    JSONB columns using Postgres-native operators. The Postgres
    implementation runs the native query; non-Postgres backends raise
    :class:`JsonbQueryUnsupportedError` so call sites invoke the methods
    directly without an ``isinstance`` capability probe.
    """

    async def query_jsonb_contains(  # noqa: PLR0913
        self,
        column: str,
        value: dict[str, object] | list[object],
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[tuple[RowT, ...], int]:
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
    ) -> tuple[tuple[RowT, ...], int]:
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
