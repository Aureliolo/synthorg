"""Postgres repository for the human escalation queue (#1418).

Sibling of :class:`SQLiteEscalationRepository` backed by
``psycopg_pool.AsyncConnectionPool``.  Uses native ``JSONB`` for the
conflict snapshot and the decision payload, and ``TIMESTAMPTZ`` for
all timestamps -- mirrors the Postgres sibling pattern from
``parked_context_repo.py``.
"""

import contextlib
import json
import re
from collections.abc import AsyncIterator  # noqa: TC003
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from synthorg.communication.conflict_resolution.escalation.models import (
    Escalation,
    EscalationDecision,
    EscalationStatus,
)
from synthorg.communication.conflict_resolution.escalation.protocol import (
    EscalationQueueStore,
)
from synthorg.communication.conflict_resolution.models import Conflict
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_REQUEST_ERROR
from synthorg.persistence._shared import parse_iso_utc
from synthorg.persistence.errors import ConstraintViolationError, QueryError

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)

_DEFAULT_LIMIT = 50
_DEFAULT_OFFSET = 0

# Postgres unquoted identifier regex (defence-in-depth for LISTEN /
# UNLISTEN arg interpolation in ``subscribe_notifications``).
_SAFE_IDENTIFIER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*$",
)
_MAX_IDENTIFIER_LEN: Final[int] = 63

_decision_adapter: TypeAdapter[EscalationDecision] = TypeAdapter(EscalationDecision)

_SELECT_COLS = (
    "id, conflict_id, conflict_json, status, "
    "created_at, expires_at, decided_at, decided_by, decision_json"
)


def _row_to_escalation(row: dict[str, Any]) -> Escalation:
    """Deserialise a Postgres row dict into an :class:`Escalation`.

    ``conflict_json`` and ``decision_json`` arrive as native Python
    objects (psycopg decodes ``JSONB`` automatically); the helper
    re-serialises them so Pydantic's ``model_validate_json`` path is
    exercised uniformly across backends.  Timestamps (``created_at``,
    ``expires_at``, ``decided_at``) arrive as native tz-aware
    ``datetime`` objects from ``TIMESTAMPTZ`` columns and are
    validated by Pydantic's ``AwareDatetime`` type on the
    :class:`Escalation` model.
    """
    try:
        conflict = Conflict.model_validate(row["conflict_json"])
        decision: EscalationDecision | None = None
        if row["decision_json"] is not None:
            decision = _decision_adapter.validate_python(row["decision_json"])
        return Escalation(
            id=str(row["id"]),
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

    Args:
        pool: Open ``psycopg_pool.AsyncConnectionPool`` owned by the
            :class:`PostgresPersistenceBackend`.
    """

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
        """
        return self._pool

    async def create(self, escalation: Escalation) -> None:
        """Insert a PENDING escalation row."""
        if escalation.status != EscalationStatus.PENDING:
            msg = "create() requires status=PENDING"
            raise ValueError(msg)
        conflict_payload = Jsonb(escalation.conflict.model_dump(mode="json"))
        params = {
            "id": escalation.id,
            "conflict_id": escalation.conflict.id,
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
                escalation_id=escalation.id,
                conflict_id=escalation.conflict.id,
                constraint=constraint_name or None,
                error=safe_error_description(exc),
            )
            raise ConstraintViolationError(
                msg,
                constraint=constraint_name or str(exc),
            ) from exc
        except psycopg.Error as exc:
            msg = f"Failed to create escalation {escalation.id!r}: {exc}"
            logger.warning(
                API_REQUEST_ERROR,
                error_type="escalation_create_failed",
                escalation_id=escalation.id,
                conflict_id=escalation.conflict.id,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, escalation_id: str) -> Escalation | None:
        """Fetch by ID."""
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
            msg = f"Failed to fetch escalation {escalation_id!r}: {exc}"
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

    async def list_items(
        self,
        *,
        status: EscalationStatus | None = EscalationStatus.PENDING,
        limit: int = _DEFAULT_LIMIT,
        offset: int = _DEFAULT_OFFSET,
    ) -> tuple[tuple[Escalation, ...], int]:
        """Page over rows filtered by status."""
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
            msg = f"Failed to list escalations: {exc}"
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

    async def apply_decision(
        self,
        escalation_id: str,
        *,
        decision: EscalationDecision,
        decided_by: str,
    ) -> Escalation:
        """Transition PENDING -> DECIDED atomically."""
        return await self._update_terminal(
            escalation_id,
            new_status=EscalationStatus.DECIDED,
            decided_by=decided_by,
            decision=decision,
        )

    async def cancel(self, escalation_id: str, *, cancelled_by: str) -> Escalation:
        """Transition PENDING -> CANCELLED."""
        return await self._update_terminal(
            escalation_id,
            new_status=EscalationStatus.CANCELLED,
            decided_by=cancelled_by,
            decision=None,
        )

    async def mark_expired(self, now_iso: str) -> tuple[str, ...]:
        """Expire PENDING rows past their deadline.

        Sets ``decided_by = 'system:expiry'`` so audit consumers can
        distinguish sweeper-driven expiry from operator-driven
        cancellation (``system:resolver_cancelled``) or human
        decisions (``human:<operator_id>``).
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
            msg = f"Failed to mark escalations expired: {exc}"
            logger.warning(
                API_REQUEST_ERROR,
                error_type="escalation_mark_expired_failed",
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        ids = tuple(str(r[0]) for r in rows)
        for escalation_id in ids:
            await self._publish_notify(escalation_id, "expired")
        return ids

    async def close(self) -> None:
        """No-op: the pool is owned by the persistence backend."""
        return

    async def _update_terminal(
        self,
        escalation_id: str,
        *,
        new_status: EscalationStatus,
        decided_by: str,
        decision: EscalationDecision | None,
    ) -> Escalation:
        """Apply a terminal state transition gated on current status."""
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
            msg = f"Failed to update escalation {escalation_id!r}: {exc}"
            logger.warning(
                API_REQUEST_ERROR,
                error_type="escalation_update_failed",
                escalation_id=escalation_id,
                target_status=new_status.value,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        await self._publish_notify(escalation_id, new_status.value)
        return _row_to_escalation(updated_row)

    @asynccontextmanager
    async def subscribe_notifications(
        self,
        channel: str,
    ) -> AsyncIterator[AsyncIterator[str]]:
        """Subscribe to Postgres LISTEN/NOTIFY on *channel*.

        Holds a dedicated pool connection for the lifetime of the
        subscription (LISTEN is session-level state). Operators
        enabling cross-instance notify MUST size ``pool_min_size`` to
        reserve at least one slot per API worker so LISTEN does not
        starve other borrowers.

        Raises:
            ValueError: If *channel* is not a safe Postgres unquoted
                identifier. Defence-in-depth -- config + caller already
                validate, but the repo re-checks so a stray caller
                cannot inject SQL via ``LISTEN "<channel>"``.
        """
        if (
            not channel
            or len(channel) > _MAX_IDENTIFIER_LEN
            or _SAFE_IDENTIFIER_PATTERN.fullmatch(channel) is None
        ):
            msg = (
                f"notify channel {channel!r} is not a safe Postgres "
                "identifier (must match ^[A-Za-z_][A-Za-z0-9_]*$, "
                f"max {_MAX_IDENTIFIER_LEN} chars)"
            )
            raise ValueError(msg)

        async with self._pool.connection() as conn:
            original_autocommit = getattr(conn, "autocommit", False)
            await conn.set_autocommit(True)
            # Track whether session state was left in a non-pristine state:
            # if UNLISTEN or autocommit restore fails, this connection must
            # not be returned to the pool with altered state (silent reuse
            # would strand LISTEN registrations on other operators' backs).
            session_tainted = False
            try:
                await conn.execute(f'LISTEN "{channel}"')
                notifies_gen = conn.notifies()

                async def _payloads() -> AsyncIterator[str]:
                    async for notify in notifies_gen:
                        yield notify.payload

                try:
                    yield _payloads()
                finally:
                    await notifies_gen.aclose()
            finally:
                try:
                    await conn.execute(f'UNLISTEN "{channel}"')
                except Exception as exc:
                    session_tainted = True
                    logger.warning(
                        API_REQUEST_ERROR,
                        error_type="escalation_unlisten_failed",
                        error=safe_error_description(exc),
                        channel=channel,
                    )
                try:
                    await conn.set_autocommit(bool(original_autocommit))
                except Exception as exc:
                    session_tainted = True
                    logger.warning(
                        API_REQUEST_ERROR,
                        error_type="escalation_autocommit_restore_failed",
                        error=safe_error_description(exc),
                        channel=channel,
                    )
                if session_tainted:
                    # Close the physical connection so the pool discards it
                    # rather than handing altered session state to the next
                    # borrower.
                    with contextlib.suppress(Exception):
                        await conn.close()

    async def _publish_notify(self, escalation_id: str, status: str) -> None:
        """Publish ``<id>:<status>`` on the configured NOTIFY channel.

        Best-effort: failure is logged and swallowed because the
        persistent state has already been committed and the sweeper
        can still reap stale rows even if the signal is missed.
        """
        channel = self._notify_channel
        if channel is None or not escalation_id or not status:
            return
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                payload = f"{escalation_id}:{status}"
                # Channel name and payload are quoted server-side; we
                # send them as query parameters to avoid SQL injection.
                await cur.execute(
                    "SELECT pg_notify(%s, %s)",
                    (channel, payload),
                )
                await conn.commit()
        except psycopg.Error as exc:
            logger.warning(
                API_REQUEST_ERROR,
                error_type="escalation_notify_failed",
                escalation_id=escalation_id,
                channel=channel,
                error=safe_error_description(exc),
            )
