# module-kind: repository
"""Postgres repository for prompt-class pin-validation records.

Sibling of :class:`SQLiteModelPinValidationRepository` backed by
``psycopg_pool.AsyncConnectionPool``. Satisfies
``ModelPinValidationRepository`` structurally: id-keyed CRUD keyed by
``prompt_class_id``.
"""

from typing import Final, cast

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import CapabilityLevel, NotBlankStr
from synthorg.llm.model_pin_validation import ModelPinValidationRow
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.observability import get_logger, safe_error_description
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

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: Final[int] = 1_000

_SELECT_COLS: Final[str] = "prompt_class_id, validated_at, tier"

_UPSERT_SQL = f"""
    INSERT INTO model_pin_validations ({_SELECT_COLS})
    VALUES (%s, %s, %s)
    ON CONFLICT (prompt_class_id) DO UPDATE SET
        validated_at = EXCLUDED.validated_at,
        tier = EXCLUDED.tier
"""  # noqa: S608 -- column list is a compile-time constant


def _row_to_record(row: DictRow) -> ModelPinValidationRow:
    """Convert a Postgres dict row into a :class:`ModelPinValidationRow`.

    Returns:
        The parsed :class:`ModelPinValidationRow`.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        return ModelPinValidationRow(
            prompt_class_id=PromptPurposeId(str(row["prompt_class_id"])),
            validated_at=coerce_row_timestamp(row["validated_at"]),
            tier=cast("CapabilityLevel", str(row["tier"])),
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


class PostgresModelPinValidationRepository:
    """Postgres-backed prompt-class pin-validation repository.

    Args:
        pool: Async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

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
        )
        try:
            async with self._pool.connection() as conn:
                await conn.execute(_UPSERT_SQL, params)
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
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
        except psycopg.Error as exc:
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
            "WHERE prompt_class_id = %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (entity_id,))
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
            "ORDER BY prompt_class_id ASC LIMIT %s OFFSET %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (effective_limit, offset))
                rows = await cur.fetchall()
            items = tuple(_row_to_record(r) for r in rows)
        except QueryError:
            raise
        except psycopg.Error as exc:
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
        sql = "DELETE FROM model_pin_validations WHERE prompt_class_id = %s"
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, (entity_id,))
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
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


__all__ = ["PostgresModelPinValidationRepository"]
