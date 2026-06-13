"""SQLite repository implementation for security audit entries."""

import json
import sqlite3
from datetime import UTC, datetime

import aiosqlite

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.audit_entry import (
    PERSISTENCE_AUDIT_ENTRY_QUERIED,
    PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import format_iso_utc, normalize_utc
from synthorg.persistence._shared.audit import (
    AUDIT_COLUMNS,
    audit_entry_to_payload,
    classify_audit_save_error,
    row_to_audit_entry,
)
from synthorg.persistence.audit_protocol import AuditFilterSpec
from synthorg.persistence.sqlite._shared import (
    WriteContext,
    is_unique_constraint_error,
)
from synthorg.security.models import AuditEntry, AuditVerdictStr

logger = get_logger(__name__)

_COL_LIST = ", ".join(AUDIT_COLUMNS)


class SQLiteAuditRepository:
    """SQLite implementation of the AuditRepository protocol.

    Append-only: entries can be saved and queried, but never updated
    or deleted, preserving audit integrity.

    Timestamps are normalized to UTC before storage to ensure correct
    lexicographic ordering in SQLite TEXT columns.

    Args:
        db: An open aiosqlite connection.
        write_context: Async context manager that serializes writes on
            the shared ``aiosqlite.Connection``. Supplied by
            ``SQLitePersistenceBackend.write_context`` in production;
            tests can pass
            ``tests._shared.persistence.make_private_write_context()``
            for standalone construction.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

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
            json_serializer=json.dumps,
            timestamp_serializer=lambda dt: format_iso_utc(normalize_utc(dt)),
        )
        placeholders = ", ".join(f":{c}" for c in AUDIT_COLUMNS)
        sql = f"INSERT INTO audit_entries ({_COL_LIST}) VALUES ({placeholders})"  # noqa: S608
        async with self._write_context():
            try:
                await self._db.execute(sql, payload)
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                error = classify_audit_save_error(
                    exc,
                    entry_id=entry.id,
                    is_duplicate=is_unique_constraint_error,
                )
                raise error from exc

    async def _safe_rollback(self) -> None:
        """Best-effort rollback on the shared connection.

        Mirrors the project_repo / artifact_repo pattern: a secondary
        rollback failure must not mask the original error, but we DO
        log it so a tainted shared connection leaves a trail in
        observability.
        """
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
            logger.warning(
                PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                error_type=type(rollback_exc).__name__,
                error=safe_error_description(rollback_exc),
                rollback_failed=True,
            )
        # No mutation log emitted from the persistence layer: per
        # CLAUDE.md "Repositories should not log mutations themselves
        # -- the service layer is the canonical logging point so audit
        # trails do not duplicate when multiple callers share a repo."
        # The audit entry IS the audit record; the persistence event
        # would be redundant. Callers that need a save signal should
        # log it once at the boundary that owns the write.

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
            offset=offset,
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
            "ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        )
        params.append(limit)
        params.append(offset)

        try:
            async with self._db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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

        results = tuple(self._row_to_entry(dict(row)) for row in rows)
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
        offset: int,
    ) -> None:
        """Validate query parameters before execution.

        Raises:
            QueryError: If *limit* < 1, *offset* < 0, *since* or *until*
                is naive, or *until* < *since*.
        """
        if limit < 1:
            msg = f"limit must be >= 1, got {limit}"
            logger.warning(
                PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                error=msg,
                limit=limit,
            )
            raise QueryError(msg)

        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            logger.warning(
                PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                error=msg,
                offset=offset,
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
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if action_type is not None:
            conditions.append("action_type = ?")
            params.append(action_type)
        if verdict is not None:
            conditions.append("verdict = ?")
            params.append(verdict)
        if risk_level is not None:
            conditions.append("risk_level = ?")
            params.append(risk_level.value)
        if since is not None:
            conditions.append("timestamp >= ?")
            params.append(since.astimezone(UTC).isoformat())
        if until is not None:
            conditions.append("timestamp <= ?")
            params.append(until.astimezone(UTC).isoformat())

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        return where, params

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
        utc_cutoff = cutoff.astimezone(UTC).isoformat()
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM audit_entries WHERE timestamp < ?",
                    (utc_cutoff,),
                ) as cursor:
                    await self._db.commit()
                    _db_rowcount = cursor.rowcount
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                msg = "Failed to purge audit entries"
                logger.warning(
                    PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    cutoff=utc_cutoff,
                )
                raise QueryError(msg) from exc
        return _db_rowcount

    def _row_to_entry(self, row: dict[str, object]) -> AuditEntry:
        """Convert a database row to an ``AuditEntry`` model.

        Delegates to :func:`row_to_audit_entry` from the shared helper
        so SQLite and Postgres use identical deserialisation logic.

        Args:
            row: A dict mapping column names to their values.

        Raises:
            QueryError: If the row cannot be deserialized.

        Returns:
            Result of type ``AuditEntry``.
        """
        return row_to_audit_entry(row)
