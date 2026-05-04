"""SQLite repository implementation for security audit entries."""

import asyncio
import json
import sqlite3
from datetime import UTC
from typing import TYPE_CHECKING

import aiosqlite

from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_AUDIT_ENTRY_QUERIED,
    PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
)
from synthorg.persistence._shared.audit import (
    AUDIT_COLUMNS,
    audit_entry_to_payload,
    classify_audit_save_error,
    row_to_audit_entry,
)

if TYPE_CHECKING:
    from pydantic import AwareDatetime

    from synthorg.core.enums import ApprovalRiskLevel
    from synthorg.core.types import NotBlankStr
    from synthorg.security.models import AuditEntry, AuditVerdictStr

from synthorg.persistence.sqlite._shared import is_unique_constraint_error

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
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_lock: asyncio.Lock | None = None,
    ) -> None:
        self._db = db
        # Inject the shared backend write lock so writes from this repo
        # serialize with sibling repos that share the same
        # ``aiosqlite.Connection``; fall back to a private lock for
        # standalone test construction.
        self._write_lock = write_lock if write_lock is not None else asyncio.Lock()

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
            json_serializer=json.dumps,
            timestamp_serializer=lambda dt: dt.isoformat(),
        )
        placeholders = ", ".join(f":{c}" for c in AUDIT_COLUMNS)
        sql = f"INSERT INTO audit_entries ({_COL_LIST}) VALUES ({placeholders})"  # noqa: S608
        async with self._write_lock:
            try:
                await self._db.execute(sql, payload)
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                raise classify_audit_save_error(
                    exc,
                    entry_id=entry.id,
                    is_duplicate=is_unique_constraint_error,
                ) from exc

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

    async def query(  # noqa: PLR0913
        self,
        *,
        agent_id: NotBlankStr | None = None,
        action_type: NotBlankStr | None = None,
        verdict: AuditVerdictStr | None = None,
        risk_level: ApprovalRiskLevel | None = None,
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
        limit: int = 100,
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
            "ORDER BY timestamp DESC LIMIT ?"
        )
        params.append(limit)

        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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

        results = tuple(self._row_to_entry(dict(row)) for row in rows)
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
        utc_cutoff = cutoff.astimezone(UTC).isoformat()
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    "DELETE FROM audit_entries WHERE timestamp < ?",
                    (utc_cutoff,),
                )
                await self._db.commit()
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
        return cursor.rowcount

    def _row_to_entry(self, row: dict[str, object]) -> AuditEntry:
        """Convert a database row to an ``AuditEntry`` model.

        Delegates to :func:`row_to_audit_entry` from the shared helper
        so SQLite and Postgres use identical deserialisation logic.

        Args:
            row: A dict mapping column names to their values.

        Raises:
            QueryError: If the row cannot be deserialized.
        """
        return row_to_audit_entry(row)
