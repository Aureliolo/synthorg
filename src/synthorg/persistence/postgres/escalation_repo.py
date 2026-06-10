# module-kind: repository
"""Postgres repository for the human escalation queue.

Sibling of :class:`SQLiteEscalationRepository` backed by
``psycopg_pool.AsyncConnectionPool``.  Uses native ``JSONB`` for the
conflict snapshot and the decision payload, and ``TIMESTAMPTZ`` for
all timestamps -- mirrors the Postgres sibling pattern from
``parked_context_repo.py``. The LISTEN/NOTIFY plumbing lives in
:mod:`synthorg.persistence.postgres._escalation_notify`.
"""

import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import ClassVar, Literal, override
from uuid import UUID

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
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
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_REQUEST_ERROR
from synthorg.observability.events.conflict import (
    CONFLICT_ESCALATION_STATUS_TRANSITIONED,
)
from synthorg.persistence._shared import parse_iso_utc
from synthorg.persistence.postgres._escalation_notify import publish_notifies, subscribe

logger = get_logger(__name__)

_decision_adapter: TypeAdapter[EscalationDecision] = TypeAdapter(EscalationDecision)

_SELECT_COLS = (
    "id, conflict_id, conflict_json, status, "
    "created_at, expires_at, decided_at, decided_by, decision_json"
)


def _row_to_escalation(row: DictRow) -> Escalation:
    """Deserialise a Postgres row dict into an :class:`Escalation`.

    ``conflict_json`` and ``decision_json`` arrive as native Python
    objects (psycopg decodes ``JSONB`` automatically); the helper
    re-serialises them so Pydantic's ``model_validate_json`` path is
    exercised uniformly across backends.  Timestamps (``created_at``,
    ``expires_at``, ``decided_at``) arrive as native tz-aware
    ``datetime`` objects from ``TIMESTAMPTZ`` columns and are
    validated by Pydantic's ``AwareDatetime`` type on the
    :class:`Escalation` model.

    Returns:
        Result of type ``Escalation``.

    Raises:
        QueryError: If row parsing or validation fails.
    """
    try:
        conflict = Conflict.model_validate(row["conflict_json"])
        decision: EscalationDecision | None = None
        if row["decision_json"] is not None:
            decision = _decision_adapter.validate_python(row["decision_json"])
        return Escalation(
            id=UUID(str(row["id"])),
            conflict=conflict,
            status=EscalationStatus(str(row["status"])),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            decided_at=row["decided_at"],
            decided_by=(
                str(row["decided_by"]) if row["decided_by"] is not None else None
            ),
            decision=decision,
        )
    except (json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
        row_id = str(row.get("id", "<unknown>"))
        logger.warning(
            API_REQUEST_ERROR,
            row_id=row_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to parse escalation row {row_id!r}"
        raise QueryError(msg) from exc


class PostgresEscalationRepository(EscalationQueueStore):
    """``psycopg``-backed :class:`EscalationQueueStore`.

    Implements the
    :class:`~synthorg.communication.conflict_resolution.escalation.protocol.CrossInstanceNotifyCapableStore`
    capability marker so the escalation factory can structurally
    detect that this store delivers real LISTEN/NOTIFY plumbing
    without reaching for the concrete class.

    Args:
        pool: Open ``psycopg_pool.AsyncConnectionPool`` owned by the
            :class:`PostgresPersistenceBackend`.
    """

    supports_cross_instance_notify: ClassVar[Literal[True]] = True

    def __init__(
        self,
        pool: AsyncConnectionPool,
        *,
        notify_channel: str | None = None,
    ) -> None:
        """Initialise the repository with a shared connection pool.

        Args:
            pool: Open ``psycopg_pool.AsyncConnectionPool`` owned by the
                :class:`PostgresPersistenceBackend`.
            notify_channel: Optional LISTEN/NOTIFY channel name.  When
                set, the repository publishes ``<id>:<status>`` payloads
                on every terminal transition so a cross-instance
                :class:`EscalationNotifySubscriber` can wake resolvers
                on other workers.  ``None`` disables publication, which
                matches the single-worker default.
        """
        self._pool = pool
        self._notify_channel = notify_channel

    @property
    def pool(self) -> AsyncConnectionPool:
        """Return the underlying connection pool.

        Exposed for the cross-instance notify subscriber, which must
        reuse the repository's pool to share credentials and pool
        sizing with the rest of the persistence layer.

        Returns:
            Result of type ``AsyncConnectionPool``.
        """
        return self._pool

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
        conflict_payload = Jsonb(escalation.conflict.model_dump(mode="json"))
        params = {
            "id": str(escalation.id),
            "conflict_id": str(escalation.conflict.id),
            "conflict_json": conflict_payload,
            "status": escalation.status.value,
            "created_at": escalation.created_at,
            "expires_at": escalation.expires_at,
            "decided_at": None,
            "decided_by": None,
            "decision_json": None,
        }
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """\
INSERT INTO conflict_escalations (
    id, conflict_id, conflict_json, status,
    created_at, expires_at, decided_at, decided_by, decision_json
) VALUES (
    %(id)s, %(conflict_id)s, %(conflict_json)s, %(status)s,
    %(created_at)s, %(expires_at)s, %(decided_at)s, %(decided_by)s, %(decision_json)s
)""",
                    params,
                )
                await conn.commit()
        except psycopg.errors.UniqueViolation as exc:
            # Distinguish the two possible unique violations so callers
            # and logs see an accurate reason:
            #   * ``idx_conflict_escalations_unique_pending_conflict``
            #     -> a PENDING row for the same conflict already exists
            #   * otherwise (primary key on ``id``) -> duplicate escalation id.
            constraint_name = getattr(exc.diag, "constraint_name", None) or ""
            if constraint_name == "idx_conflict_escalations_unique_pending_conflict":
                msg = (
                    f"Pending escalation for conflict "
                    f"{escalation.conflict.id!r} already exists"
                )
                error_type = "escalation_create_duplicate_pending_conflict"
            else:
                msg = f"Escalation {escalation.id!r} already exists"
                error_type = "escalation_create_duplicate_id"
            logger.warning(
                API_REQUEST_ERROR,
                error_type=error_type,
                escalation_id=str(escalation.id),
                conflict_id=str(escalation.conflict.id),
                constraint=constraint_name or None,
                error=safe_error_description(exc),
            )
            raise ConstraintViolationError(
                msg,
                constraint=constraint_name or str(exc),
            ) from exc
        except psycopg.Error as exc:
            msg = f"Failed to create escalation {escalation.id!r}: {safe_error_description(exc)}"  # noqa: E501
            logger.warning(
                API_REQUEST_ERROR,
                error_type="escalation_create_failed",
                escalation_id=str(escalation.id),
                conflict_id=str(escalation.conflict.id),
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    @override
    async def get(self, escalation_id: str) -> Escalation | None:
        """Fetch by ID.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_SELECT_COLS} FROM conflict_escalations "  # noqa: S608
                    "WHERE id = %s",
                    (escalation_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch escalation {escalation_id!r}: {safe_error_description(exc)}"  # noqa: E501
            logger.warning(
                API_REQUEST_ERROR,
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
        if status is not None:
            where_sql = "WHERE status = %s"
            where_params: tuple[object, ...] = (status.value,)
        else:
            where_sql = ""
            where_params = ()
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT COUNT(*) AS total FROM conflict_escalations {where_sql}",  # noqa: S608
                    where_params,
                )
                count_row = await cur.fetchone()
                total = int(count_row["total"]) if count_row is not None else 0
                await cur.execute(
                    f"SELECT {_SELECT_COLS} FROM conflict_escalations "  # noqa: S608
                    f"{where_sql} ORDER BY created_at ASC "
                    "LIMIT %s OFFSET %s",
                    (*where_params, limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = f"Failed to list escalations: {safe_error_description(exc)}"
            logger.warning(
                API_REQUEST_ERROR,
                error_type="escalation_list_failed",
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        # Corrupt-row resilience: skip + log instead of failing the whole page.
        page_items: list[Escalation] = []
        for row in rows:
            try:
                page_items.append(_row_to_escalation(row))
            except QueryError as exc:
                logger.warning(
                    API_REQUEST_ERROR,
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
        return await self._update_terminal(
            escalation_id,
            new_status=EscalationStatus.DECIDED,
            decided_by=decided_by,
            decision=decision,
        )

    @override
    async def cancel(self, escalation_id: str, *, cancelled_by: str) -> Escalation:
        """Transition PENDING -> CANCELLED.

        Returns:
            Result of type ``Escalation``.
        """
        return await self._update_terminal(
            escalation_id,
            new_status=EscalationStatus.CANCELLED,
            decided_by=cancelled_by,
            decision=None,
        )

    @override
    async def mark_expired(self, now_iso: str) -> tuple[str, ...]:
        """Expire PENDING rows past their deadline.

        Sets ``decided_by = 'system:expiry'`` so audit consumers can
        distinguish sweeper-driven expiry from operator-driven
        cancellation (``system:resolver_cancelled``) or human
        decisions (``human:<operator_id>``).

        Returns:
            The matching collection.

        Raises:
            QueryError: If the database query fails.
        """
        now_dt = parse_iso_utc(now_iso)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor() as cur,
            ):
                await cur.execute(
                    "UPDATE conflict_escalations SET "
                    "status = 'expired', decided_at = %s, "
                    "decided_by = 'system:expiry' "
                    "WHERE status = 'pending' "
                    "AND expires_at IS NOT NULL AND expires_at <= %s "
                    "RETURNING id",
                    (now_dt, now_dt),
                )
                rows = await cur.fetchall()
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to mark escalations expired: {safe_error_description(exc)}"
            logger.warning(
                API_REQUEST_ERROR,
                error_type="escalation_mark_expired_failed",
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        ids = tuple(str(r[0]) for r in rows)
        for escalation_id in ids:
            logger.info(
                CONFLICT_ESCALATION_STATUS_TRANSITIONED,
                escalation_id=escalation_id,
                from_status=EscalationStatus.PENDING.value,
                to_status=EscalationStatus.EXPIRED.value,
            )
        await self._publish_notifies(ids, "expired")
        return ids

    @override
    async def close(self) -> None:
        """No-op: the pool is owned by the persistence backend."""
        return

    async def _publish_notifies(
        self,
        escalation_ids: tuple[str, ...],
        status: str,
    ) -> None:
        """Publish one ``<id>:<status>`` NOTIFY per id over a single checkout.

        Thin instance seam over the module-level
        :func:`synthorg.persistence.postgres._escalation_notify.publish_notifies`,
        binding the repository's pool and configured notify channel so the
        terminal-transition and expiry paths share one publish call shape.
        A ``None`` channel (single-worker default) disables publication.
        """
        await publish_notifies(self._pool, self._notify_channel, escalation_ids, status)

    async def _update_terminal(
        self,
        escalation_id: str,
        *,
        new_status: EscalationStatus,
        decided_by: str,
        decision: EscalationDecision | None,
    ) -> Escalation:
        """Apply a terminal state transition gated on current status.

        Returns:
            Result of type ``Escalation``.

        Raises:
            QueryError: If the database query fails.
            ValueError: If an argument fails validation.
            KeyError: If a required dictionary key is missing.
        """
        decided_at = datetime.now(UTC)
        decision_payload: Jsonb | None = None
        if decision is not None:
            decision_payload = Jsonb(
                json.loads(_decision_adapter.dump_json(decision).decode("utf-8")),
            )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "UPDATE conflict_escalations SET "  # noqa: S608
                    "status = %s, decided_at = %s, decided_by = %s, "
                    "decision_json = %s "
                    "WHERE id = %s AND status = 'pending' "
                    f"RETURNING {_SELECT_COLS}",
                    (
                        new_status.value,
                        decided_at,
                        decided_by,
                        decision_payload,
                        escalation_id,
                    ),
                )
                updated_row = await cur.fetchone()
                await conn.commit()
                if updated_row is None:
                    await cur.execute(
                        "SELECT status FROM conflict_escalations WHERE id = %s",
                        (escalation_id,),
                    )
                    existing = await cur.fetchone()
                    if existing is None:
                        msg = f"Escalation {escalation_id!r} not found"
                        raise KeyError(msg)
                    msg = (
                        f"Escalation {escalation_id!r} is "
                        f"{existing['status']}, cannot transition to "
                        f"{new_status.value}"
                    )
                    raise ValueError(msg)
        except psycopg.Error as exc:
            msg = f"Failed to update escalation {escalation_id!r}: {safe_error_description(exc)}"  # noqa: E501
            logger.warning(
                API_REQUEST_ERROR,
                error_type="escalation_update_failed",
                escalation_id=escalation_id,
                target_status=new_status.value,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.info(
            CONFLICT_ESCALATION_STATUS_TRANSITIONED,
            escalation_id=escalation_id,
            from_status=EscalationStatus.PENDING.value,
            to_status=new_status.value,
        )
        await self._publish_notifies((escalation_id,), new_status.value)
        return _row_to_escalation(updated_row)

    @override
    def subscribe_notifications(
        self,
        channel: str,
    ) -> AbstractAsyncContextManager[AsyncIterator[str]]:
        """Subscribe to Postgres LISTEN/NOTIFY on *channel*.

        Delegates to
        :func:`synthorg.persistence.postgres._escalation_notify.subscribe`,
        which holds a dedicated pool connection for the lifetime of the
        subscription. Operators enabling cross-instance notify MUST size
        ``pool_min_size`` to reserve at least one slot per API worker so
        LISTEN does not starve other borrowers.

        Returns:
            An async context manager yielding the payload iterator.
        """
        return subscribe(self._pool, channel)
