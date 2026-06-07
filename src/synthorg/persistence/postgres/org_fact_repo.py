# module-kind: repository
"""Postgres-backed org fact repository with MVCC (append-only log + snapshot).

Row <-> model marshalling is shared with the SQLite sibling via
:mod:`synthorg.persistence._shared.org_fact_marshalling`; the
point-in-time ``snapshot_at`` query lives in
:mod:`synthorg.persistence.postgres._org_fact_sql`.
"""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from psycopg.rows import TupleRow, dict_row

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import AutonomyLevel
from synthorg.core.types import NotBlankStr
from synthorg.hr.seniority import SeniorityLevel
from synthorg.memory.enums import OrgFactCategory
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
    ORG_MEMORY_WRITE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc, validate_pagination_args
from synthorg.persistence._shared.org_fact_marshalling import (
    row_to_operation_log_entry,
    row_to_snapshot,
    snapshot_row_to_org_fact,
    tags_from_json,
    tags_to_json,
)
from synthorg.persistence.memory_protocol import _DEFAULT_LIST_LIMIT_FACTS
from synthorg.persistence.postgres._org_fact_sql import SNAPSHOT_AT_SQL

if TYPE_CHECKING:
    import psycopg
    from psycopg_pool import AsyncConnectionPool


logger = get_logger(__name__)


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

    async def _append_to_operation_log(  # noqa: PLR0913
        self,
        conn: psycopg.AsyncConnection[TupleRow],
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
        """Append an operation within the caller's transaction.

        Returns:
            ``(version, persisted_at)`` of the newly appended log entry.
        """
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
                    tags_to_json(tags),
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
        """Publish a fact: append PUBLISH to log, upsert snapshot.

        Raises:
            OrgMemoryWriteError: If the underlying call raises.
        """
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
                            tags_to_json(fact.tags),
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
            reraise_critical(exc)
            logger.warning(
                ORG_MEMORY_WRITE_FAILED,
                fact_id=fact.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to save org fact: {safe_error_description(exc)}"
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
        """Retract a fact: append RETRACT to log, mark snapshot.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            OrgMemoryWriteError: If the underlying call raises.
        """
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
                    tags=tags_from_json(row["tags"]),
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
            reraise_critical(exc)
            logger.warning(
                ORG_MEMORY_WRITE_FAILED,
                fact_id=fact_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to delete org fact: {safe_error_description(exc)}"
            raise OrgMemoryWriteError(msg) from exc
        else:
            logger.info(
                ORG_MEMORY_MVCC_RETRACT_APPENDED,
                fact_id=fact_id,
                version=version,
            )
            return True

    async def get(self, fact_id: NotBlankStr) -> OrgFact | None:
        """Get an active fact by its ID.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            OrgMemoryQueryError: If the underlying call raises.
        """
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
            reraise_critical(exc)
            logger.warning(
                ORG_MEMORY_QUERY_FAILED,
                fact_id=fact_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to get org fact: {safe_error_description(exc)}"
            raise OrgMemoryQueryError(msg) from exc
        if row is None:
            return None
        return snapshot_row_to_org_fact(row)

    async def query(
        self,
        *,
        categories: frozenset[OrgFactCategory] | None = None,
        text: str | None = None,
        limit: int = _DEFAULT_LIST_LIMIT_FACTS,
        offset: int = 0,
    ) -> tuple[OrgFact, ...]:
        """Query active facts by category and/or text content.

        Returns:
            The matching entities.

        Raises:
            OrgMemoryQueryError: If the underlying call raises.
        """
        limit = max(1, min(limit, 100))
        offset = max(0, int(offset))
        clauses: list[str] = ["retracted_at IS NULL"]
        params: list[object] = []

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
                "LENGTH(content) ASC, created_at DESC, fact_id ASC"
            )
            params.append(text)
        else:
            order = "ORDER BY created_at DESC, fact_id ASC"
        sql = (
            f"SELECT * FROM org_facts_snapshot{where} {order} "  # noqa: S608
            "LIMIT %s OFFSET %s"
        )
        params.extend([limit, offset])

        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                ORG_MEMORY_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to query org facts: {safe_error_description(exc)}"
            raise OrgMemoryQueryError(msg) from exc
        return tuple(snapshot_row_to_org_fact(row) for row in rows)

    async def list_by_category(
        self,
        category: OrgFactCategory,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[OrgFact, ...]:
        """List all active facts in a category, optionally paginated.

        Returns:
            The matching entities.

        Raises:
            OrgMemoryQueryError: If the underlying call raises.
        """
        sql = (
            "SELECT * FROM org_facts_snapshot "
            "WHERE category = %s AND retracted_at IS NULL "
            "ORDER BY created_at DESC, fact_id ASC"
        )
        params: tuple[object, ...] = (category.value,)
        # Clamp ``limit`` at the repository boundary: PostgreSQL
        # rejects negative LIMIT (SQLSTATE 2201W) and an unbounded
        # value would defeat the page-size invariant the protocol
        # documents. Mirrors the clamp on the sibling ``query``
        # method so both paths share the same bounded contract.
        effective_limit = max(1, min(int(limit), 100))
        effective_offset = max(0, int(offset))
        sql += " LIMIT %s OFFSET %s"
        params = (*params, effective_limit, effective_offset)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                ORG_MEMORY_QUERY_FAILED,
                category=category.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to list org facts by category: {safe_error_description(exc)}"
            raise OrgMemoryQueryError(msg) from exc
        return tuple(snapshot_row_to_org_fact(row) for row in rows)

    async def snapshot_at(
        self,
        timestamp: datetime,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[OperationLogSnapshot, ...]:
        """Bounded page of the point-in-time snapshot of all facts.

        ``timestamp`` must be timezone-aware so psycopg binds it to the
        ``TIMESTAMPTZ`` parameter at a known instant; a naive datetime
        would otherwise bind in the session timezone and silently
        produce a wrong-but-plausible snapshot.  The parameter is a
        :class:`datetime.datetime`; callers must pass an aware value.
        Rows page in ``fact_id`` order so a cursor walk is repeatable
        across the same snapshot; callers needing the whole snapshot
        drain via :func:`synthorg.persistence._shared.collect_all`.

        Returns:
            The matching collection.

        Raises:
            ValueError: If an argument fails validation.
            OrgMemoryQueryError: If the underlying call raises.
        """
        limit = validate_pagination_args(limit, offset, event=ORG_MEMORY_QUERY_FAILED)
        if timestamp.tzinfo is None:
            msg = (
                "snapshot_at requires a timezone-aware timestamp, "
                f"got naive {timestamp!r}"
            )
            raise ValueError(msg)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    SNAPSHOT_AT_SQL,
                    {"ts": timestamp, "limit": limit, "offset": offset},
                )
                rows = await cur.fetchall()
        except Exception as exc:
            reraise_critical(exc)
            ts_iso = timestamp.isoformat()
            error_desc = safe_error_description(exc)
            logger.warning(
                ORG_MEMORY_QUERY_FAILED,
                timestamp=ts_iso,
                error_type=type(exc).__name__,
                error=error_desc,
            )
            msg = f"Failed to query snapshot at {ts_iso}: {error_desc}"
            raise OrgMemoryQueryError(msg) from exc
        result = tuple(row_to_snapshot(row) for row in rows)
        logger.debug(
            ORG_MEMORY_MVCC_SNAPSHOT_AT_QUERIED,
            timestamp=timestamp.isoformat(),
            count=len(result),
        )
        return result

    async def get_operation_log(
        self,
        fact_id: NotBlankStr,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[OperationLogEntry, ...]:
        """Bounded page of the audit trail for a fact (version ASC).

        Version is unique per fact so the ordering is already stable;
        callers needing the full trail drain via
        :func:`synthorg.persistence._shared.collect_all`.

        Returns:
            Tuple of matching rows; empty when no rows match.

        Raises:
            OrgMemoryQueryError: If the underlying call raises.
        """
        limit = validate_pagination_args(
            limit, offset, event=ORG_MEMORY_QUERY_FAILED, fact_id=fact_id
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT * FROM org_facts_operation_log "
                    "WHERE fact_id = %s ORDER BY version ASC "
                    "LIMIT %s OFFSET %s",
                    (fact_id, limit, offset),
                )
                rows = await cur.fetchall()
        except Exception as exc:
            reraise_critical(exc)
            error_desc = safe_error_description(exc)
            logger.warning(
                ORG_MEMORY_QUERY_FAILED,
                fact_id=fact_id,
                error_type=type(exc).__name__,
                error=error_desc,
            )
            msg = f"Failed to get operation log for {fact_id}: {error_desc}"
            raise OrgMemoryQueryError(msg) from exc
        result = tuple(row_to_operation_log_entry(row) for row in rows)
        logger.debug(
            ORG_MEMORY_MVCC_LOG_QUERIED,
            fact_id=fact_id,
            count=len(result),
        )
        return result


__all__ = ["PostgresOrgFactRepository"]
