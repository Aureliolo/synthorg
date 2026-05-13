"""SQLite repository for TrainingResult persistence.

Provides ``SQLiteTrainingResultRepository`` which persists
``TrainingResult`` models via aiosqlite with upsert semantics.
"""

import contextlib
import json
import sqlite3
from datetime import UTC, datetime

import aiosqlite
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
from synthorg.persistence.sqlite._shared import WriteContext  # noqa: TC001

logger = get_logger(__name__)

_UPSERT_SQL = """\
INSERT INTO training_results (
    id, plan_id, new_agent_id, source_agents_used,
    items_extracted, items_after_curation,
    items_after_guards, items_stored,
    approval_item_id, pending_approvals,
    review_pending, errors, started_at, completed_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    plan_id=excluded.plan_id,
    new_agent_id=excluded.new_agent_id,
    source_agents_used=excluded.source_agents_used,
    items_extracted=excluded.items_extracted,
    items_after_curation=excluded.items_after_curation,
    items_after_guards=excluded.items_after_guards,
    items_stored=excluded.items_stored,
    approval_item_id=excluded.approval_item_id,
    pending_approvals=excluded.pending_approvals,
    review_pending=excluded.review_pending,
    errors=excluded.errors,
    started_at=excluded.started_at,
    completed_at=excluded.completed_at"""


def _serialize_count_tuples(
    counts: tuple[tuple[ContentType, int], ...],
) -> str:
    """Serialize per-content-type count tuples to JSON."""
    return json.dumps([[ct.value, n] for ct, n in counts])


def _serialize_approvals(
    approvals: tuple[TrainingApprovalHandle, ...],
) -> str:
    """Serialize pending approval handles to JSON."""
    return json.dumps(
        [
            {
                "approval_item_id": str(h.approval_item_id),
                "content_type": h.content_type.value,
                "item_count": h.item_count,
            }
            for h in approvals
        ]
    )


def _serialize_sources(
    sources: tuple[NotBlankStr, ...],
) -> str:
    """Serialize source agent IDs to JSON."""
    return json.dumps([str(s) for s in sources])


def _serialize_errors(errors: tuple[str, ...]) -> str:
    """Serialize error strings to JSON."""
    return json.dumps(list(errors))


def _result_to_params(result: TrainingResult) -> tuple[object, ...]:
    """Build the parameter tuple for the upsert SQL statement."""
    return (
        str(result.id),
        str(result.plan_id),
        str(result.new_agent_id),
        _serialize_sources(result.source_agents_used),
        _serialize_count_tuples(result.items_extracted),
        _serialize_count_tuples(result.items_after_curation),
        _serialize_count_tuples(result.items_after_guards),
        _serialize_count_tuples(result.items_stored),
        str(result.approval_item_id) if result.approval_item_id is not None else None,
        _serialize_approvals(result.pending_approvals),
        int(result.review_pending),
        _serialize_errors(result.errors),
        result.started_at.astimezone(UTC).isoformat(),
        result.completed_at.astimezone(UTC).isoformat(),
    )


def _deserialize_count_tuples(
    raw: str,
) -> tuple[tuple[ContentType, int], ...]:
    """Deserialize per-content-type count tuples from JSON."""
    return tuple((ContentType(ct), n) for ct, n in json.loads(raw))


def _deserialize_approvals(
    raw: str,
) -> tuple[TrainingApprovalHandle, ...]:
    """Deserialize pending approval handles from JSON."""
    return tuple(
        TrainingApprovalHandle(
            approval_item_id=NotBlankStr(h["approval_item_id"]),
            content_type=ContentType(h["content_type"]),
            item_count=h["item_count"],
        )
        for h in json.loads(raw)
    )


def _row_to_result(row: aiosqlite.Row) -> TrainingResult:
    """Reconstruct a ``TrainingResult`` from a database row.

    Args:
        row: A single database row.

    Returns:
        Validated ``TrainingResult`` model instance.

    Raises:
        QueryError: If deserialization fails.
    """
    data = dict(row)
    try:
        data["source_agents_used"] = tuple(
            NotBlankStr(s) for s in json.loads(data["source_agents_used"])
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
        data["review_pending"] = bool(data["review_pending"])
        data["errors"] = tuple(json.loads(data["errors"]))
        data["started_at"] = datetime.fromisoformat(data["started_at"])
        data["completed_at"] = datetime.fromisoformat(
            data["completed_at"],
        )
        return TrainingResult.model_validate(data)
    except (
        json.JSONDecodeError,
        ValueError,
        TypeError,
        KeyError,
        ValidationError,
    ) as exc:
        result_id = data.get("id", "<unknown>")
        msg = f"Failed to deserialize training result {result_id!r}"
        logger.warning(
            HR_TRAINING_PERSISTENCE_ERROR,
            result_id=str(result_id),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


class SQLiteTrainingResultRepository:
    """SQLite-backed training result repository.

    Provides upsert-based persistence for ``TrainingResult`` models
    using a shared ``aiosqlite.Connection``.

    Args:
        db: An open aiosqlite connection with ``row_factory``
            set to ``aiosqlite.Row``.
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

    async def save(self, result: TrainingResult) -> None:
        """Persist a training result via upsert.

        Args:
            result: Training result to persist.

        Raises:
            QueryError: If the database operation fails.
        """
        async with self._write_context():
            try:
                await self._db.execute(
                    _UPSERT_SQL,
                    _result_to_params(result),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to save training result {result.id!r}"
                logger.warning(
                    HR_TRAINING_PERSISTENCE_ERROR,
                    result_id=str(result.id),
                    plan_id=str(result.plan_id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get_by_plan(
        self,
        plan_id: NotBlankStr,
    ) -> TrainingResult | None:
        """Retrieve the latest result by plan ID.

        Args:
            plan_id: Training plan identifier.

        Returns:
            The most recent matching result, or ``None`` if not found.
        """
        try:
            cursor = await self._db.execute(
                """\
SELECT * FROM training_results
WHERE plan_id = ?
ORDER BY completed_at DESC, id DESC
LIMIT 1""",
                (str(plan_id),),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
            agent_id: Target agent identifier.

        Returns:
            The most recent result, or ``None``.
        """
        try:
            cursor = await self._db.execute(
                """\
SELECT * FROM training_results
WHERE new_agent_id = ?
ORDER BY completed_at DESC
LIMIT 1""",
                (str(agent_id),),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
