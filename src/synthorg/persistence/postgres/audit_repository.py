"""Postgres repository implementation for security audit entries."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb

from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.audit_entry import (
    PERSISTENCE_AUDIT_ENTRY_QUERIED,
    PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared.audit import (
    AUDIT_COLUMNS,
    audit_entry_to_payload,
    classify_audit_save_error,
    row_to_audit_entry,
)
from synthorg.security.models import AuditVerdictStr

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from synthorg.core.enums import ApprovalRiskLevel
    from synthorg.core.types import NotBlankStr
    from synthorg.persistence.audit_protocol import AuditFilterSpec
    from synthorg.security.models import AuditEntry

logger = get_logger(__name__)

_COL_LIST = ", ".join(AUDIT_COLUMNS)


def _postgres_is_duplicate(exc: BaseException) -> bool:
    """Detect Postgres duplicate-key violations by exception type.

    Returns:
        ``True`` when ``exc`` is a Postgres unique-constraint violation,
        ``False`` otherwise.
    """
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

    async def append(self, entry: AuditEntry) -> None:
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
            error = classify_audit_save_error(
                exc,
                entry_id=entry.id,
                is_duplicate=_postgres_is_duplicate,
            )
            raise error from exc
        # No mutation log emitted from the persistence layer: per
        # CLAUDE.md "Repositories should not log mutations themselves
        # -- the service layer is the canonical logging point so audit
        # trails do not duplicate when multiple callers share a repo."

    async def query(
        self,
        filter_spec: AuditFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[AuditEntry, ...]:
        """Return audit entries matching the filter spec (paginated).

        Results are ordered by timestamp descending (newest first).

        Args:
            filter_spec: Audit filter specification with optional filters.
            limit: Maximum number of entries to return (must be >= 1).
            offset: Number of entries to skip (for pagination).

        Returns:
            Matching audit entries as a tuple.

        Raises:
            QueryError: If the operation fails, *limit* < 1, or
                *until* is earlier than *since* in the filter spec.
        """
        self._validate_query_args(
            since=filter_spec.since,
            until=filter_spec.until,
            limit=limit,
        )

        where, params = self._build_query_clause(
            agent_id=filter_spec.agent_id,
            action_type=filter_spec.action_type,
            verdict=filter_spec.verdict,
            risk_level=filter_spec.risk_level,
            since=filter_spec.since,
            until=filter_spec.until,
        )
        sql = (
            f"SELECT {_COL_LIST} FROM audit_entries{where} "  # noqa: S608
            "ORDER BY timestamp DESC LIMIT %s OFFSET %s"
        )
        params.append(limit)
        params.append(offset)

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
                agent_id=filter_spec.agent_id,
                action_type=filter_spec.action_type,
                verdict=filter_spec.verdict,
                risk_level=(
                    filter_spec.risk_level.value if filter_spec.risk_level else None
                ),
                since=filter_spec.since.isoformat() if filter_spec.since else None,
                until=filter_spec.until.isoformat() if filter_spec.until else None,
                limit=limit,
                offset=offset,
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
        since: datetime | None,
        until: datetime | None,
        limit: int,
    ) -> None:
        """Validate query parameters before execution.

        Raises:
            QueryError: If *limit* < 1, *since* or *until* is naive, or
                *until* < *since*.
        """
        if limit < 1:
            msg = f"limit must be >= 1, got {limit}"
            logger.warning(
                PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                error=msg,
                limit=limit,
            )
            raise QueryError(msg)

        if since is not None and since.tzinfo is None:
            msg = "since must be timezone-aware; a naive datetime is rejected"
            raise QueryError(msg)
        if until is not None and until.tzinfo is None:
            msg = "until must be timezone-aware; a naive datetime is rejected"
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
        since: datetime | None,
        until: datetime | None,
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

    def _row_to_entry(self, row: DictRow) -> AuditEntry:
        """Convert a database row to an ``AuditEntry`` model.

        Delegates to :func:`row_to_audit_entry` from the shared helper
        so SQLite and Postgres use identical deserialisation logic.
        Postgres JSONB returns ``matched_rules`` as a Python list; the
        helper handles both that and string-encoded SQLite rows.

        Args:
            row: A dict mapping column names to their values.

        Raises:
            QueryError: If the row cannot be deserialized.

        Returns:
            Result of type ``AuditEntry``.
        """
        return row_to_audit_entry(row)

    # ── JsonbQueryCapability implementation ────────────────────

    _ALLOWED_JSONB_COLS: frozenset[str] = frozenset({"matched_rules"})

    def _check_jsonb_column(self, column: str) -> None:
        """Reject unknown column names to prevent SQL injection.

        Raises:
            ValueError: If an argument fails validation.
        """
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
        since: datetime | None,
        until: datetime | None,
    ) -> tuple[list[str], list[object]]:
        """Build timestamp filter conditions.

        Returns:
            ``(conditions, params)`` where ``conditions`` is a list of
            SQL fragments to AND into the WHERE clause and ``params``
            is the matching positional parameter list.
        """
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
        since: datetime | None,
        until: datetime | None,
        limit: int,
        offset: int,
    ) -> tuple[tuple[AuditEntry, ...], int]:
        """Execute a JSONB query with time filters and pagination.

        Returns:
            ``(page, total)`` where ``page`` is the tuple of matching
            audit entries for the requested page and ``total`` is the
            unpaginated row count.

        Raises:
            QueryError: If the database query fails.
        """
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
        value: dict[str, object] | list[object],
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[tuple[AuditEntry, ...], int]:
        """Query audit entries where *column* contains *value*.

        Uses the ``@>`` containment operator (GIN-indexed).

        Returns:
            Tuple of ``(page, total)`` -- the page of matching entities and the
            total match count.
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
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[tuple[AuditEntry, ...], int]:
        """Query audit entries where *column* has a top-level *key*.

        Uses the ``?`` existence operator (GIN-indexed).

        Returns:
            Tuple of ``(page, total)`` -- the page of matching entities and the
            total match count.
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

    async def purge_before(self, cutoff: datetime) -> int:
        """Delete audit entries strictly older than *cutoff* (CFG-1).

        Args:
            cutoff: Timezone-aware UTC timestamp. Rows with
                ``timestamp < cutoff`` are removed. A naive datetime is
                rejected to prevent silent local-time misinterpretation.

        Returns:
            Number of rows deleted.

        Raises:
            QueryError: If *cutoff* is naive or the DELETE fails.
        """
        if cutoff.tzinfo is None:
            msg = "cutoff must be timezone-aware; a naive datetime is rejected"
            raise QueryError(msg)
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
