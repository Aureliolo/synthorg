"""Postgres implementation of the RiskOverrideRepository protocol.

This is the Postgres sibling of src/synthorg/persistence/sqlite/risk_override_repo.py.
Postgres stores timestamps as native TIMESTAMPTZ. Per-connection transactions
handle isolation without explicit write locks.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import psycopg
from psycopg.rows import dict_row
from pydantic import AwareDatetime, ValidationError

from synthorg.core.enums import ApprovalRiskLevel
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_RISK_OVERRIDE_QUERY_FAILED,
    PERSISTENCE_RISK_OVERRIDE_REVOKE_FAILED,
    PERSISTENCE_RISK_OVERRIDE_REVOKED,
    PERSISTENCE_RISK_OVERRIDE_SAVE_FAILED,
)
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence.errors import DuplicateRecordError, QueryError
from synthorg.security.rules.risk_override import RiskTierOverride

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)

_COLS = (
    "id, action_type, original_tier, override_tier, reason, "
    "created_by, created_at, expires_at, revoked_at, revoked_by"
)


class PostgresRiskOverrideRepository:
    """Postgres implementation of the RiskOverrideRepository protocol.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, override: RiskTierOverride) -> None:
        """Persist a new risk tier override.

        Args:
            override: The override to save.

        Raises:
            DuplicateRecordError: If an override with the same ID exists.
            QueryError: If the save fails.
        """
        created_at_utc = normalize_utc(override.created_at)
        expires_at_utc = normalize_utc(override.expires_at)
        revoked_at_utc = (
            normalize_utc(override.revoked_at) if override.revoked_at else None
        )

        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    f"INSERT INTO risk_overrides ({_COLS}) "  # noqa: S608
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
                await conn.commit()
        except psycopg.errors.UniqueViolation as exc:
            msg = f"Risk override {override.id!r} already exists"
            logger.warning(
                PERSISTENCE_RISK_OVERRIDE_SAVE_FAILED,
                override_id=override.id,
                error=msg,
            )
            raise DuplicateRecordError(msg) from exc
        except psycopg.Error as exc:
            msg = "Failed to save risk override"
            logger.warning(
                PERSISTENCE_RISK_OVERRIDE_SAVE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(
        self,
        override_id: NotBlankStr,
    ) -> RiskTierOverride | None:
        """Retrieve an override by ID."""
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_COLS} FROM risk_overrides WHERE id = %s",  # noqa: S608
                    (override_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = "Failed to get risk override"
            logger.warning(
                PERSISTENCE_RISK_OVERRIDE_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        if row is None:
            return None
        try:
            return _row_to_override(row)
        except (ValueError, ValidationError) as exc:
            msg = f"Failed to deserialize risk override {override_id!r}"
            logger.warning(
                PERSISTENCE_RISK_OVERRIDE_QUERY_FAILED,
                override_id=override_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def list_active(self) -> tuple[RiskTierOverride, ...]:
        """Return all active (non-expired, non-revoked) overrides."""
        now_utc = datetime.now(tz=UTC)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_COLS} FROM risk_overrides "  # noqa: S608
                    "WHERE revoked_at IS NULL AND expires_at > %s "
                    "ORDER BY created_at DESC",
                    (now_utc,),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list active overrides"
            logger.warning(
                PERSISTENCE_RISK_OVERRIDE_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

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
                row_id = row.get("id") if row else "unknown"
                logger.warning(
                    PERSISTENCE_RISK_OVERRIDE_QUERY_FAILED,
                    row_id=row_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                msg = f"Failed to deserialize active risk override row {row_id!r}"
                raise QueryError(msg) from exc
        return tuple(results)

    async def revoke(
        self,
        override_id: NotBlankStr,
        *,
        revoked_by: NotBlankStr,
        revoked_at: AwareDatetime,
    ) -> bool:
        """Mark an override as revoked."""
        revoked_at_utc = normalize_utc(revoked_at)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "UPDATE risk_overrides "
                    "SET revoked_at = %s, revoked_by = %s "
                    "WHERE id = %s AND revoked_at IS NULL",
                    (revoked_at_utc, revoked_by, override_id),
                )
                revoked = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to revoke risk override"
            logger.warning(
                PERSISTENCE_RISK_OVERRIDE_REVOKE_FAILED,
                override_id=override_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        if revoked:
            logger.info(
                PERSISTENCE_RISK_OVERRIDE_REVOKED,
                override_id=override_id,
                revoked_by=revoked_by,
            )
        return revoked


def _row_to_override(row: dict[str, object]) -> RiskTierOverride:
    """Convert a Postgres row to a RiskTierOverride."""
    return RiskTierOverride(
        id=str(row["id"]),
        action_type=str(row["action_type"]),
        original_tier=ApprovalRiskLevel(str(row["original_tier"])),
        override_tier=ApprovalRiskLevel(str(row["override_tier"])),
        reason=str(row["reason"]),
        created_by=str(row["created_by"]),
        created_at=normalize_utc(cast("datetime", row["created_at"])),
        expires_at=normalize_utc(cast("datetime", row["expires_at"])),
        revoked_at=(
            normalize_utc(cast("datetime", row["revoked_at"]))
            if row.get("revoked_at")
            else None
        ),
        revoked_by=cast("str | None", row.get("revoked_by")),
    )
