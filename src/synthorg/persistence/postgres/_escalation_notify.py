"""Postgres LISTEN/NOTIFY plumbing for the escalation queue.

Extracted from ``escalation_repo`` so the repository module stays under
its tier cap. These helpers operate on a shared
``psycopg_pool.AsyncConnectionPool``: :func:`subscribe` holds a dedicated
connection for the lifetime of a LISTEN subscription, and
:func:`publish_notifies` emits one ``pg_notify`` per id over a single
checkout.
"""

import contextlib
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

import psycopg
from psycopg_pool import AsyncConnectionPool

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.escalation import (
    PERSISTENCE_ESCALATION_NOTIFY_FAILED,
    PERSISTENCE_ESCALATION_SUBSCRIBE_FAILED,
)

logger = get_logger(__name__)

# Postgres unquoted identifier regex (defence-in-depth for LISTEN /
# UNLISTEN arg interpolation in :func:`subscribe`).
_SAFE_IDENTIFIER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*$",
)
_MAX_IDENTIFIER_LEN: Final[int] = 63


@asynccontextmanager
async def subscribe(
    pool: AsyncConnectionPool,
    channel: str,
) -> AsyncIterator[AsyncIterator[str]]:
    """Subscribe to Postgres LISTEN/NOTIFY on *channel*.

    Holds a dedicated pool connection for the lifetime of the
    subscription (LISTEN is session-level state). Operators enabling
    cross-instance notify MUST size ``pool_min_size`` to reserve at
    least one slot per API worker so LISTEN does not starve other
    borrowers.

    Raises:
        ValueError: If *channel* is not a safe Postgres unquoted
            identifier. Defence-in-depth -- config + caller already
            validate, but this re-checks so a stray caller cannot inject
            SQL via ``LISTEN "<channel>"``.
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

    async with pool.connection() as conn:
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
                """Yield notification payloads from the LISTEN iterator."""
                async for notify in notifies_gen:
                    yield notify.payload

            try:
                yield _payloads()
            finally:
                await notifies_gen.aclose()
        finally:
            try:
                await conn.execute(f'UNLISTEN "{channel}"')
            except psycopg.Error as exc:
                session_tainted = True
                logger.warning(
                    PERSISTENCE_ESCALATION_SUBSCRIBE_FAILED,
                    error_type="escalation_unlisten_failed",
                    error=safe_error_description(exc),
                    channel=channel,
                )
            try:
                await conn.set_autocommit(bool(original_autocommit))
            except psycopg.Error as exc:
                session_tainted = True
                logger.warning(
                    PERSISTENCE_ESCALATION_SUBSCRIBE_FAILED,
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


async def publish_notifies(
    pool: AsyncConnectionPool,
    channel: str | None,
    escalation_ids: tuple[str, ...],
    status: str,
) -> None:
    """Publish one ``<id>:<status>`` NOTIFY per id over a single checkout.

    Avoids the N+1 connection churn of a per-id loop (e.g. the sweeper's
    expire-overdue path). Each id still emits its own ``pg_notify`` call
    so subscribers continue to see one ``"<id>:<status>"`` payload per
    transition -- only the connection-pool overhead collapses.

    Best-effort: any psycopg failure is logged and swallowed because the
    persistent state has already been committed and the sweeper can
    still reap stale rows even if the signal is missed. A ``None``
    channel (single-worker default) disables publication.
    """
    if channel is None or not status or not escalation_ids:
        return
    # Filter empty ids defensively; ``pg_notify`` would otherwise accept
    # them and produce useless payloads on the wire.
    valid_ids = tuple(eid for eid in escalation_ids if eid)
    if not valid_ids:
        return
    try:
        async with pool.connection() as conn, conn.cursor() as cur:
            for escalation_id in valid_ids:
                payload = f"{escalation_id}:{status}"
                await cur.execute(
                    "SELECT pg_notify(%s, %s)",
                    (channel, payload),
                )
            await conn.commit()
    except psycopg.Error as exc:
        # Bound the logged ids: a sweep covering thousands of escalations
        # would otherwise emit megabyte-scale warning records.
        logger.warning(
            PERSISTENCE_ESCALATION_NOTIFY_FAILED,
            error_type="escalation_notify_failed",
            escalation_id_count=len(valid_ids),
            escalation_ids_sample=valid_ids[:10],
            channel=channel,
            error=safe_error_description(exc),
        )


__all__ = ["publish_notifies", "subscribe"]
