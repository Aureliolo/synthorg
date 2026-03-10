"""SQLite repository implementation for security audit entries."""

import json
import sqlite3
from typing import TYPE_CHECKING

import aiosqlite
from pydantic import ValidationError

from ai_company.observability import get_logger
from ai_company.observability.events.persistence import (
    PERSISTENCE_AUDIT_ENTRY_DESERIALIZE_FAILED,
    PERSISTENCE_AUDIT_ENTRY_QUERIED,
    PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
    PERSISTENCE_AUDIT_ENTRY_SAVE_FAILED,
    PERSISTENCE_AUDIT_ENTRY_SAVED,
)
from ai_company.persistence.errors import DuplicateRecordError, QueryError
from ai_company.security.models import AuditEntry

if TYPE_CHECKING:
    from pydantic import AwareDatetime

    from ai_company.core.enums import ApprovalRiskLevel
    from ai_company.core.types import NotBlankStr
    from ai_company.security.models import AuditVerdictStr

logger = get_logger(__name__)


class SQLiteAuditRepository:
    """SQLite implementation of the AuditRepository protocol.

    Append-only: entries can be saved and queried, but never updated
    or deleted, preserving audit integrity.

    Args:
        db: An open aiosqlite connection.
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def save(self, entry: AuditEntry) -> None:
        """Persist an audit entry (append-only, no upsert).

        Args:
            entry: The audit entry to persist.

        Raises:
            DuplicateRecordError: If an entry with the same ID exists.
            QueryError: If the operation fails.
        """
        try:
            data = entry.model_dump(mode="json")
            await self._db.execute(
                """\
INSERT INTO audit_entries (
    id, timestamp, agent_id, task_id, tool_name, tool_category,
    action_type, arguments_hash, verdict, risk_level, reason,
    matched_rules, evaluation_duration_ms, approval_id
) VALUES (
    :id, :timestamp, :agent_id, :task_id, :tool_name, :tool_category,
    :action_type, :arguments_hash, :verdict, :risk_level, :reason,
    :matched_rules, :evaluation_duration_ms, :approval_id
)""",
                {
                    **data,
                    "matched_rules": json.dumps(list(data["matched_rules"])),
                },
            )
            await self._db.commit()
        except sqlite3.IntegrityError as exc:
            msg = f"Duplicate audit entry {entry.id!r}"
            logger.warning(
                PERSISTENCE_AUDIT_ENTRY_SAVE_FAILED,
                entry_id=entry.id,
                error=str(exc),
            )
            raise DuplicateRecordError(msg) from exc
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to save audit entry {entry.id!r}"
            logger.exception(
                PERSISTENCE_AUDIT_ENTRY_SAVE_FAILED,
                entry_id=entry.id,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(
            PERSISTENCE_AUDIT_ENTRY_SAVED,
            entry_id=entry.id,
            agent_id=entry.agent_id,
        )

    async def query(  # noqa: PLR0913
        self,
        *,
        agent_id: NotBlankStr | None = None,
        action_type: str | None = None,
        verdict: AuditVerdictStr | None = None,
        risk_level: ApprovalRiskLevel | None = None,
        since: AwareDatetime | None = None,
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
            limit: Maximum number of entries (must be >= 1).

        Returns:
            Matching audit entries as a tuple.

        Raises:
            QueryError: If the operation fails or *limit* < 1.
        """
        if limit < 1:
            msg = f"limit must be >= 1, got {limit}"
            logger.warning(
                PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                error=msg,
                limit=limit,
            )
            raise QueryError(msg)

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
            params.append(since.isoformat())

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        cols = (
            "id, timestamp, agent_id, task_id, tool_name, "
            "tool_category, action_type, arguments_hash, verdict, "
            "risk_level, reason, matched_rules, "
            "evaluation_duration_ms, approval_id"
        )

        sql = f"SELECT {cols} FROM audit_entries{where} ORDER BY timestamp DESC LIMIT ?"  # noqa: S608
        params.append(limit)

        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query audit entries"
            logger.exception(
                PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                error=str(exc),
                agent_id=agent_id,
                action_type=action_type,
                verdict=verdict,
                risk_level=risk_level.value if risk_level else None,
                limit=limit,
            )
            raise QueryError(msg) from exc

        results = tuple(self._row_to_entry(dict(row)) for row in rows)

        logger.debug(
            PERSISTENCE_AUDIT_ENTRY_QUERIED,
            count=len(results),
        )
        return results

    def _row_to_entry(self, row: dict[str, object]) -> AuditEntry:
        """Convert a database row to an ``AuditEntry`` model.

        Raises:
            QueryError: If the row cannot be deserialized.
        """
        try:
            raw_rules = row.get("matched_rules")
            if isinstance(raw_rules, str):
                row = {**row, "matched_rules": json.loads(raw_rules)}
            return AuditEntry.model_validate(row)
        except (ValidationError, json.JSONDecodeError, KeyError, TypeError) as exc:
            msg = f"Failed to deserialize audit entry {row.get('id')!r}"
            logger.exception(
                PERSISTENCE_AUDIT_ENTRY_DESERIALIZE_FAILED,
                entry_id=row.get("id"),
                error=str(exc),
            )
            raise QueryError(msg) from exc
