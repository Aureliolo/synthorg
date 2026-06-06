"""SQLite repository implementation for risk tier overrides."""

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import aiosqlite
from pydantic import ValidationError

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import ApprovalRiskLevel
from synthorg.core.persistence_errors import DuplicateRecordError, PersistenceError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.risk_override import (
    PERSISTENCE_RISK_OVERRIDE_QUERY_FAILED,
    PERSISTENCE_RISK_OVERRIDE_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import coerce_row_timestamp, format_iso_utc
from synthorg.persistence._shared.pagination import (
    DEFAULT_LIST_LIMIT,
    validate_pagination_args,
)
from synthorg.persistence.sqlite._shared import (
    WriteContext,
    is_unique_constraint_error,
)
from synthorg.security.rules.risk_override import RiskTierOverride

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

_COLS = (
    "id, action_type, original_tier, override_tier, reason, "
    "created_by, created_at, expires_at, revoked_at, revoked_by"
)


class SQLiteRiskOverrideRepository:
    """SQLite implementation of the RiskOverrideRepository protocol.

    Args:
        db: An open aiosqlite connection.
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

    async def _rollback_quietly(self) -> None:
        """Roll back the current transaction, swallowing errors."""
        try:
            await self._db.rollback()
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                PERSISTENCE_RISK_OVERRIDE_SAVE_FAILED,
                error="rollback failed",
            )

    async def save(self, override: RiskTierOverride) -> None:
        """Persist a new risk tier override.

        Args:
            override: The override to save.

        Raises:
            DuplicateRecordError: If an override with the same ID exists.
            PersistenceError: If the save fails.
        """
        created_at_utc = format_iso_utc(override.created_at)
        expires_at_utc = format_iso_utc(override.expires_at)
        revoked_at_utc = (
            format_iso_utc(override.revoked_at) if override.revoked_at else None
        )

        async with self._write_context():
            try:
                await self._db.execute(
                    f"INSERT INTO risk_overrides ({_COLS}) "  # noqa: S608
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        override.id,
                        override.action_type,
                        override.original_tier.value,
                        override.override_tier.value,
                        override.reason,
                        override.created_by,
                        created_at_utc,
                        expires_at_utc,
                        revoked_at_utc,
                        override.revoked_by,
                    ),
                )
                await self._db.commit()
            except sqlite3.IntegrityError as exc:
                await self._rollback_quietly()
                if is_unique_constraint_error(exc):
                    msg = f"Risk override {override.id!r} already exists"
                    raise DuplicateRecordError(msg) from exc
                msg = "Failed to save risk override"
                logger.warning(
                    PERSISTENCE_RISK_OVERRIDE_SAVE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise PersistenceError(msg) from exc
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._rollback_quietly()
                msg = "Failed to save risk override"
                logger.warning(
                    PERSISTENCE_RISK_OVERRIDE_SAVE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise PersistenceError(msg) from exc

    async def get(
        self,
        override_id: NotBlankStr,
    ) -> RiskTierOverride | None:
        """Retrieve an override by ID.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            PersistenceError: If the persistence layer rejects the operation.
        """
        try:
            cursor = await self._db.execute(
                f"SELECT {_COLS} FROM risk_overrides "  # noqa: S608
                "WHERE id = ?",
                (override_id,),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to get risk override"
            logger.warning(
                PERSISTENCE_RISK_OVERRIDE_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise PersistenceError(msg) from exc

        if row is None:
            return None
        return _row_to_override(row)

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[RiskTierOverride, ...]:
        """List overrides ordered by id ascending (generic IdKeyed surface).

        Returns:
            The matching entities.

        Raises:
            PersistenceError: If the persistence layer rejects the operation.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_RISK_OVERRIDE_QUERY_FAILED
        )
        try:
            cursor = await self._db.execute(
                f"SELECT {_COLS} FROM risk_overrides "  # noqa: S608
                "ORDER BY id LIMIT ? OFFSET ?",
                (limit, offset),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list risk overrides"
            logger.warning(
                PERSISTENCE_RISK_OVERRIDE_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise PersistenceError(msg) from exc
        results: list[RiskTierOverride] = []
        for row in rows:
            try:
                results.append(_row_to_override(row))
            except (ValueError, ValidationError, TypeError) as exc:
                row_id = row[0] if row else "unknown"
                msg = f"Failed to deserialize risk override row {row_id!r}"
                logger.warning(
                    PERSISTENCE_RISK_OVERRIDE_QUERY_FAILED,
                    row_id=row_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise PersistenceError(msg) from exc
        return tuple(results)

    async def delete(self, override_id: NotBlankStr) -> bool:
        """Delete an override by ID.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            PersistenceError: If the persistence layer rejects the operation.
        """
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM risk_overrides WHERE id = ?",
                    (override_id,),
                )
                deleted = cursor.rowcount > 0
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._rollback_quietly()
                msg = f"Failed to delete risk override {override_id!r}"
                logger.warning(
                    PERSISTENCE_RISK_OVERRIDE_SAVE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise PersistenceError(msg) from exc
        return deleted

    async def list_active(
        self,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[RiskTierOverride, ...]:
        """Return active overrides bounded by *limit*.

        Args:
            limit: Maximum overrides to return (default
                :data:`DEFAULT_LIST_LIMIT`).

        Raises:
            PersistenceError: If the query or pagination validation
                fails.

        Returns:
            The matching entities.
        """
        limit = validate_pagination_args(
            limit, 0, event=PERSISTENCE_RISK_OVERRIDE_QUERY_FAILED
        )
        now_utc = format_iso_utc(datetime.now(UTC))
        try:
            cursor = await self._db.execute(
                f"SELECT {_COLS} FROM risk_overrides "  # noqa: S608
                "WHERE revoked_at IS NULL AND expires_at > ? "
                "ORDER BY created_at DESC LIMIT ?",
                (now_utc, limit),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list active overrides"
            logger.warning(
                PERSISTENCE_RISK_OVERRIDE_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise PersistenceError(msg) from exc

        results: list[RiskTierOverride] = []
        for row in rows:
            try:
                results.append(_row_to_override(row))
            except (ValueError, ValidationError, TypeError) as exc:
                # Never silently drop a malformed active override:
                # callers rely on ``list_active`` to return the full
                # current policy set, so a partial result would be a
                # dangerous security regression (missing overrides
                # mean risk rules silently revert to defaults).
                row_id = row[0] if row else "unknown"
                logger.warning(
                    PERSISTENCE_RISK_OVERRIDE_QUERY_FAILED,
                    row_id=row_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                msg = f"Failed to deserialize active risk override row {row_id!r}"
                raise PersistenceError(msg) from exc
        return tuple(results)

    async def revoke(
        self,
        override_id: NotBlankStr,
        *,
        revoked_by: NotBlankStr,
        revoked_at: datetime,
    ) -> bool:
        """Mark an override as revoked.

        Returns:
            ``True`` when the operation succeeded, ``False`` otherwise.

        Raises:
            PersistenceError: If the persistence layer rejects the operation.
        """
        revoked_at_utc = format_iso_utc(revoked_at)
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "UPDATE risk_overrides "
                    "SET revoked_at = ?, revoked_by = ? "
                    "WHERE id = ? AND revoked_at IS NULL",
                    (revoked_at_utc, revoked_by, override_id),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._rollback_quietly()
                msg = "Failed to revoke risk override"
                logger.warning(
                    PERSISTENCE_RISK_OVERRIDE_SAVE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise PersistenceError(msg) from exc

        return cursor.rowcount > 0


def _row_to_override(row: aiosqlite.Row) -> RiskTierOverride:
    """Convert a SQLite row to a RiskTierOverride.

    Returns:
        Result of type ``RiskTierOverride``.
    """
    (
        id_,
        action_type,
        original_tier,
        override_tier,
        reason,
        created_by,
        created_at,
        expires_at,
        revoked_at,
        revoked_by,
    ) = row

    return RiskTierOverride(
        id=id_,
        action_type=action_type,
        original_tier=ApprovalRiskLevel(original_tier),
        override_tier=ApprovalRiskLevel(override_tier),
        reason=reason,
        created_by=created_by,
        created_at=coerce_row_timestamp(created_at),
        expires_at=coerce_row_timestamp(expires_at),
        revoked_at=(
            coerce_row_timestamp(revoked_at) if revoked_at is not None else None
        ),
        revoked_by=revoked_by,
    )
