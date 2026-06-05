# module-kind: repository
"""SQLite-backed org fact repository with MVCC (append-only log + snapshot).

Row <-> model marshalling is shared with the Postgres sibling via
:mod:`synthorg.persistence._shared.org_fact_marshalling`; the
point-in-time ``snapshot_at`` query lives in
:mod:`synthorg.persistence.sqlite._org_fact_sql`.
"""

import contextlib
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Literal

import aiosqlite

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
    ORG_MEMORY_WRITE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import format_iso_utc, validate_pagination_args
from synthorg.persistence._shared.org_fact_marshalling import (
    row_to_operation_log_entry,
    row_to_snapshot,
    snapshot_row_to_org_fact,
    tags_from_json,
    tags_to_json,
)
from synthorg.persistence.memory_protocol import _DEFAULT_LIST_LIMIT_FACTS
from synthorg.persistence.sqlite._org_fact_sql import SNAPSHOT_AT_SQL
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)


class SQLiteOrgFactRepository:
    """SQLite-backed organizational fact repository with MVCC.

    All writes are appended to an operation log; a materialized
    snapshot table maintains the current committed state.  Reads
    query the snapshot.  Time-travel queries replay the log.

    Args:
        db: Open aiosqlite connection with ``row_factory`` set.
        write_context: Async context manager that serializes writes on
            the shared connection. Supplied by
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

    async def _append_to_operation_log(  # noqa: PLR0913
        self,
        db: aiosqlite.Connection,
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
            ``(log_id, persisted_at)`` of the newly appended log entry.
        """
        operation_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        cursor = await db.execute(
            "SELECT COALESCE(MAX(version), 0) "
            "FROM org_facts_operation_log WHERE fact_id = ?",
            (fact_id,),
        )
        row = await cursor.fetchone()
        current: int = row[0] if row is not None else 0
        next_version = current + 1
        await db.execute(
            "INSERT INTO org_facts_operation_log "
            "(operation_id, fact_id, operation_type, content, "
            "tags, author_agent_id, author_seniority, "
            "author_is_human, author_autonomy_level, category, "
            "timestamp, version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                operation_id,
                fact_id,
                operation_type,
                content,
                tags_to_json(tags),
                author_agent_id,
                (author_seniority.value if author_seniority else None),
                int(author_is_human),
                (author_autonomy_level.value if author_autonomy_level else None),
                (category.value if category else None),
                format_iso_utc(now),
                next_version,
            ),
        )
        return next_version, now

    async def save(self, fact: OrgFact) -> None:
        """Publish a fact: append PUBLISH to log, upsert snapshot.

        Raises:
            OrgMemoryWriteError: If the underlying call raises.
        """
        db = self._db
        # Marshal every Python value into its SQLite-bound shape BEFORE
        # the transaction opens.  ``format_iso_utc`` raises
        # ``ValueError`` on a naive ``created_at`` (defending against
        # a regression that lets a naive value reach the database);
        # doing the marshal up here keeps that error path outside the
        # ``try`` block, so we never strand a ``BEGIN IMMEDIATE``
        # transaction holding the write lock.
        created_at_iso = format_iso_utc(fact.created_at)
        tags_json = tags_to_json(fact.tags)
        async with self._write_context():
            try:
                await db.execute("BEGIN IMMEDIATE")
                version, _ = await self._append_to_operation_log(
                    db,
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
                await db.execute(
                    "INSERT INTO org_facts_snapshot "
                    "(fact_id, content, category, tags, "
                    "author_agent_id, author_seniority, "
                    "author_is_human, "
                    "author_autonomy_level, created_at, "
                    "retracted_at, version) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?) "
                    "ON CONFLICT(fact_id) DO UPDATE SET "
                    "content=excluded.content, "
                    "category=excluded.category, "
                    "tags=excluded.tags, "
                    "author_agent_id=excluded.author_agent_id, "
                    "author_seniority=excluded.author_seniority, "
                    "author_is_human=excluded.author_is_human, "
                    "author_autonomy_level="
                    "excluded.author_autonomy_level, "
                    "retracted_at=NULL, "
                    "version=excluded.version",
                    (
                        fact.id,
                        fact.content,
                        fact.category.value,
                        tags_json,
                        fact.author.agent_id,
                        (
                            fact.author.seniority.value
                            if fact.author.seniority
                            else None
                        ),
                        int(fact.author.is_human),
                        (
                            fact.author.autonomy_level.value
                            if fact.author.autonomy_level
                            else None
                        ),
                        created_at_iso,
                        version,
                    ),
                )
                await db.commit()
            except sqlite3.Error as exc:
                with contextlib.suppress(sqlite3.Error):
                    await db.execute("ROLLBACK")
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
        db = self._db
        async with self._write_context():
            try:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    "SELECT fact_id, category, tags "
                    "FROM org_facts_snapshot "
                    "WHERE fact_id = ? "
                    "AND retracted_at IS NULL",
                    (fact_id,),
                )
                row = await cursor.fetchone()
                if row is None:
                    await db.execute("ROLLBACK")
                    return False
                version, now = await self._append_to_operation_log(
                    db,
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
                await db.execute(
                    "UPDATE org_facts_snapshot "
                    "SET retracted_at = ?, version = ? "
                    "WHERE fact_id = ?",
                    (format_iso_utc(now), version, fact_id),
                )
                await db.commit()
            except (sqlite3.Error, ValueError, OrgMemoryQueryError) as exc:
                with contextlib.suppress(sqlite3.Error):
                    await db.execute("ROLLBACK")
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
            cursor = await self._db.execute(
                "SELECT * FROM org_facts_snapshot "
                "WHERE fact_id = ? AND retracted_at IS NULL",
                (fact_id,),
            )
            row = await cursor.fetchone()
        except sqlite3.Error as exc:
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
        db = self._db
        limit = max(1, min(limit, 100))
        offset = max(0, int(offset))
        clauses: list[str] = ["retracted_at IS NULL"]
        params: list[str | int] = []

        if categories is not None and categories:
            placeholders = ",".join("?" for _ in categories)
            clauses.append(f"category IN ({placeholders})")
            params.extend(c.value for c in categories)

        if text is not None:
            escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("content LIKE ? ESCAPE '\\'")
            params.append(f"%{escaped}%")

        where = f" WHERE {' AND '.join(clauses)}"
        if text is not None:
            order = (
                "ORDER BY INSTR(LOWER(content), LOWER(?)) ASC, "
                "LENGTH(content) ASC, created_at DESC, fact_id ASC"
            )
            params.append(text)
        else:
            order = "ORDER BY created_at DESC, fact_id ASC"
        sql = (
            f"SELECT * FROM org_facts_snapshot{where} {order} "  # noqa: S608
            "LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])

        try:
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
        except sqlite3.Error as exc:
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
            "WHERE category = ? AND retracted_at IS NULL "
            "ORDER BY created_at DESC, fact_id ASC"
        )
        params: tuple[object, ...] = (category.value,)
        # Clamp ``limit`` to a sane positive range at the boundary so
        # SQLite's ``LIMIT -1`` "unlimited" semantics cannot leak in
        # via a caller passing a negative or oversized value, and so
        # the SQLite path agrees with Postgres on the bounded contract
        # (Postgres rejects negative LIMIT outright; SQLite would
        # silently drop the cap).
        effective_limit = max(1, min(int(limit), 100))
        effective_offset = max(0, int(offset))
        sql += " LIMIT ? OFFSET ?"
        params = (*params, effective_limit, effective_offset)
        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
        except sqlite3.Error as exc:
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

        ``timestamp`` must be timezone-aware; ``format_iso_utc`` will
        raise ``ValueError`` on a naive datetime so a regression that
        bypasses the type guard surfaces immediately rather than
        binding a misinterpreted instant into the WHERE clause. Rows
        page in ``fact_id`` order so a cursor walk is repeatable
        across the same snapshot; callers needing the whole snapshot
        drain via :func:`synthorg.persistence._shared.collect_all`.

        Returns:
            The matching collection.

        Raises:
            OrgMemoryQueryError: If the underlying call raises.
        """
        limit = validate_pagination_args(limit, offset, event=ORG_MEMORY_QUERY_FAILED)
        db = self._db
        query_ts = format_iso_utc(timestamp)
        try:
            cursor = await db.execute(
                SNAPSHOT_AT_SQL,
                (query_ts, query_ts, query_ts, query_ts, query_ts, limit, offset),
            )
            rows = await cursor.fetchall()
        except sqlite3.Error as exc:
            logger.warning(
                ORG_MEMORY_QUERY_FAILED,
                timestamp=query_ts,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = (
                f"Failed to query snapshot at {query_ts}: {safe_error_description(exc)}"
            )
            raise OrgMemoryQueryError(msg) from exc
        else:
            result = tuple(row_to_snapshot(row) for row in rows)
            logger.debug(
                ORG_MEMORY_MVCC_SNAPSHOT_AT_QUERIED,
                timestamp=query_ts,
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
            cursor = await self._db.execute(
                "SELECT * FROM org_facts_operation_log "
                "WHERE fact_id = ? ORDER BY version ASC "
                "LIMIT ? OFFSET ?",
                (fact_id, limit, offset),
            )
            rows = await cursor.fetchall()
        except sqlite3.Error as exc:
            logger.warning(
                ORG_MEMORY_QUERY_FAILED,
                fact_id=fact_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to get operation log for {fact_id}: {safe_error_description(exc)}"  # noqa: E501
            raise OrgMemoryQueryError(msg) from exc
        else:
            result = tuple(row_to_operation_log_entry(row) for row in rows)
            logger.debug(
                ORG_MEMORY_MVCC_LOG_QUERIED,
                fact_id=fact_id,
                count=len(result),
            )
            return result


__all__ = ["SQLiteOrgFactRepository"]
