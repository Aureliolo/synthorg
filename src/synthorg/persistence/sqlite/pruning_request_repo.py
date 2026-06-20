# module-kind: repository
"""SQLite repository for pending HR-pruning requests.

Durable backing for the pruning service's in-memory
``_pending_requests`` map: id-keyed CRUD keyed by ``agent_id`` (one
pending request per agent). ``save`` upserts on the ``agent_id`` primary
key; the rich :class:`PruningEvaluation` is stored as a JSON blob so a
restart recovers the full evaluation (scores, reasons, snapshot) rather
than the lossy approval-metadata summary.
"""

import json
from typing import NoReturn

import aiosqlite

from synthorg.approval.enums import ApprovalStatus
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.hr.pruning.models import PruningEvaluation, PruningRequest
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.hr import HR_PRUNING_PERSISTENCE_FAILED
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    format_iso_utc,
    validate_pagination_args,
)
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_SELECT_COLS = (
    "agent_id, id, agent_name, evaluation, approval_id, status, "
    "created_at, decided_at, decided_by"
)


def _row_to_request(row: aiosqlite.Row) -> PruningRequest:
    """Convert a database row into a :class:`PruningRequest`.

    Returns:
        The reconstructed request.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        evaluation = PruningEvaluation.model_validate(
            json.loads(str(row["evaluation"]))
        )
        decided_by = row["decided_by"]
        decided_at = row["decided_at"]
        return PruningRequest(
            id=row["id"],
            agent_id=NotBlankStr(str(row["agent_id"])),
            agent_name=NotBlankStr(str(row["agent_name"])),
            evaluation=evaluation,
            approval_id=NotBlankStr(str(row["approval_id"])),
            status=ApprovalStatus(str(row["status"])),
            created_at=coerce_row_timestamp(row["created_at"]),
            decided_at=coerce_row_timestamp(decided_at)
            if decided_at is not None
            else None,
            decided_by=NotBlankStr(str(decided_by)) if decided_by is not None else None,
        )
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        logger.warning(
            HR_PRUNING_PERSISTENCE_FAILED,
            operation="deserialize",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to parse pruning-request row: {type(exc).__name__}"
        raise QueryError(msg) from exc


class SQLitePruningRequestRepository:
    """SQLite-backed pending-pruning-request store.

    Args:
        db: An open aiosqlite connection.
        write_context: Async write-serialising context manager.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._db.row_factory = aiosqlite.Row
        self._write_context = write_context

    async def save(self, entity: PruningRequest) -> None:
        """Upsert a pending pruning request keyed by ``agent_id``.

        Raises:
            QueryError: On database errors.
        """
        sql = """
            INSERT INTO pruning_requests (
                agent_id, id, agent_name, evaluation, approval_id, status,
                created_at, decided_at, decided_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                id = excluded.id,
                agent_name = excluded.agent_name,
                evaluation = excluded.evaluation,
                approval_id = excluded.approval_id,
                status = excluded.status,
                decided_at = excluded.decided_at,
                decided_by = excluded.decided_by
        """
        params = (
            entity.agent_id,
            str(entity.id),
            entity.agent_name,
            json.dumps(
                entity.evaluation.model_dump(mode="json"), separators=(",", ":")
            ),
            entity.approval_id,
            entity.status.value,
            format_iso_utc(entity.created_at),
            format_iso_utc(entity.decided_at)
            if entity.decided_at is not None
            else None,
            entity.decided_by,
        )
        async with self._write_context():
            try:
                await self._db.execute(sql, params)
                await self._db.commit()
            except (aiosqlite.Error, ValueError) as exc:
                await self._rollback("save")
                self._raise_query_error("save pruning request", exc)

    async def get(self, entity_id: NotBlankStr) -> PruningRequest | None:
        """Get the pending request for ``agent_id``, or ``None``.

        Returns:
            The matching request, or ``None``.

        Raises:
            QueryError: If the database query fails.
        """
        sql = (
            f"SELECT {_SELECT_COLS} FROM pruning_requests "  # noqa: S608
            "WHERE agent_id = ?"
        )
        try:
            async with self._db.execute(sql, (entity_id,)) as cursor:
                row = await cursor.fetchone()
        except (aiosqlite.Error, ValueError) as exc:
            self._raise_query_error("get pruning request", exc)
        return None if row is None else _row_to_request(row)

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[PruningRequest, ...]:
        """List pending requests oldest-first by ``created_at`` (paginated).

        Returns:
            The matching requests, oldest-first.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=HR_PRUNING_PERSISTENCE_FAILED
        )
        sql = (
            f"SELECT {_SELECT_COLS} FROM pruning_requests "  # noqa: S608
            "ORDER BY created_at ASC, agent_id ASC LIMIT ? OFFSET ?"
        )
        try:
            async with self._db.execute(sql, (effective_limit, offset)) as cursor:
                rows = await cursor.fetchall()
            return tuple(_row_to_request(r) for r in rows)
        except QueryError:
            raise
        except (aiosqlite.Error, ValueError) as exc:
            self._raise_query_error("list pruning requests", exc)

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete the pending request for ``agent_id``. ``True`` iff present.

        Returns:
            ``True`` when a row was removed, ``False`` otherwise.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "DELETE FROM pruning_requests WHERE agent_id = ?"
        async with self._write_context():
            try:
                async with self._db.execute(sql, (entity_id,)) as cursor:
                    await self._db.commit()
                    return cursor.rowcount > 0
            except (aiosqlite.Error, ValueError) as exc:
                await self._rollback("delete")
                self._raise_query_error("delete pruning request", exc)

    async def _rollback(self, operation: str) -> None:
        try:
            await self._db.rollback()
        except aiosqlite.Error as exc:
            logger.warning(
                HR_PRUNING_PERSISTENCE_FAILED,
                operation=operation,
                phase="rollback",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    def _raise_query_error(self, operation: str, exc: Exception) -> NoReturn:
        logger.warning(
            HR_PRUNING_PERSISTENCE_FAILED,
            operation=operation,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to {operation}: {type(exc).__name__}"
        raise QueryError(msg) from exc


__all__ = ["SQLitePruningRequestRepository"]
