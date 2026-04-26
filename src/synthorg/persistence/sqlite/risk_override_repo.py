"""SQLite repository implementation for risk tier overrides."""

import asyncio
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import aiosqlite
from pydantic import AwareDatetime, ValidationError

from synthorg.core.enums import ApprovalRiskLevel
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_RISK_OVERRIDE_QUERY_FAILED,
    PERSISTENCE_RISK_OVERRIDE_SAVE_FAILED,
)
from synthorg.persistence._shared import format_iso_utc, parse_iso_utc
from synthorg.persistence.errors import DuplicateRecordError, PersistenceError
from synthorg.security.rules.risk_override import RiskTierOverride

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

_COLS = (
    "id, action_type, original_tier, override_tier, reason, "
    "created_by, created_at, expires_at, revoked_at, revoked_by"
)


def _is_unique_constraint_error(exc: sqlite3.IntegrityError) -> bool:
    """Return True for UNIQUE/PRIMARY KEY violations."""
    return exc.sqlite_errorname in {
        "SQLITE_CONSTRAINT_UNIQUE",
        "SQLITE_CONSTRAINT_PRIMARYKEY",
    }


class SQLiteRiskOverrideRepository:
    """SQLite implementation of the RiskOverrideRepository protocol.

    Args:
        db: An open aiosqlite connection.
        write_lock: Optional shared lock protecting multi-statement
            transactions.  Defaults to a per-instance lock for test
            ergonomics; production wiring injects the backend's
            shared lock.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_lock: asyncio.Lock | None = None,
    ) -> None:
        self._db = db
        self._write_lock = write_lock if write_lock is not None else asyncio.Lock()

    async def _rollback_quietly(self) -> None:
        """Roll back the current transaction, swallowing errors."""
        try:
            await self._db.rollback()
        except Exception:
            logger.warning(
                PERSISTENCE_RISK_OVERRIDE_SAVE_FAILED,
                error="rollback failed",
                exc_info=True,
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

        try:
            async with self._write_lock:
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
            if _is_unique_constraint_error(exc):
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
        """Retrieve an override by ID."""
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

    async def list_active(self) -> tuple[RiskTierOverride, ...]:
        """Return all active (non-expired, non-revoked) overrides."""
        now_utc = format_iso_utc(datetime.now(UTC))
        try:
            cursor = await self._db.execute(
                f"SELECT {_COLS} FROM risk_overrides "  # noqa: S608
                "WHERE revoked_at IS NULL AND expires_at > ? "
                "ORDER BY created_at DESC",
                (now_utc,),
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
            except (ValueError, ValidationError) as exc:
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
        revoked_at: AwareDatetime,
    ) -> bool:
        """Mark an override as revoked."""
        revoked_at_utc = format_iso_utc(revoked_at)
        try:
            async with self._write_lock:
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


def _row_to_override(row: Any) -> RiskTierOverride:
    """Convert a SQLite row to a RiskTierOverride."""
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
        created_at=parse_iso_utc(created_at),
        expires_at=parse_iso_utc(expires_at),
        revoked_at=(parse_iso_utc(revoked_at) if revoked_at else None),
        revoked_by=revoked_by,
    )
