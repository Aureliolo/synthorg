"""In-memory escalation queue store.

Process-local backend for the :class:`EscalationQueueStore` Protocol.
Used for tests and ephemeral deployments; production deployments
should use ``sqlite`` or ``postgres`` via
:func:`build_escalation_queue_store`.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Final, override

from synthorg.communication.conflict_resolution.escalation.models import (
    Escalation,
    EscalationDecision,
    EscalationStatus,
)
from synthorg.communication.conflict_resolution.escalation.protocol import (
    EscalationQueueStore,
)
from synthorg.core.iso_datetime import parse_iso_utc
from synthorg.observability import get_logger
from synthorg.observability.events.conflict import (
    CONFLICT_ESCALATION_CANCELLED,
    CONFLICT_ESCALATION_EXPIRED,
    CONFLICT_ESCALATION_QUEUED,
    CONFLICT_ESCALATION_RESOLVED,
    CONFLICT_ESCALATION_STATUS_TRANSITIONED,
)

logger = get_logger(__name__)

_DEFAULT_LIMIT: Final[int] = 50
_DEFAULT_OFFSET = 0


class InMemoryEscalationStore(EscalationQueueStore):
    """Dict-backed escalation queue with asyncio-safe writes."""

    def __init__(self) -> None:
        """Initialise an empty store."""
        self._rows: dict[str, Escalation] = {}
        self._lock = asyncio.Lock()

    @override
    async def create(self, escalation: Escalation) -> None:
        """Insert a PENDING escalation.

        Raises:
            ValueError: ``escalation.status`` is not PENDING, the
                ``escalation.id`` already exists, or a PENDING row
                already exists for the same ``conflict.id`` -- the
                queue enforces "at most one active escalation per
                conflict" to match the Postgres partial-unique index.
        """
        if escalation.status != EscalationStatus.PENDING:
            msg = "create() requires status=PENDING"
            logger.warning(
                CONFLICT_ESCALATION_QUEUED,
                escalation_id=str(escalation.id),
                conflict_id=escalation.conflict.id,
                note="non_pending_rejected",
            )
            raise ValueError(msg)
        async with self._lock:
            if str(escalation.id) in self._rows:
                msg = f"Escalation {escalation.id!r} already exists"
                logger.warning(
                    CONFLICT_ESCALATION_QUEUED,
                    escalation_id=str(escalation.id),
                    note="duplicate_id",
                )
                raise ValueError(msg)
            conflict_id = escalation.conflict.id
            for existing in self._rows.values():
                if (
                    existing.status == EscalationStatus.PENDING
                    and existing.conflict.id == conflict_id
                ):
                    msg = (
                        f"Pending escalation for conflict {conflict_id!r} "
                        "already exists"
                    )
                    logger.warning(
                        CONFLICT_ESCALATION_QUEUED,
                        escalation_id=str(escalation.id),
                        conflict_id=conflict_id,
                        conflicting_escalation_id=str(existing.id),
                        note="duplicate_pending_conflict",
                    )
                    raise ValueError(msg)
            self._rows[str(escalation.id)] = escalation
        logger.info(
            CONFLICT_ESCALATION_QUEUED,
            escalation_id=str(escalation.id),
            conflict_id=conflict_id,
            expires_at=(
                escalation.expires_at.isoformat()
                if escalation.expires_at is not None
                else None
            ),
        )

    @override
    async def get(self, escalation_id: str) -> Escalation | None:
        """Fetch by ID or return ``None``.

        Returns:
            The stored escalation, or ``None`` if absent.
        """
        async with self._lock:
            return self._rows.get(escalation_id)

    @override
    async def list_items(
        self,
        *,
        status: EscalationStatus | None = EscalationStatus.PENDING,
        limit: int = _DEFAULT_LIMIT,
        offset: int = _DEFAULT_OFFSET,
    ) -> tuple[tuple[Escalation, ...], int]:
        """Return a page of rows ordered by ``created_at`` ascending.

        Returns:
            A ``(page, total)`` pair: the page of escalations and the
            total match count.

        Raises:
            ValueError: If ``limit`` is not positive or ``offset`` is
                negative.
        """
        if limit <= 0:
            msg = "limit must be positive"
            raise ValueError(msg)
        if offset < 0:
            msg = "offset must be non-negative"
            raise ValueError(msg)
        async with self._lock:
            if status is None:
                matching = list(self._rows.values())
            else:
                matching = [r for r in self._rows.values() if r.status == status]
        matching.sort(key=lambda r: r.created_at)
        total = len(matching)
        page = tuple(matching[offset : offset + limit])
        return page, total

    @override
    async def apply_decision(
        self,
        escalation_id: str,
        *,
        decision: EscalationDecision,
        decided_by: str,
    ) -> Escalation:
        """Transition PENDING -> DECIDED with ``decision``.

        Returns:
            The updated, decided escalation.

        Raises:
            KeyError: If no escalation has ``escalation_id``.
            ValueError: If the escalation is not PENDING.
        """
        async with self._lock:
            row = self._rows.get(escalation_id)
            if row is None:
                msg = f"Escalation {escalation_id!r} not found"
                logger.warning(
                    CONFLICT_ESCALATION_RESOLVED,
                    escalation_id=escalation_id,
                    note="not_found",
                )
                raise KeyError(msg)
            if row.status != EscalationStatus.PENDING:
                msg = (
                    f"Escalation {escalation_id!r} is {row.status}, "
                    "cannot apply a decision"
                )
                logger.warning(
                    CONFLICT_ESCALATION_RESOLVED,
                    escalation_id=escalation_id,
                    current_status=row.status.value,
                    note="not_pending",
                )
                raise ValueError(msg)
            prior_status = row.status
            updated = row.model_copy(
                update={
                    "status": EscalationStatus.DECIDED,
                    "decision": decision,
                    "decided_at": datetime.now(UTC),
                    "decided_by": decided_by,
                },
            )
            self._rows[escalation_id] = updated
        logger.info(
            CONFLICT_ESCALATION_STATUS_TRANSITIONED,
            escalation_id=escalation_id,
            from_status=prior_status.value,
            to_status=EscalationStatus.DECIDED.value,
        )
        logger.info(
            CONFLICT_ESCALATION_RESOLVED,
            escalation_id=escalation_id,
            decided_by=decided_by,
        )
        return updated

    @override
    async def cancel(self, escalation_id: str, *, cancelled_by: str) -> Escalation:
        """Transition PENDING -> CANCELLED.

        Returns:
            The updated, cancelled escalation.

        Raises:
            KeyError: If no escalation has ``escalation_id``.
            ValueError: If the escalation is not PENDING.
        """
        async with self._lock:
            row = self._rows.get(escalation_id)
            if row is None:
                msg = f"Escalation {escalation_id!r} not found"
                logger.warning(
                    CONFLICT_ESCALATION_CANCELLED,
                    escalation_id=escalation_id,
                    note="not_found",
                )
                raise KeyError(msg)
            if row.status != EscalationStatus.PENDING:
                msg = f"Escalation {escalation_id!r} is {row.status}, cannot cancel"
                logger.warning(
                    CONFLICT_ESCALATION_CANCELLED,
                    escalation_id=escalation_id,
                    current_status=row.status.value,
                    note="not_pending",
                )
                raise ValueError(msg)
            prior_status = row.status
            updated = row.model_copy(
                update={
                    "status": EscalationStatus.CANCELLED,
                    "decided_at": datetime.now(UTC),
                    "decided_by": cancelled_by,
                },
            )
            self._rows[escalation_id] = updated
        logger.info(
            CONFLICT_ESCALATION_STATUS_TRANSITIONED,
            escalation_id=escalation_id,
            from_status=prior_status.value,
            to_status=EscalationStatus.CANCELLED.value,
        )
        logger.info(
            CONFLICT_ESCALATION_CANCELLED,
            escalation_id=escalation_id,
            cancelled_by=cancelled_by,
        )
        return updated

    @override
    async def mark_expired(self, now_iso: str) -> tuple[str, ...]:
        """Expire PENDING rows past their deadline.

        Tags ``decided_by`` with ``"system:expiry"`` so audit consumers
        can distinguish sweeper-driven expiry from operator actions
        (mirrors the SQLite/Postgres backends).

        Returns:
            The ids of the escalations that were expired.
        """
        # ``parse_iso_utc`` rejects naive datetimes -- ``EscalationRow.expires_at``
        # is UTC-aware, so a naive ``fromisoformat`` parse would raise
        # ``TypeError`` on the ``<=`` compare and silently break expiry sweeps.
        now_dt = parse_iso_utc(now_iso)
        expired_pairs: list[tuple[str, EscalationStatus]] = []
        async with self._lock:
            for key, row in list(self._rows.items()):
                if (
                    row.status == EscalationStatus.PENDING
                    and row.expires_at is not None
                    and row.expires_at <= now_dt
                ):
                    prior_status = row.status
                    self._rows[key] = row.model_copy(
                        update={
                            "status": EscalationStatus.EXPIRED,
                            "decided_at": now_dt,
                            "decided_by": "system:expiry",
                        },
                    )
                    expired_pairs.append((key, prior_status))
        # Per-escalation transition log so each PENDING -> EXPIRED hop
        # appears in the audit stream with from_status / to_status, not
        # just the bulk count summary below.
        for escalation_id, expired_from in expired_pairs:
            logger.info(
                CONFLICT_ESCALATION_STATUS_TRANSITIONED,
                escalation_id=escalation_id,
                from_status=expired_from.value,
                to_status=EscalationStatus.EXPIRED.value,
            )
        expired_ids = [eid for eid, _ in expired_pairs]
        if expired_ids:
            logger.info(
                CONFLICT_ESCALATION_EXPIRED,
                expired_count=len(expired_ids),
                expired_ids=expired_ids,
            )
        return tuple(expired_ids)

    @override
    async def close(self) -> None:
        """Clear the store."""
        async with self._lock:
            self._rows.clear()

    @override
    @asynccontextmanager
    async def subscribe_notifications(
        self,
        channel: str,
    ) -> AsyncIterator[AsyncIterator[str]]:
        """Return an iterator that blocks until cancelled (no-op).

        The in-memory store runs inside a single process, so there is
        no cross-process signal to wait on. The context manager yields
        an iterator that parks on an ``asyncio.Event`` that is only
        set when the caller exits the ``async with`` block.

        Note on typing: the ``@asynccontextmanager`` decorator turns
        this generator (which yields an ``AsyncIterator[str]``) into a
        callable returning an
        ``AbstractAsyncContextManager[AsyncIterator[str]]`` at the call
        site, matching :class:`EscalationQueueStore`'s protocol. The
        decorated function's own annotation stays ``AsyncIterator[...]``
        because that is what the underlying async generator produces.
        """
        stop = asyncio.Event()

        async def _never() -> AsyncIterator[str]:
            """Yield nothing until stopped (the in-memory no-notify stream)."""
            # lint-allow: long-running-loop-kill-switch -- sentinel coroutine.
            while not stop.is_set():
                await stop.wait()
                if stop.is_set():
                    return
                yield ""  # pragma: no cover - unreachable in normal flow

        try:
            yield _never()
        finally:
            stop.set()
