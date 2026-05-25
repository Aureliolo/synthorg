"""Postgres repository for TrainingResult persistence.

Postgres-native port of the SQLite training result repository.  Uses
JSONB for array/object columns and native TIMESTAMPTZ for timestamps.
"""

from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.hr.training.models import (
    ContentType,
    TrainingApprovalHandle,
    TrainingResult,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.training import (
    HR_TRAINING_PERSISTENCE_ERROR,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared.pagination import validate_pagination_args

logger = get_logger(__name__)

_UPSERT_SQL = """\
INSERT INTO training_results (
    id, plan_id, new_agent_id, source_agents_used,
    items_extracted, items_after_curation,
    items_after_guards, items_stored,
    approval_item_id, pending_approvals,
    review_pending, errors, started_at, completed_at
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT(id) DO UPDATE SET
    plan_id=EXCLUDED.plan_id,
    new_agent_id=EXCLUDED.new_agent_id,
    source_agents_used=EXCLUDED.source_agents_used,
    items_extracted=EXCLUDED.items_extracted,
    items_after_curation=EXCLUDED.items_after_curation,
    items_after_guards=EXCLUDED.items_after_guards,
    items_stored=EXCLUDED.items_stored,
    approval_item_id=EXCLUDED.approval_item_id,
    pending_approvals=EXCLUDED.pending_approvals,
    review_pending=EXCLUDED.review_pending,
    errors=EXCLUDED.errors,
    started_at=EXCLUDED.started_at,
    completed_at=EXCLUDED.completed_at"""


def _deserialize_count_tuples(
    raw: list[list[Any]],
) -> tuple[tuple[ContentType, int], ...]:
    """Convert JSONB list of ``[type, count]`` pairs.

    Returns:
        The matching collection.
    """
    return tuple((ContentType(ct), n) for ct, n in raw)


def _deserialize_approvals(
    raw: list[dict[str, Any]],
) -> tuple[TrainingApprovalHandle, ...]:
    """Convert JSONB list of approval handle dicts.

    Returns:
        The matching collection.
    """
    return tuple(
        TrainingApprovalHandle(
            approval_item_id=NotBlankStr(h["approval_item_id"]),
            content_type=ContentType(h["content_type"]),
            item_count=h["item_count"],
        )
        for h in raw
    )


def _row_to_result(row: dict[str, Any]) -> TrainingResult:
    """Reconstruct a ``TrainingResult`` from a Postgres dict_row.

    Postgres returns JSONB as Python lists/dicts, TIMESTAMPTZ as
    aware datetimes, and BOOLEAN as bool.

    Raises:
        QueryError: If deserialization fails.

    Returns:
        Result of type ``TrainingResult``.
    """
    data = dict(row)
    try:
        data["source_agents_used"] = tuple(
            NotBlankStr(s) for s in data["source_agents_used"]
        )
        data["items_extracted"] = _deserialize_count_tuples(
            data["items_extracted"],
        )
        data["items_after_curation"] = _deserialize_count_tuples(
            data["items_after_curation"],
        )
        data["items_after_guards"] = _deserialize_count_tuples(
            data["items_after_guards"],
        )
        data["items_stored"] = _deserialize_count_tuples(
            data["items_stored"],
        )
        data["pending_approvals"] = _deserialize_approvals(
            data["pending_approvals"],
        )
        data["errors"] = tuple(data["errors"])
        return TrainingResult.model_validate(data)
    except (ValueError, TypeError, KeyError, ValidationError) as exc:
        result_id = data.get("id", "<unknown>")
        msg = f"Failed to deserialize training result {result_id!r}"
        logger.warning(
            HR_TRAINING_PERSISTENCE_ERROR,
            result_id=str(result_id),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


def _result_to_params(result: TrainingResult) -> tuple[object, ...]:
    """Build the parameter tuple for the upsert SQL statement.

    Returns:
        The matching collection.
    """
    return (
        str(result.id),
        str(result.plan_id),
        str(result.new_agent_id),
        Jsonb([str(s) for s in result.source_agents_used]),
        Jsonb([[ct.value, n] for ct, n in result.items_extracted]),
        Jsonb([[ct.value, n] for ct, n in result.items_after_curation]),
        Jsonb([[ct.value, n] for ct, n in result.items_after_guards]),
        Jsonb([[ct.value, n] for ct, n in result.items_stored]),
        str(result.approval_item_id) if result.approval_item_id is not None else None,
        Jsonb(
            [
                {
                    "approval_item_id": str(h.approval_item_id),
                    "content_type": h.content_type.value,
                    "item_count": h.item_count,
                }
                for h in result.pending_approvals
            ]
        ),
        result.review_pending,
        Jsonb(list(result.errors)),
        result.started_at,
        result.completed_at,
    )


class PostgresTrainingResultRepository:
    """Postgres-backed training result repository.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, result: TrainingResult) -> None:
        """Persist a training result via upsert.

        Args:
            result: Training result to persist.

        Raises:
            QueryError: If the database operation fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor() as cur,
            ):
                await cur.execute(
                    _UPSERT_SQL,
                    _result_to_params(result),
                )
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to save training result {result.id!r}"
            logger.warning(
                HR_TRAINING_PERSISTENCE_ERROR,
                result_id=str(result.id),
                plan_id=str(result.plan_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(
        self,
        result_id: NotBlankStr,
    ) -> TrainingResult | None:
        """Retrieve a training result by ID.

        Args:
            result_id: Training result identifier.

        Returns:
            The result, or ``None`` if not found.

        Raises:
            QueryError: If the operation fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT * FROM training_results WHERE id = %s",
                    (str(result_id),),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch training result {result_id!r}"
            logger.warning(
                HR_TRAINING_PERSISTENCE_ERROR,
                result_id=str(result_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return _row_to_result(row)

    async def delete(
        self,
        result_id: NotBlankStr,
    ) -> bool:
        """Delete a training result by ID.

        Args:
            result_id: Training result identifier.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the operation fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor() as cur,
            ):
                await cur.execute(
                    "DELETE FROM training_results WHERE id = %s",
                    (str(result_id),),
                )
                await conn.commit()
                return cur.rowcount > 0
        except psycopg.Error as exc:
            msg = f"Failed to delete training result {result_id!r}"
            logger.warning(
                HR_TRAINING_PERSISTENCE_ERROR,
                result_id=str(result_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[TrainingResult, ...]:
        """List training results with pagination.

        Args:
            limit: Maximum results to return (must be >= 1).
            offset: Rows to skip before the window.

        Returns:
            Tuple of results ordered by id ascending.

        Raises:
            QueryError: If the operation fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=HR_TRAINING_PERSISTENCE_ERROR
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT * FROM training_results ORDER BY id ASC LIMIT %s OFFSET %s",
                    (limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list training results"
            logger.warning(
                HR_TRAINING_PERSISTENCE_ERROR,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(_row_to_result(row) for row in rows)

    async def get_by_plan(
        self,
        plan_id: NotBlankStr,
    ) -> TrainingResult | None:
        """Retrieve the latest result by plan ID.

        Args:
            plan_id: Training plan identifier.

        Returns:
            The most recent matching result, or ``None`` if not found.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    """\
SELECT * FROM training_results
WHERE plan_id = %s
ORDER BY completed_at DESC, id DESC
LIMIT 1""",
                    (str(plan_id),),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch result for plan {plan_id!r}"
            logger.warning(
                HR_TRAINING_PERSISTENCE_ERROR,
                plan_id=str(plan_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return _row_to_result(row)

    async def get_latest(
        self,
        agent_id: NotBlankStr,
    ) -> TrainingResult | None:
        """Retrieve the latest result for an agent.

        Args:
            agent_id: Agent identifier.

        Returns:
            The most recent result, or ``None`` if not found.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    """\
SELECT * FROM training_results
WHERE new_agent_id = %s
ORDER BY completed_at DESC
LIMIT 1""",
                    (str(agent_id),),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch latest result for {agent_id!r}"
            logger.warning(
                HR_TRAINING_PERSISTENCE_ERROR,
                agent_id=str(agent_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return _row_to_result(row)
