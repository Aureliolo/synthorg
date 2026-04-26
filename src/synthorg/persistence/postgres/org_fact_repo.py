"""Postgres-backed org fact repository with MVCC (append-only log + snapshot)."""

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ValidationError

from synthorg.core.enums import (
    AutonomyLevel,
    OrgFactCategory,
    SeniorityLevel,
)
from synthorg.core.types import NotBlankStr
from synthorg.memory.org.errors import (
    OrgMemoryQueryError,
    OrgMemoryWriteError,
)
from synthorg.memory.org.models import (
    OperationLogEntry,
    OperationLogSnapshot,
    OrgFact,
    OrgFactAuthor,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.org_memory import (
    ORG_MEMORY_MVCC_LOG_QUERIED,
    ORG_MEMORY_MVCC_PUBLISH_APPENDED,
    ORG_MEMORY_MVCC_RETRACT_APPENDED,
    ORG_MEMORY_MVCC_SNAPSHOT_AT_QUERIED,
    ORG_MEMORY_QUERY_FAILED,
    ORG_MEMORY_ROW_PARSE_FAILED,
    ORG_MEMORY_WRITE_FAILED,
)
from synthorg.persistence._shared import normalize_utc

if TYPE_CHECKING:
    import psycopg
    from psycopg_pool import AsyncConnectionPool


def _import_dict_row() -> Any:
    """Lazily resolve ``psycopg.rows.dict_row``."""
    from psycopg.rows import dict_row  # noqa: PLC0415

    return dict_row


logger = get_logger(__name__)


def _tags_to_json(tags: tuple[NotBlankStr, ...]) -> str:
    """Serialize tags tuple to sorted JSON array."""
    return json.dumps(sorted(tags))


def _tags_from_json(raw: str | list[Any]) -> tuple[NotBlankStr, ...]:
    """Deserialize tags (JSON string or JSONB-decoded list) to tuple."""
    parsed = raw if isinstance(raw, list) else json.loads(raw)
    if not isinstance(parsed, list):
        msg = f"Tags must be a JSON array, got {type(parsed).__name__}"
        logger.warning(ORG_MEMORY_ROW_PARSE_FAILED, error=msg)
        raise OrgMemoryQueryError(msg)
    if any(not isinstance(t, str) or not t.strip() for t in parsed):
        msg = "Tags must be a JSON array of non-blank strings"
        logger.warning(ORG_MEMORY_ROW_PARSE_FAILED, error=msg)
        raise OrgMemoryQueryError(msg)
    return tuple(NotBlankStr(t) for t in parsed)


def _snapshot_row_to_org_fact(row: dict[str, Any]) -> OrgFact:
    """Reconstruct an ``OrgFact`` from a snapshot row."""
    try:
        author = OrgFactAuthor(
            agent_id=row["author_agent_id"],
            seniority=(
                SeniorityLevel(row["author_seniority"])
                if row["author_seniority"]
                else None
            ),
            autonomy_level=(
                AutonomyLevel(row["author_autonomy_level"])
                if row["author_autonomy_level"]
                else None
            ),
            is_human=bool(row["author_is_human"]),
        )
        return OrgFact(
            id=row["fact_id"],
            content=row["content"],
            category=OrgFactCategory(row["category"]),
            tags=_tags_from_json(row["tags"]),
            author=author,
            created_at=normalize_utc(row["created_at"]),
        )
    except (KeyError, ValueError, ValidationError, OrgMemoryQueryError) as exc:
        logger.warning(
            ORG_MEMORY_ROW_PARSE_FAILED,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        msg = f"Failed to deserialize snapshot row: {exc}"
        raise OrgMemoryQueryError(msg) from exc


def _row_to_operation_log_entry(row: dict[str, Any]) -> OperationLogEntry:
    """Reconstruct an ``OperationLogEntry`` from a database row."""
    try:
        return OperationLogEntry(
            operation_id=row["operation_id"],
            fact_id=row["fact_id"],
            operation_type=row["operation_type"],
            content=row["content"],
            category=(OrgFactCategory(row["category"]) if row["category"] else None),
            tags=_tags_from_json(row["tags"]),
            author_agent_id=row["author_agent_id"],
            author_seniority=(
                SeniorityLevel(row["author_seniority"])
                if row["author_seniority"]
                else None
            ),
            author_is_human=bool(row["author_is_human"]),
            author_autonomy_level=(
                AutonomyLevel(row["author_autonomy_level"])
                if row["author_autonomy_level"]
                else None
            ),
            timestamp=normalize_utc(row["timestamp"]),
            version=row["version"],
        )
    except (KeyError, ValueError, ValidationError, OrgMemoryQueryError) as exc:
        logger.warning(
            ORG_MEMORY_ROW_PARSE_FAILED,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        msg = f"Failed to deserialize operation log row: {exc}"
        raise OrgMemoryQueryError(msg) from exc


def _row_to_snapshot(row: dict[str, Any]) -> OperationLogSnapshot:
    """Reconstruct an ``OperationLogSnapshot`` from a time-travel query row."""
    try:
        op_type: str = row["operation_type"]
        retracted_at = normalize_utc(row["timestamp"]) if op_type == "RETRACT" else None
        created_at_raw = row.get("created_at")
        created_at = normalize_utc(created_at_raw or row["timestamp"])
        return OperationLogSnapshot(
            fact_id=row["fact_id"],
            content=row["content"],
            category=OrgFactCategory(row["category"]),
            tags=_tags_from_json(row["tags"]),
            created_at=created_at,
            retracted_at=retracted_at,
            version=row["version"],
        )
    except (KeyError, ValueError, ValidationError, OrgMemoryQueryError) as exc:
        logger.warning(
            ORG_MEMORY_ROW_PARSE_FAILED,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        msg = f"Failed to deserialize snapshot_at row: {exc}"
        raise OrgMemoryQueryError(msg) from exc


class PostgresOrgFactRepository:
    """Postgres-backed organizational fact repository with MVCC.

    Uses the shared :class:`AsyncConnectionPool`.  Each operation
    acquires a connection via ``async with pool.connection()`` which
    auto-commits on clean exit.  Writes explicitly call
    ``conn.transaction()`` so the operation-log insert and snapshot
    upsert happen atomically.

    Args:
        pool: An open ``psycopg_pool.AsyncConnectionPool``.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool
        self._dict_row = _import_dict_row()

    async def _append_to_operation_log(  # noqa: PLR0913
        self,
        conn: psycopg.AsyncConnection[Any],
        *,
        fact_id: str,
        operation_type: Literal["PUBLISH", "RETRACT"],
        content: str | None,
        category: OrgFactCategory | None,
        tags: tuple[NotBlankStr, ...],
        author_agent_id: str | None,
        author_seniority: SeniorityLevel | None,
        author_is_human: bool,
        author_autonomy_level: AutonomyLevel | None,
    ) -> tuple[int, datetime]:
        """Append an operation within the caller's transaction."""
        operation_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COALESCE(MAX(version), 0) "
                "FROM org_facts_operation_log WHERE fact_id = %s",
                (fact_id,),
            )
            row = await cur.fetchone()
            current: int = row[0] if row is not None else 0
            next_version = current + 1
            await cur.execute(
                "INSERT INTO org_facts_operation_log "
                "(operation_id, fact_id, operation_type, content, "
                "tags, author_agent_id, author_seniority, "
                "author_is_human, author_autonomy_level, category, "
                "timestamp, version) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    operation_id,
                    fact_id,
                    operation_type,
                    content,
                    _tags_to_json(tags),
                    author_agent_id,
                    (author_seniority.value if author_seniority else None),
                    author_is_human,
                    (author_autonomy_level.value if author_autonomy_level else None),
                    (category.value if category else None),
                    now,
                    next_version,
                ),
            )
        return next_version, now

    async def save(self, fact: OrgFact) -> None:
        """Publish a fact: append PUBLISH to log, upsert snapshot."""
        try:
            async with self._pool.connection() as conn, conn.transaction():
                version, _ = await self._append_to_operation_log(
                    conn,
                    fact_id=fact.id,
                    operation_type="PUBLISH",
                    content=fact.content,
                    category=fact.category,
                    tags=fact.tags,
                    author_agent_id=fact.author.agent_id,
                    author_seniority=fact.author.seniority,
                    author_is_human=fact.author.is_human,
                    author_autonomy_level=fact.author.autonomy_level,
                )
                async with conn.cursor() as cur:
                    await cur.execute(
                        "INSERT INTO org_facts_snapshot "
                        "(fact_id, content, category, tags, "
                        "author_agent_id, author_seniority, "
                        "author_is_human, "
                        "author_autonomy_level, created_at, "
                        "retracted_at, version) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s) "
                        "ON CONFLICT (fact_id) DO UPDATE SET "
                        "content=EXCLUDED.content, "
                        "category=EXCLUDED.category, "
                        "tags=EXCLUDED.tags, "
                        "author_agent_id=EXCLUDED.author_agent_id, "
                        "author_seniority=EXCLUDED.author_seniority, "
                        "author_is_human=EXCLUDED.author_is_human, "
                        "author_autonomy_level="
                        "EXCLUDED.author_autonomy_level, "
                        "retracted_at=NULL, "
                        "version=EXCLUDED.version",
                        (
                            fact.id,
                            fact.content,
                            fact.category.value,
                            _tags_to_json(fact.tags),
                            fact.author.agent_id,
                            (
                                fact.author.seniority.value
                                if fact.author.seniority
                                else None
                            ),
                            fact.author.is_human,
                            (
                                fact.author.autonomy_level.value
                                if fact.author.autonomy_level
                                else None
                            ),
                            normalize_utc(fact.created_at),
                            version,
                        ),
                    )
        except Exception as exc:
            logger.warning(
                ORG_MEMORY_WRITE_FAILED,
                fact_id=fact.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to save org fact: {exc}"
            raise OrgMemoryWriteError(msg) from exc
        else:
            logger.info(
                ORG_MEMORY_MVCC_PUBLISH_APPENDED,
                fact_id=fact.id,
                version=version,
            )

    async def delete(
        self,
        fact_id: NotBlankStr,
        *,
        author: OrgFactAuthor,
    ) -> bool:
        """Retract a fact: append RETRACT to log, mark snapshot."""
        dict_row = self._dict_row
        try:
            async with self._pool.connection() as conn, conn.transaction():
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        "SELECT fact_id, category, tags "
                        "FROM org_facts_snapshot "
                        "WHERE fact_id = %s "
                        "AND retracted_at IS NULL",
                        (fact_id,),
                    )
                    row = await cur.fetchone()
                if row is None:
                    return False
                version, now = await self._append_to_operation_log(
                    conn,
                    fact_id=fact_id,
                    operation_type="RETRACT",
                    content=None,
                    category=(
                        OrgFactCategory(row["category"]) if row["category"] else None
                    ),
                    tags=_tags_from_json(row["tags"]),
                    author_agent_id=author.agent_id,
                    author_seniority=author.seniority,
                    author_is_human=author.is_human,
                    author_autonomy_level=author.autonomy_level,
                )
                async with conn.cursor() as cur:
                    await cur.execute(
                        "UPDATE org_facts_snapshot "
                        "SET retracted_at = %s, version = %s "
                        "WHERE fact_id = %s",
                        (now, version, fact_id),
                    )
        except Exception as exc:
            logger.warning(
                ORG_MEMORY_WRITE_FAILED,
                fact_id=fact_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to delete org fact: {exc}"
            raise OrgMemoryWriteError(msg) from exc
        else:
            logger.info(
                ORG_MEMORY_MVCC_RETRACT_APPENDED,
                fact_id=fact_id,
                version=version,
            )
            return True

    async def get(self, fact_id: NotBlankStr) -> OrgFact | None:
        """Get an active fact by its ID."""
        dict_row = self._dict_row
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT * FROM org_facts_snapshot "
                    "WHERE fact_id = %s AND retracted_at IS NULL",
                    (fact_id,),
                )
                row = await cur.fetchone()
        except Exception as exc:
            logger.warning(
                ORG_MEMORY_QUERY_FAILED,
                fact_id=fact_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to get org fact: {exc}"
            raise OrgMemoryQueryError(msg) from exc
        if row is None:
            return None
        return _snapshot_row_to_org_fact(row)

    async def query(
        self,
        *,
        categories: frozenset[OrgFactCategory] | None = None,
        text: str | None = None,
        limit: int = 5,
    ) -> tuple[OrgFact, ...]:
        """Query active facts by category and/or text content."""
        dict_row = self._dict_row
        limit = max(1, min(limit, 100))
        clauses: list[str] = ["retracted_at IS NULL"]
        params: list[Any] = []

        if categories is not None and categories:
            placeholders = ",".join("%s" for _ in categories)
            clauses.append(f"category IN ({placeholders})")
            params.extend(c.value for c in categories)

        if text is not None:
            escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("content LIKE %s ESCAPE '\\'")
            params.append(f"%{escaped}%")

        where = f" WHERE {' AND '.join(clauses)}"
        if text is not None:
            order = (
                "ORDER BY POSITION(LOWER(%s) IN LOWER(content)) ASC, "
                "LENGTH(content) ASC, created_at DESC"
            )
            params.append(text)
        else:
            order = "ORDER BY created_at DESC"
        sql = f"SELECT * FROM org_facts_snapshot{where} {order} LIMIT %s"  # noqa: S608
        params.append(limit)

        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except Exception as exc:
            logger.warning(
                ORG_MEMORY_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to query org facts: {exc}"
            raise OrgMemoryQueryError(msg) from exc
        return tuple(_snapshot_row_to_org_fact(row) for row in rows)

    async def list_by_category(
        self,
        category: OrgFactCategory,
    ) -> tuple[OrgFact, ...]:
        """List all active facts in a category."""
        dict_row = self._dict_row
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT * FROM org_facts_snapshot "
                    "WHERE category = %s AND retracted_at IS NULL "
                    "ORDER BY created_at DESC",
                    (category.value,),
                )
                rows = await cur.fetchall()
        except Exception as exc:
            logger.warning(
                ORG_MEMORY_QUERY_FAILED,
                category=category.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to list org facts by category: {exc}"
            raise OrgMemoryQueryError(msg) from exc
        return tuple(_snapshot_row_to_org_fact(row) for row in rows)

    async def snapshot_at(
        self,
        timestamp: datetime,
    ) -> tuple[OperationLogSnapshot, ...]:
        """Point-in-time snapshot of all facts at a given timestamp."""
        dict_row = self._dict_row
        timestamp = normalize_utc(timestamp)
        sql = """\
WITH latest_ops AS (
    SELECT fact_id, operation_type, content, tags, category,
           timestamp, version,
           ROW_NUMBER() OVER (
               PARTITION BY fact_id ORDER BY version DESC
           ) AS rn
    FROM org_facts_operation_log
    WHERE timestamp <= %(ts)s
),
latest_publishes AS (
    SELECT DISTINCT ON (fact_id)
           fact_id, content, tags, category
    FROM org_facts_operation_log
    WHERE operation_type = 'PUBLISH'
      AND timestamp <= %(ts)s
    ORDER BY fact_id, version DESC
),
first_publishes AS (
    SELECT fact_id, MIN(timestamp) AS created_at
    FROM org_facts_operation_log
    WHERE operation_type = 'PUBLISH'
      AND timestamp <= %(ts)s
    GROUP BY fact_id
)
SELECT lo.fact_id, lo.operation_type,
       COALESCE(lo.content, lp.content) AS content,
       COALESCE(lo.category, lp.category) AS category,
       COALESCE(
           CASE WHEN lo.operation_type = 'PUBLISH' THEN lo.tags END,
           lp.tags
       ) AS tags,
       lo.version, lo.timestamp,
       fp.created_at AS created_at
FROM latest_ops lo
LEFT JOIN latest_publishes lp ON lp.fact_id = lo.fact_id
LEFT JOIN first_publishes fp ON fp.fact_id = lo.fact_id
WHERE lo.rn = 1
ORDER BY lo.fact_id
"""
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, {"ts": timestamp})
                rows = await cur.fetchall()
        except Exception as exc:
            logger.warning(
                ORG_MEMORY_QUERY_FAILED,
                timestamp=timestamp.isoformat(),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to query snapshot at {timestamp.isoformat()}: {exc}"
            raise OrgMemoryQueryError(msg) from exc
        result = tuple(_row_to_snapshot(row) for row in rows)
        logger.debug(
            ORG_MEMORY_MVCC_SNAPSHOT_AT_QUERIED,
            timestamp=timestamp.isoformat(),
            count=len(result),
        )
        return result

    async def get_operation_log(
        self,
        fact_id: NotBlankStr,
    ) -> tuple[OperationLogEntry, ...]:
        """Retrieve full audit trail for a fact."""
        dict_row = self._dict_row
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT * FROM org_facts_operation_log "
                    "WHERE fact_id = %s ORDER BY version ASC",
                    (fact_id,),
                )
                rows = await cur.fetchall()
        except Exception as exc:
            logger.warning(
                ORG_MEMORY_QUERY_FAILED,
                fact_id=fact_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to get operation log for {fact_id}: {exc}"
            raise OrgMemoryQueryError(msg) from exc
        result = tuple(_row_to_operation_log_entry(row) for row in rows)
        logger.debug(
            ORG_MEMORY_MVCC_LOG_QUERIED,
            fact_id=fact_id,
            count=len(result),
        )
        return result
