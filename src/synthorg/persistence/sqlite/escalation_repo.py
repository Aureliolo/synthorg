"""SQLite repository for the human escalation queue.

Mirrors the shape of :class:`SQLiteApprovalRepository`: a shared
``aiosqlite.Connection``, row-mapping helper, and async CRUD with
structured logging.  Stores the :class:`Conflict` snapshot and the
optional decision payload as JSON TEXT columns for schema simplicity.
"""

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import override
from uuid import UUID

import aiosqlite
from aiosqlite import Row
from pydantic import TypeAdapter

from synthorg.communication.conflict_resolution.escalation.models import (
    Escalation,
    EscalationDecision,
    EscalationStatus,
)
from synthorg.communication.conflict_resolution.escalation.protocol import (
    _DEFAULT_LIMIT,
    _DEFAULT_OFFSET,
    EscalationQueueStore,
)
from synthorg.communication.conflict_resolution.models import Conflict
from synthorg.core.persistence_errors import (
    ConstraintViolationError,
    MalformedRowError,
    QueryError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.conflict import (
    CONFLICT_ESCALATION_STATUS_TRANSITIONED,
)
from synthorg.observability.events.persistence.escalation import (
    PERSISTENCE_ESCALATION_CREATE_FAILED,
    PERSISTENCE_ESCALATION_DESERIALIZE_FAILED,
    PERSISTENCE_ESCALATION_GET_FAILED,
    PERSISTENCE_ESCALATION_LIST_FAILED,
    PERSISTENCE_ESCALATION_MARK_EXPIRED_FAILED,
    PERSISTENCE_ESCALATION_UPDATE_FAILED,
)
from synthorg.persistence._shared import format_iso_utc, parse_iso_utc
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_decision_adapter: TypeAdapter[EscalationDecision] = TypeAdapter(EscalationDecision)

_UPSERT_SQL = """
    INSERT INTO conflict_escalations (
        id, conflict_id, conflict_json, status,
        created_at, expires_at, decided_at, decided_by, decision_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_COLS = (
    "id, conflict_id, conflict_json, status, "
    "created_at, expires_at, decided_at, decided_by, decision_json"
)


def _row_to_escalation(row: Row) -> Escalation:
    """Deserialize a DB row into an :class:`Escalation`.

    Returns:
        Result of type ``Escalation``.

    Raises:
        MalformedRowError: If row parsing or validation fails. The failure
            is deterministic (a corrupt row reparses identically), so it is
            non-retryable.
    """
    try:
        conflict = Conflict.model_validate_json(str(row["conflict_json"]))
        decision: EscalationDecision | None = None
        decision_raw = row["decision_json"]
        if decision_raw is not None:
            decision = _decision_adapter.validate_json(str(decision_raw))
        return Escalation(
            id=UUID(str(row["id"])),
            conflict=conflict,
            status=EscalationStatus(str(row["status"])),
            created_at=parse_iso_utc(str(row["created_at"])),
            expires_at=(
                parse_iso_utc(str(row["expires_at"]))
                if row["expires_at"] is not None
                else None
            ),
            decided_at=(
                parse_iso_utc(str(row["decided_at"]))
                if row["decided_at"] is not None
                else None
            ),
            decided_by=(
                str(row["decided_by"]) if row["decided_by"] is not None else None
            ),
            decision=decision,
        )
    except (json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
        try:
            row_id = str(row["id"]) if row else "<unknown>"
        except TypeError, KeyError:
            row_id = "<unknown>"
        logger.warning(
            PERSISTENCE_ESCALATION_DESERIALIZE_FAILED,
            row_id=row_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to parse escalation row {row_id!r}"
        raise MalformedRowError(msg) from exc


class SQLiteEscalationRepository(EscalationQueueStore):
    """aiosqlite-backed :class:`EscalationQueueStore`.

    Args:
        db: An open aiosqlite connection (typically the one shared by
            the :class:`SQLiteBackend` with all other SQLite repos).
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
        self._db: aiosqlite.Connection = db
        self._db.row_factory = aiosqlite.Row
        self._write_context = write_context

    @override
    async def create(self, escalation: Escalation) -> None:
        """Insert a PENDING escalation row.

        Raises:
            ValueError: If an argument fails validation.
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        if escalation.status != EscalationStatus.PENDING:
            msg = "create() requires status=PENDING"
            raise ValueError(msg)
        params = (
            str(escalation.id),
            str(escalation.conflict.id),
            escalation.conflict.model_dump_json(),
            escalation.status.value,
            format_iso_utc(escalation.created_at),
            (format_iso_utc(escalation.expires_at) if escalation.expires_at else None),
            None,
            None,
            None,
        )
        async with self._write_context():
            try:
                await self._db.execute(_UPSERT_SQL, params)
                await self._db.commit()
            except sqlite3.IntegrityError as exc:
                msg = f"Escalation {escalation.id!r} already exists"
                logger.warning(
                    PERSISTENCE_ESCALATION_CREATE_FAILED,
                    error_type="escalation_create_duplicate",
                    escalation_id=str(escalation.id),
                    conflict_id=str(escalation.conflict.id),
                    error=safe_error_description(exc),
                )
                await self._db.rollback()
                raise ConstraintViolationError(msg, constraint=str(exc)) from exc
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = f"Failed to create escalation {escalation.id!r}: {safe_error_description(exc)}"  # noqa: E501
                logger.warning(
                    PERSISTENCE_ESCALATION_CREATE_FAILED,
                    error_type="escalation_create_failed",
                    escalation_id=str(escalation.id),
                    conflict_id=str(escalation.conflict.id),
                    error=safe_error_description(exc),
                )
                await self._db.rollback()
                raise QueryError(msg) from exc

    @override
    async def get(self, escalation_id: str) -> Escalation | None:
        """Fetch by ID.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        sql = f"SELECT {_SELECT_COLS} FROM conflict_escalations WHERE id = ?"  # noqa: S608
        try:
            cursor = await self._db.execute(sql, (escalation_id,))
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch escalation {escalation_id!r}: {safe_error_description(exc)}"  # noqa: E501
            logger.warning(
                PERSISTENCE_ESCALATION_GET_FAILED,
                error_type="escalation_get_failed",
                escalation_id=escalation_id,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return _row_to_escalation(row)

    @override
    async def list_items(
        self,
        *,
        status: EscalationStatus | None = EscalationStatus.PENDING,
        limit: int = _DEFAULT_LIMIT,
        offset: int = _DEFAULT_OFFSET,
    ) -> tuple[tuple[Escalation, ...], int]:
        """Page over rows filtered by status.

        Returns:
            Tuple of (items, total_count).

        Raises:
            ValueError: If an argument fails validation.
            QueryError: If the database query fails.
        """
        if limit <= 0:
            msg = "limit must be positive"
            raise ValueError(msg)
        if offset < 0:
            msg = "offset must be non-negative"
            raise ValueError(msg)
        where = "1=1"
        params: list[object] = []
        if status is not None:
            where = "status = ?"
            params.append(status.value)
        count_sql = f"SELECT COUNT(*) AS total FROM conflict_escalations WHERE {where}"  # noqa: S608
        page_sql = (
            f"SELECT {_SELECT_COLS} FROM conflict_escalations "  # noqa: S608
            f"WHERE {where} ORDER BY created_at ASC LIMIT ? OFFSET ?"
        )
        try:
            count_cursor = await self._db.execute(count_sql, params)
            count_row = await count_cursor.fetchone()
            total = int(count_row["total"]) if count_row is not None else 0
            page_cursor = await self._db.execute(page_sql, (*params, limit, offset))
            rows = await page_cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to list escalations: {safe_error_description(exc)}"
            logger.warning(
                PERSISTENCE_ESCALATION_LIST_FAILED,
                error_type="escalation_list_failed",
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        # A single corrupt row must not poison the entire page -- log and
        # skip it instead so the operator dashboard keeps functioning.
        page_items: list[Escalation] = []
        for row in rows:
            try:
                page_items.append(_row_to_escalation(row))
            except QueryError as exc:
                logger.warning(
                    PERSISTENCE_ESCALATION_DESERIALIZE_FAILED,
                    error_type="escalation_row_corrupt_skipped",
                    error=safe_error_description(exc),
                )
        return tuple(page_items), total

    @override
    async def apply_decision(
        self,
        escalation_id: str,
        *,
        decision: EscalationDecision,
        decided_by: str,
    ) -> Escalation:
        """Transition PENDING -> DECIDED atomically.

        Returns:
            Result of type ``Escalation``.
        """
        now_iso = format_iso_utc(datetime.now(UTC))
        decision_json = _decision_adapter.dump_json(decision).decode("utf-8")
        return await self._update_terminal(
            escalation_id,
            new_status=EscalationStatus.DECIDED,
            decided_at_iso=now_iso,
            decided_by=decided_by,
            decision_json=decision_json,
            allowed_from={EscalationStatus.PENDING},
        )

    @override
    async def cancel(self, escalation_id: str, *, cancelled_by: str) -> Escalation:
        """Transition PENDING -> CANCELLED.

        Returns:
            Result of type ``Escalation``.
        """
        now_iso = format_iso_utc(datetime.now(UTC))
        return await self._update_terminal(
            escalation_id,
            new_status=EscalationStatus.CANCELLED,
            decided_at_iso=now_iso,
            decided_by=cancelled_by,
            decision_json=None,
            allowed_from={EscalationStatus.PENDING},
        )

    @override
    async def mark_expired(self, now_iso: str) -> tuple[str, ...]:
        """Expire PENDING rows past their deadline.

        Returns the IDs of rows that were actually UPDATEd -- using
        ``UPDATE ... RETURNING`` (SQLite 3.35+) so a row that raced
        with a concurrent decide/cancel and is no longer PENDING is
        not falsely reported as expired.  ``decided_by`` is set to
        the ``"system:expiry"`` sentinel so audit consumers can
        distinguish sweeper-driven expiry from operator-driven
        cancellation (``"system:resolver_cancelled"``) or human
        decisions (``"human:<operator_id>"``).

        Returns:
            Tuple of escalation IDs that were marked as expired.

        Raises:
            QueryError: If the database query fails.
        """
        update_sql = (
            "UPDATE conflict_escalations "
            "SET status='expired', decided_at=?, decided_by='system:expiry' "
            "WHERE status='pending' AND expires_at IS NOT NULL "
            "AND expires_at <= ? "
            "RETURNING id"
        )
        async with self._write_context():
            try:
                cursor = await self._db.execute(update_sql, (now_iso, now_iso))
                rows = await cursor.fetchall()
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = "Failed to mark escalations expired"
                logger.warning(
                    PERSISTENCE_ESCALATION_MARK_EXPIRED_FAILED,
                    error_type="escalation_mark_expired_failed",
                    error=safe_error_description(exc),
                )
                await self._db.rollback()
                raise QueryError(msg) from exc
        expired_ids = tuple(str(r["id"]) for r in rows)
        for escalation_id in expired_ids:
            logger.info(
                CONFLICT_ESCALATION_STATUS_TRANSITIONED,
                escalation_id=escalation_id,
                from_status=EscalationStatus.PENDING.value,
                to_status=EscalationStatus.EXPIRED.value,
            )
        return expired_ids

    @override
    async def close(self) -> None:
        """No-op: the connection is owned by the persistence backend."""
        return

    @override
    @asynccontextmanager
    async def subscribe_notifications(
        self,
        channel: str,
    ) -> AsyncIterator[AsyncIterator[str]]:
        # The `@asynccontextmanager` decorator turns this generator into a
        # callable returning an `AbstractAsyncContextManager[AsyncIterator[str]]`
        # which matches the protocol declared on `EscalationQueueStore`;
        # the generator itself must be annotated with the inner iterator
        # type because that is what the `yield` statement produces.
        """Return an iterator that blocks until cancelled (no-op).

        SQLite is single-process by design; there is no cross-instance
        signalling channel for this backend. The subscriber contract
        still needs an async context manager + iterator, so we yield
        an iterator that awaits an :class:`asyncio.Event` that is never
        set and exits cleanly on cancellation. Callers get a valid
        iterator they can iterate over with ``async for`` -- the body
        just never runs until the task is cancelled.
        """
        stop = asyncio.Event()

        async def _never() -> AsyncIterator[str]:
            # The type system needs at least one ``yield`` to see this
            # as an async generator; put one behind an always-false
            # gate so it is never actually emitted.  The outer ``await``
            # blocks until ``stop`` is set on context-manager exit.
            # lint-allow: long-running-loop-kill-switch -- sentinel coroutine.
            while not stop.is_set():
                await stop.wait()
                if stop.is_set():
                    return
                yield ""  # pragma: no cover - unreachable in normal flow

        try:
            yield _never()
        finally:
            stop.set()

    async def _update_terminal(  # noqa: PLR0913
        self,
        escalation_id: str,
        *,
        new_status: EscalationStatus,
        decided_at_iso: str,
        decided_by: str,
        decision_json: str | None,
        allowed_from: set[EscalationStatus],
    ) -> Escalation:
        """Apply a terminal state transition under a conditional WHERE.

        ``allowed_from`` is an internal EscalationStatus enum set -- the
        ``IN (...)`` clause interpolates values from a trusted enum, not
        caller input, so the S608 is a false positive.

        Returns:
            Result of type ``Escalation``.

        Raises:
            ValueError: If an argument fails validation.
            QueryError: If the database query fails.
            KeyError: If a required dictionary key is missing.
        """
        allowed = ",".join(f"'{s.value}'" for s in allowed_from)
        update_sql = (
            "UPDATE conflict_escalations SET "  # noqa: S608
            "status = ?, decided_at = ?, decided_by = ?, decision_json = ? "
            f"WHERE id = ? AND status IN ({allowed})"
        )
        params = (
            new_status.value,
            decided_at_iso,
            decided_by,
            decision_json,
            escalation_id,
        )
        async with self._write_context():
            try:
                cursor = await self._db.execute(update_sql, params)
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = f"Failed to update escalation {escalation_id!r}: {safe_error_description(exc)}"  # noqa: E501
                logger.warning(
                    PERSISTENCE_ESCALATION_UPDATE_FAILED,
                    error_type="escalation_update_failed",
                    escalation_id=escalation_id,
                    target_status=new_status.value,
                    error=safe_error_description(exc),
                )
                await self._db.rollback()
                raise QueryError(msg) from exc
        if cursor.rowcount == 0:
            # Recovery lookup runs on a fresh cursor so a crashed row
            # doesn't poison the failure signal back to the caller.
            try:
                existing = await self.get(escalation_id)
            except QueryError as exc:
                msg = (
                    f"Escalation {escalation_id!r} update failed and "
                    "recovery lookup raised"
                )
                raise QueryError(msg) from exc
            if existing is None:
                msg = f"Escalation {escalation_id!r} not found"
                raise KeyError(msg)
            msg = (
                f"Escalation {escalation_id!r} is {existing.status.value}, "
                f"cannot transition to {new_status.value}"
            )
            raise ValueError(msg)
        updated = await self.get(escalation_id)
        if updated is None:
            msg = f"Escalation {escalation_id!r} vanished after update"
            raise QueryError(msg)
        logger.info(
            CONFLICT_ESCALATION_STATUS_TRANSITIONED,
            escalation_id=escalation_id,
            from_status=EscalationStatus.PENDING.value,
            to_status=new_status.value,
        )
        return updated
