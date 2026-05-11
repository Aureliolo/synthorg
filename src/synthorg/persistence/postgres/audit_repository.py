"""Postgres repository implementation for security audit entries."""

from datetime import UTC
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_AUDIT_ENTRY_QUERIED,
    PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
)
from synthorg.persistence._shared import DEFAULT_LIST_LIMIT
from synthorg.persistence._shared.audit import (
    AUDIT_COLUMNS,
    audit_entry_to_payload,
    classify_audit_save_error,
    row_to_audit_entry,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool
    from pydantic import AwareDatetime

    from synthorg.core.enums import ApprovalRiskLevel
    from synthorg.core.types import NotBlankStr
    from synthorg.security.models import AuditEntry, AuditVerdictStr

logger = get_logger(__name__)

_COL_LIST = ", ".join(AUDIT_COLUMNS)


def _postgres_is_duplicate(exc: BaseException) -> bool:
    """Detect Postgres duplicate-key violations by exception type."""
    return isinstance(exc, psycopg.errors.UniqueViolation)


class PostgresAuditRepository:
    """Postgres implementation of the AuditRepository protocol.

    Append-only: entries can be saved and queried, but never updated
    or deleted, preserving audit integrity.

    Timestamps are normalized to UTC to ensure correct ordering in
    TIMESTAMPTZ columns.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, entry: AuditEntry) -> None:
        """Persist an audit entry (append-only, no upsert).

        Args:
            entry: The audit entry to persist.

        Raises:
            DuplicateRecordError: If an entry with the same ID exists.
            QueryError: If the operation fails.
        """
        payload = audit_entry_to_payload(
            entry,
            json_serializer=Jsonb,
            timestamp_serializer=lambda dt: dt,
        )
        placeholders = ", ".join(["%s"] * len(AUDIT_COLUMNS))
        values = tuple(payload[c] for c in AUDIT_COLUMNS)
        sql = (
            f"INSERT INTO audit_entries ({_COL_LIST}) "  # noqa: S608
            f"VALUES ({placeholders})"
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, values)
                await conn.commit()
        except psycopg.Error as exc:
            raise classify_audit_save_error(
                exc,
                entry_id=entry.id,
                is_duplicate=_postgres_is_duplicate,
            ) from exc
        # No mutation log emitted from the persistence layer: per
        # CLAUDE.md "Repositories should not log mutations themselves
        # -- the service layer is the canonical logging point so audit
        # trails do not duplicate when multiple callers share a repo."

    async def query(  # noqa: PLR0913
        self,
        *,
        agent_id: NotBlankStr | None = None,
        action_type: NotBlankStr | None = None,
        verdict: AuditVerdictStr | None = None,
        risk_level: ApprovalRiskLevel | None = None,
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[AuditEntry, ...]:
        """Query audit entries with optional filters (newest first).

        Filters are AND-combined. Results ordered by timestamp
        descending.

        Args:
            agent_id: Filter by agent identifier.
            action_type: Filter by action type string.
            verdict: Filter by verdict string.
            risk_level: Filter by risk level.
            since: Only return entries at or after this timestamp.
            until: Only return entries at or before this timestamp.
            limit: Maximum number of entries (must be >= 1).

        Returns:
            Matching audit entries as a tuple.

        Raises:
            QueryError: If the operation fails, *limit* < 1, or
                *until* is earlier than *since*.
        """
        self._validate_query_args(since=since, until=until, limit=limit)

        where, params = self._build_query_clause(
            agent_id=agent_id,
            action_type=action_type,
            verdict=verdict,
            risk_level=risk_level,
            since=since,
            until=until,
        )
        sql = (
            f"SELECT {_COL_LIST} FROM audit_entries{where} "  # noqa: S608
            "ORDER BY timestamp DESC LIMIT %s"
        )
        params.append(limit)

        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to query audit entries"
            logger.warning(
                PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                agent_id=agent_id,
                action_type=action_type,
                verdict=verdict,
                risk_level=(risk_level.value if risk_level else None),
                since=since.isoformat() if since else None,
                until=until.isoformat() if until else None,
                limit=limit,
            )
            raise QueryError(msg) from exc

        results = tuple(self._row_to_entry(row) for row in rows)
        logger.debug(
            PERSISTENCE_AUDIT_ENTRY_QUERIED,
            count=len(results),
        )
        return results

    def _validate_query_args(
        self,
        *,
        since: AwareDatetime | None,
        until: AwareDatetime | None,
        limit: int,
    ) -> None:
        """Validate query parameters before execution.

        Raises:
            QueryError: If *limit* < 1 or *until* < *since*.
        """
        if limit < 1:
            msg = f"limit must be >= 1, got {limit}"
            logger.warning(
                PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                error=msg,
                limit=limit,
            )
            raise QueryError(msg)

        if since is not None and until is not None and until < since:
            msg = "until must not be earlier than since"
            logger.warning(
                PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                error=msg,
                since=since.isoformat(),
                until=until.isoformat(),
            )
            raise QueryError(msg)

    def _build_query_clause(  # noqa: PLR0913
        self,
        *,
        agent_id: NotBlankStr | None,
        action_type: NotBlankStr | None,
        verdict: AuditVerdictStr | None,
        risk_level: ApprovalRiskLevel | None,
        since: AwareDatetime | None,
        until: AwareDatetime | None,
    ) -> tuple[str, list[object]]:
        """Build WHERE clause and parameters for audit query.

        Timestamps are normalized to UTC for consistent comparison.

        Returns:
            Tuple of (WHERE clause string, parameter list).
        """
        conditions: list[str] = []
        params: list[object] = []

        if agent_id is not None:
            conditions.append("agent_id = %s")
            params.append(agent_id)
        if action_type is not None:
            conditions.append("action_type = %s")
            params.append(action_type)
        if verdict is not None:
            conditions.append("verdict = %s")
            params.append(verdict)
        if risk_level is not None:
            conditions.append("risk_level = %s")
            params.append(risk_level.value)
        if since is not None:
            conditions.append("timestamp >= %s")
            params.append(since.astimezone(UTC))
        if until is not None:
            conditions.append("timestamp <= %s")
            params.append(until.astimezone(UTC))

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        return where, params

    def _row_to_entry(self, row: dict[str, object]) -> AuditEntry:
        """Convert a database row to an ``AuditEntry`` model.

        Delegates to :func:`row_to_audit_entry` from the shared helper
        so SQLite and Postgres use identical deserialisation logic.
        Postgres JSONB returns ``matched_rules`` as a Python list; the
        helper handles both that and string-encoded SQLite rows.

        Args:
            row: A dict mapping column names to their values.

        Raises:
            QueryError: If the row cannot be deserialized.
        """
        return row_to_audit_entry(row)

    # ── JsonbQueryCapability implementation ────────────────────

    _ALLOWED_JSONB_COLS: frozenset[str] = frozenset({"matched_rules"})

    def _check_jsonb_column(self, column: str) -> None:
        """Reject unknown column names to prevent SQL injection."""
        if column not in self._ALLOWED_JSONB_COLS:
            logger.warning(
                PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                reason="jsonb_column_rejected",
                column=column,
                allowed=sorted(self._ALLOWED_JSONB_COLS),
            )
            msg = (
                f"JSONB column {column!r} not allowed; "
                f"must be one of {sorted(self._ALLOWED_JSONB_COLS)}"
            )
            raise ValueError(msg)

    def _build_time_clause(
        self,
        since: AwareDatetime | None,
        until: AwareDatetime | None,
    ) -> tuple[list[str], list[object]]:
        """Build timestamp filter conditions."""
        conditions: list[str] = []
        params: list[object] = []
        if since is not None:
            conditions.append("timestamp >= %s")
            params.append(since.astimezone(UTC))
        if until is not None:
            conditions.append("timestamp <= %s")
            params.append(until.astimezone(UTC))
        return conditions, params

    async def _jsonb_query(  # noqa: PLR0913
        self,
        extra_condition: str,
        extra_params: list[object],
        *,
        since: AwareDatetime | None,
        until: AwareDatetime | None,
        limit: int,
        offset: int,
    ) -> tuple[tuple[AuditEntry, ...], int]:
        """Execute a JSONB query with time filters and pagination."""
        self._validate_query_args(since=since, until=until, limit=limit)
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            logger.warning(
                PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                error=msg,
                offset=offset,
            )
            raise QueryError(msg)
        time_conds, time_params = self._build_time_clause(since, until)
        all_conds = [extra_condition, *time_conds]
        all_params = [*extra_params, *time_params]

        where = f" WHERE {' AND '.join(all_conds)}"
        count_sql = f"SELECT COUNT(*) FROM audit_entries{where}"  # noqa: S608
        data_sql = (
            f"SELECT {_COL_LIST} FROM audit_entries{where} "  # noqa: S608
            "ORDER BY timestamp DESC LIMIT %s OFFSET %s"
        )

        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(count_sql, all_params)
                count_row = await cur.fetchone()
                total = int(count_row["count"]) if count_row else 0

                await cur.execute(
                    data_sql,
                    [*all_params, limit, offset],
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "JSONB query failed on audit_entries"
            logger.warning(
                PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        entries = tuple(self._row_to_entry(row) for row in rows)
        return entries, total

    async def query_jsonb_contains(  # noqa: PLR0913
        self,
        column: str,
        value: dict[str, Any] | list[Any],
        *,
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[tuple[AuditEntry, ...], int]:
        """Query audit entries where *column* contains *value*.

        Uses the ``@>`` containment operator (GIN-indexed).
        """
        self._check_jsonb_column(column)
        condition = f"{column} @> %s::jsonb"
        return await self._jsonb_query(
            condition,
            [Jsonb(value)],
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )

    async def query_jsonb_key_exists(  # noqa: PLR0913
        self,
        column: str,
        key: str,
        *,
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[tuple[AuditEntry, ...], int]:
        """Query audit entries where *column* has a top-level *key*.

        Uses the ``?`` existence operator (GIN-indexed).
        """
        self._check_jsonb_column(column)
        condition = f"{column} ? %s"
        return await self._jsonb_query(
            condition,
            [key],
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )

    async def purge_before(self, cutoff: AwareDatetime) -> int:
        """Delete audit entries strictly older than *cutoff* (CFG-1).

        Args:
            cutoff: UTC-normalised timestamp. Rows with
                ``timestamp < cutoff`` are removed.

        Returns:
            Number of rows deleted.

        Raises:
            QueryError: If the DELETE fails.
        """
        utc_cutoff = cutoff.astimezone(UTC)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM audit_entries WHERE timestamp < %s",
                    (utc_cutoff,),
                )
                deleted = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to purge audit entries"
            logger.warning(
                PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                cutoff=utc_cutoff.isoformat(),
            )
            raise QueryError(msg) from exc
        return deleted
