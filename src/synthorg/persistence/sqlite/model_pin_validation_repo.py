# module-kind: repository
"""SQLite repository for prompt-class pin-validation records.

Satisfies ``ModelPinValidationRepository`` structurally: id-keyed CRUD
(``save`` upsert / ``get`` / ``delete`` / ``list_items``) keyed by
``prompt_class_id``. A validation record is the latest result per prompt
class, re-recorded by each clean eval-refresh pass.
"""

import sqlite3
from typing import Final, cast

import aiosqlite
from aiosqlite import Row

from synthorg.budget.model_tier import TierName
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.llm.model_pin_validation import ModelPinValidationRow
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.model_pins import (
    MODEL_PIN_VALIDATION_FAILED,
    MODEL_PIN_VALIDATION_FETCHED,
    MODEL_PIN_VALIDATION_LISTED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    format_iso_utc,
    validate_pagination_args,
)
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: Final[int] = 1_000

_SELECT_COLS: Final[str] = "prompt_class_id, validated_at, tier, passed"

_UPSERT_SQL = f"""
    INSERT INTO model_pin_validations ({_SELECT_COLS})
    VALUES (?, ?, ?, ?)
    ON CONFLICT(prompt_class_id) DO UPDATE SET
        validated_at = excluded.validated_at,
        tier = excluded.tier,
        passed = excluded.passed
"""  # noqa: S608 -- column list is a compile-time constant


async def _safe_rollback(
    db: aiosqlite.Connection,
    *,
    operation: str,
    **log_context: object,
) -> None:
    """Roll back the current transaction, logging any rollback failure."""
    try:
        await db.rollback()
    except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
        log_exception_redacted(
            logger,
            MODEL_PIN_VALIDATION_FAILED,
            rollback_exc,
            phase="rollback",
            operation=operation,
            **log_context,
        )


def _row_to_record(row: Row) -> ModelPinValidationRow:
    """Convert a database row into a :class:`ModelPinValidationRow`.

    Returns:
        The parsed :class:`ModelPinValidationRow`.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        return ModelPinValidationRow(
            prompt_class_id=PromptPurposeId(str(row["prompt_class_id"])),
            validated_at=coerce_row_timestamp(row["validated_at"]),
            tier=cast("TierName", str(row["tier"])),
            passed=bool(row["passed"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        error_type = type(exc).__name__
        error_desc = safe_error_description(exc)
        msg = f"Failed to parse model pin validation row: {error_type} ({error_desc})"
        logger.warning(
            MODEL_PIN_VALIDATION_FAILED,
            operation="deserialize",
            error_type=error_type,
            error=error_desc,
        )
        raise QueryError(msg) from exc


class SQLiteModelPinValidationRepository:
    """SQLite-backed prompt-class pin-validation repository.

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

    async def save(self, entity: ModelPinValidationRow) -> None:
        """Upsert a validation row keyed by ``prompt_class_id``.

        Raises:
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        class_id = str(entity.prompt_class_id)
        params = (
            class_id,
            format_iso_utc(entity.validated_at),
            str(entity.tier),
            int(entity.passed),
        )
        async with self._write_context():
            try:
                await self._db.execute(_UPSERT_SQL, params)
                await self._db.commit()
            except sqlite3.IntegrityError as exc:
                await _safe_rollback(
                    self._db, operation="save", prompt_class_id=class_id
                )
                msg = (
                    f"Constraint violation saving pin validation "
                    f"{class_id!r}: {safe_error_description(exc)}"
                )
                logger.warning(
                    MODEL_PIN_VALIDATION_FAILED,
                    operation="save",
                    prompt_class_id=class_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise ConstraintViolationError(msg, constraint=str(exc)) from exc
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(
                    self._db, operation="save", prompt_class_id=class_id
                )
                msg = (
                    f"Failed to save pin validation {class_id!r}: "
                    f"{type(exc).__name__} ({safe_error_description(exc)})"
                )
                logger.warning(
                    MODEL_PIN_VALIDATION_FAILED,
                    operation="save",
                    prompt_class_id=class_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, entity_id: NotBlankStr) -> ModelPinValidationRow | None:
        """Get a validation row by ``prompt_class_id``, or ``None`` if absent.

        Returns:
            The matching record, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        sql = (
            f"SELECT {_SELECT_COLS} FROM model_pin_validations "  # noqa: S608
            "WHERE prompt_class_id = ?"
        )
        try:
            async with self._db.execute(sql, (entity_id,)) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = (
                f"Failed to fetch pin validation {entity_id!r}: "
                f"{type(exc).__name__} ({safe_error_description(exc)})"
            )
            logger.warning(
                MODEL_PIN_VALIDATION_FAILED,
                operation="get",
                prompt_class_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        record = _row_to_record(row)
        logger.debug(MODEL_PIN_VALIDATION_FETCHED, prompt_class_id=entity_id)
        return record

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ModelPinValidationRow, ...]:
        """List rows ordered by ``prompt_class_id`` ascending (paginated).

        Returns:
            The matching records.

        Raises:
            QueryError: If the database query fails.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=MODEL_PIN_VALIDATION_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        sql = (
            f"SELECT {_SELECT_COLS} FROM model_pin_validations "  # noqa: S608
            "ORDER BY prompt_class_id ASC LIMIT ? OFFSET ?"
        )
        try:
            async with self._db.execute(sql, (effective_limit, offset)) as cursor:
                rows = await cursor.fetchall()
            items = tuple(_row_to_record(r) for r in rows)
        except QueryError:
            raise
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list pin validations"
            logger.warning(
                MODEL_PIN_VALIDATION_FAILED,
                operation="list_items",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(MODEL_PIN_VALIDATION_LISTED, count=len(items))
        return items

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a validation row by ``prompt_class_id``.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "DELETE FROM model_pin_validations WHERE prompt_class_id = ?"
        async with self._write_context():
            try:
                async with self._db.execute(sql, (entity_id,)) as cursor:
                    rowcount = cursor.rowcount
                    await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(
                    self._db, operation="delete", prompt_class_id=entity_id
                )
                msg = (
                    f"Failed to delete pin validation {entity_id!r}: "
                    f"{type(exc).__name__} ({safe_error_description(exc)})"
                )
                logger.warning(
                    MODEL_PIN_VALIDATION_FAILED,
                    operation="delete",
                    prompt_class_id=entity_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return rowcount > 0


__all__ = ["SQLiteModelPinValidationRepository"]
