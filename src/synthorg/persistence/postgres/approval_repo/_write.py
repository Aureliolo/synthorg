"""Write-path mixin for the Postgres approval repository."""

from datetime import datetime
from typing import TYPE_CHECKING

import psycopg

from synthorg.approval.enums import ApprovalStatus
from synthorg.core.approval import ApprovalItem
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APPROVAL_REPO_FAILED
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence.postgres.approval_repo._base import _ApprovalRepoBase
from synthorg.persistence.postgres.approval_repo._marshalling import item_save_params
from synthorg.persistence.postgres.approval_repo._sql import APPROVALS_UPSERT_SQL

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = get_logger(__name__)


def _constraint_name(exc: psycopg.errors.IntegrityError) -> str:
    """Extract the violated constraint name from an integrity error.

    Returns:
        The constraint name, or ``"<unknown>"`` when unavailable.
    """
    return getattr(getattr(exc, "diag", None), "constraint_name", None) or "<unknown>"


class _WriteMixin(_ApprovalRepoBase):
    """Insert / upsert / CAS / delete operations for approval items."""

    async def save(self, item: ApprovalItem) -> None:
        """Upsert an approval item.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        params = item_save_params(item)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(APPROVALS_UPSERT_SQL, params)
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
            msg = f"Constraint violation saving approval {item.id!r}"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                approval_id=str(item.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConstraintViolationError(
                msg,
                constraint=_constraint_name(exc),
            ) from exc
        except psycopg.Error as exc:
            msg = f"Failed to save approval {item.id!r}"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                approval_id=str(item.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def save_many(self, items: Sequence[ApprovalItem]) -> None:
        """Upsert multiple approval items in a single transaction.

        Empty input is a no-op.  Single-item input falls back to
        :meth:`save` so the per-item error context still names the
        offending id on constraint violation.

        Raises:
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        if not items:
            return
        if len(items) == 1:
            await self.save(items[0])
            return
        param_rows = [item_save_params(item) for item in items]
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.executemany(APPROVALS_UPSERT_SQL, param_rows)
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
            msg = f"Constraint violation saving approval batch (size={len(items)})"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                batch_size=len(items),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConstraintViolationError(
                msg, constraint=_constraint_name(exc)
            ) from exc
        except psycopg.Error as exc:
            msg = f"Failed to save approval batch (size={len(items)})"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                batch_size=len(items),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def expire_if_pending(
        self, ids: Sequence[NotBlankStr]
    ) -> tuple[NotBlankStr, ...]:
        """Compare-and-set: flip rows still PENDING to EXPIRED.

        Uses ``UPDATE ... WHERE id = ANY(%s) AND status='pending'
        RETURNING id`` so the compare-and-set is atomic at the row
        level and the returned ids reflect what actually transitioned.

        Returns:
            The matching collection.

        Raises:
            QueryError: If the database query fails.
        """
        if not ids:
            return ()
        sql = (
            "UPDATE approvals SET status = %s "
            "WHERE id = ANY(%s) "
            "AND status = %s "
            "RETURNING id"
        )
        params = (
            ApprovalStatus.EXPIRED.value,
            list(ids),
            ApprovalStatus.PENDING.value,
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to expire approval batch (size={len(ids)})"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                batch_size=len(ids),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(NotBlankStr(row[0]) for row in rows)

    async def transition_if(
        self,
        entity_id: NotBlankStr,
        from_state: ApprovalStatus,
        to_state: ApprovalStatus,
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for approval state transitions.

        Transitions the approval from ``from_state`` to ``to_state`` iff
        the current persisted status matches ``from_state``. Returns
        ``True`` iff the state transition succeeded.

        Decision metadata (``decided_at`` / ``decided_by`` /
        ``decision_json``) is governed by a table CHECK constraint that
        requires the full triple together, so partial writes through
        this method are rejected rather than silently dropped: pass an
        empty ``updates`` and persist the decision triple via the
        dedicated decision path.

        Args:
            entity_id: The approval id.
            from_state: Expected current status.
            to_state: Target status.
            **updates: Must be empty; any keys raise ``QueryError``.

        Returns:
            ``True`` iff the transition succeeded, ``False`` on state
            mismatch or when no row exists.

        Raises:
            QueryError: On database errors, or if ``updates`` is
                non-empty (status-correlated writes are not supported
                through this CAS path).
        """
        if updates:
            msg = (
                "transition_if does not persist decision metadata "
                f"(got keys {sorted(updates)!r}); the approvals CHECK "
                "constraint requires the full decision triple, so use "
                "the dedicated decision path instead"
            )
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                approval_id=entity_id,
                error=msg,
            )
            raise QueryError(msg)
        sql = "UPDATE approvals SET status = %s WHERE id = %s AND status = %s"
        params = (to_state.value, entity_id, from_state.value)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                updated = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to transition approval {entity_id!r}"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                approval_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return updated

    async def consume_if_approved(
        self,
        approval_id: NotBlankStr,
        *,
        consumed_at: datetime,
    ) -> bool:
        """Atomic compare-and-set: mark an APPROVED grant as consumed.

        Sets ``consumed_at`` iff the row is currently ``approved`` and not
        already consumed, so a one-shot approval can authorise exactly one
        action. Returns ``True`` iff this call won the race (rowcount == 1);
        ``False`` on replay (already consumed), state mismatch (not
        approved), or missing row.

        Args:
            approval_id: The approval id.
            consumed_at: Aware UTC timestamp to stamp on success.

        Returns:
            ``True`` iff the grant was consumed by this call.

        Raises:
            QueryError: On database errors.
        """
        sql = (
            "UPDATE approvals SET consumed_at = %s "
            "WHERE id = %s AND status = %s AND consumed_at IS NULL"
        )
        # Normalise to aware-UTC before binding to TIMESTAMPTZ so the
        # stored instant matches the SQLite path (which goes through
        # format_iso_utc) regardless of the session timezone.
        params = (
            normalize_utc(consumed_at),
            approval_id,
            ApprovalStatus.APPROVED.value,
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                consumed = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to consume approval {approval_id!r}"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                approval_id=approval_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return consumed

    async def delete(self, approval_id: NotBlankStr) -> bool:
        """Delete an approval item; returns True when a row was removed.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database operation fails.
        """
        sql = "DELETE FROM approvals WHERE id = %s"
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, (approval_id,))
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete approval {approval_id!r}"
            logger.warning(
                API_APPROVAL_REPO_FAILED,
                approval_id=approval_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted


__all__ = ["_WriteMixin"]
