"""Postgres-backed account lockout repository.

Tracks failed login attempts per username and enforces temporary
lockout after exceeding the threshold within a sliding window.  An
in-memory ``{username: monotonic_unlock_time}`` map backs O(1)
synchronous ``is_locked`` checks on the auth hot path.
"""

import asyncio
import threading
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from synthorg.core.auth.config import AuthConfig  # noqa: TC001
from synthorg.core.clock import Clock, SystemClock
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_AUTH_LOCKOUT_CLEANUP,
    API_AUTH_LOCKOUT_RESTORED,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool


def _import_dict_row() -> Any:
    """Lazily resolve ``psycopg.rows.dict_row``.

    Returns:
        Result of type ``Any``.
    """
    from psycopg.rows import dict_row  # noqa: PLC0415

    return dict_row


logger = get_logger(__name__)


class PostgresLockoutRepository:
    """Postgres-backed account lockout repository.

    Args:
        pool: An open ``psycopg_pool.AsyncConnectionPool``.
        config: Auth configuration with lockout thresholds.
    """

    def __init__(
        self,
        pool: AsyncConnectionPool,
        config: AuthConfig,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._pool = pool
        self._threshold = config.lockout_threshold
        self._window = timedelta(minutes=config.lockout_window_minutes)
        self._duration = timedelta(minutes=config.lockout_duration_minutes)
        self._duration_seconds = config.lockout_duration_minutes * 60
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._locked: dict[str, float] = {}
        # ``_locked_lock`` is held briefly for sync dict mutations and
        # by the auth hot-path ``is_locked`` reader. Holding it across
        # an async DB transaction would block every concurrent
        # ``is_locked`` (the lock is a real ``threading.Lock``, not an
        # asyncio primitive). Instead, ``_write_lock`` (asyncio)
        # serialises competing ``record_failure`` /
        # ``record_success`` callers so their DB tx + cache mutation
        # land as a single atomic unit relative to other writers,
        # while readers stay unblocked.
        self._locked_lock: threading.Lock = threading.Lock()
        self._write_lock: asyncio.Lock = asyncio.Lock()
        self._dict_row = _import_dict_row()

    @property
    def lockout_duration_seconds(self) -> int:
        """Return the lockout duration in seconds for Retry-After.

        Returns:
            Numeric result of the operation.
        """
        return self._duration_seconds

    @property
    def threshold(self) -> int:
        """Failed-attempt threshold; used by the controller's audit log.

        Returns:
            Numeric result of the operation.
        """
        return self._threshold

    def is_locked(self, username: str) -> bool:
        """Sync O(1) lockout check for the auth hot path.

        Returns:
            ``True`` when ``username`` is currently locked out, ``False`` otherwise.
        """
        username = username.lower()
        with self._locked_lock:
            locked_until = self._locked.get(username)
            if locked_until is None:
                return False
            # ``>=`` so an exact-match expiry releases the lock and
            # evicts the entry; a strict ``>`` would briefly hold a
            # user past the configured duration.
            if self._clock.monotonic() >= locked_until:
                self._locked.pop(username, None)
                return False
            return True

    async def load_locked(self) -> int:
        """Restore in-memory lockout state from recent failure records.

        Scans attempts within ``window + duration`` so locks triggered
        just before the window rolled forward (e.g. when
        ``lockout_duration_minutes`` > ``lockout_window_minutes``) are
        not silently dropped.  A user is restored only when (1) at
        least ``threshold`` failures fell inside the window ending at
        their most-recent attempt, and (2) ``max_attempted_at +
        duration`` is still in the future.

        Returns:
            Number of usernames restored to the in-memory lockout cache.
        """
        dict_row = self._dict_row

        scan_now = self._clock.now()
        scan_start = scan_now - (self._window + self._duration)
        async with (
            self._pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            # Per-user count limited to the window ending at each
            # user's most-recent attempt, so extending the scan range
            # for recovery does not inflate the threshold check.  The
            # CTE decorates each row with that user's latest attempt
            # so the outer GROUP BY can filter down to the correct
            # window before counting.
            await cur.execute(
                "WITH user_attempts AS ("
                "  SELECT username, attempted_at, "
                "         MAX(attempted_at) OVER ("
                "           PARTITION BY username"
                "         ) AS latest "
                "  FROM login_attempts "
                "  WHERE attempted_at >= %s"
                ") "
                "SELECT username, "
                "       MAX(attempted_at) AS max_attempted_at, "
                "       COUNT(*) AS cnt "
                "FROM user_attempts "
                "WHERE attempted_at >= latest - %s "
                "GROUP BY username "
                "HAVING COUNT(*) >= %s",
                (scan_start, self._window, self._threshold),
            )
            rows = await cur.fetchall()

        # Resample wall-clock AFTER the DB read so query latency does
        # not inflate restored lockout durations. ``mono_now`` and
        # ``restore_now`` are sampled together as the post-query
        # anchor; computing ``remaining`` from the pre-query
        # ``scan_now`` would extend every restored lock by the
        # observed query duration.
        restore_now = self._clock.now()
        mono_now = self._clock.monotonic()
        restored = 0
        with self._locked_lock:
            for row in rows:
                uname = row["username"].lower()
                if uname not in self._locked:
                    max_at = row["max_attempted_at"]
                    locked_until = max_at + self._duration
                    remaining = (locked_until - restore_now).total_seconds()
                    if remaining > 0:
                        self._locked[uname] = mono_now + remaining
                        restored += 1
        if restored:
            # Cache rehydration only -- no NEW lockout decision is
            # being made here. Emitting SECURITY_AUTH_ACCOUNT_LOCKED
            # would chain a duplicate decision into the audit log on
            # every restart for every still-locked account.
            logger.info(
                API_AUTH_LOCKOUT_RESTORED,
                restored=restored,
            )
        return restored

    async def record_failure(
        self,
        username: str,
        ip_address: str = "",
    ) -> bool:
        """Record a failed login attempt.  Return ``True`` if now locked.

        Returns:
            ``True`` when this failure pushed the username past the lockout threshold,
            ``False`` otherwise.
        """
        username = username.lower()
        now = self._clock.now()
        window_start = now - self._window

        # Hold ``_write_lock`` across the entire DB tx + cache
        # mutation so a concurrent ``record_success`` cannot
        # interleave: without this serialisation the two methods
        # could commit in DB-order T1->T2 but mutate the cache in
        # opposite order, leaving ``_locked`` claiming a lockout the
        # DB already cleared.
        async with self._write_lock:
            async with (
                self._pool.connection() as conn,
                conn.transaction(),
                conn.cursor() as cur,
            ):
                await cur.execute(
                    "INSERT INTO login_attempts "
                    "(username, attempted_at, ip_address) "
                    "VALUES (%s, %s, %s)",
                    (username, now, ip_address),
                )
                await cur.execute(
                    "SELECT COUNT(*) FROM login_attempts "
                    "WHERE username = %s AND attempted_at >= %s",
                    (username, window_start),
                )
                row = await cur.fetchone()
                count = row[0] if row else 0
                now_locked = count >= self._threshold
            # ``conn.transaction()`` commits on successful exit.
            # Mutate the cache only AFTER the inner async-with
            # releases without raising (commit succeeded) but BEFORE
            # releasing ``_write_lock``, so no sibling writer can
            # sneak in between commit and cache write.
            # ``_locked_lock`` is taken briefly for the dict op so
            # the auth hot-path ``is_locked`` reader is not blocked
            # by the surrounding async tx.
            if now_locked:
                with self._locked_lock:
                    self._locked[username] = (
                        self._clock.monotonic() + self._duration_seconds
                    )
        # Caller logs SECURITY_AUTH_ACCOUNT_LOCKED with the contextual
        # fields (attempts, threshold, duration); persistence does not
        # emit decision events (repositories never emit operational
        # events; that lives in the service layer).
        return now_locked

    async def record_success(self, username: str) -> bool:
        """Clear failure count on successful login.

        Returns ``True`` if a previously-locked account was unlocked
        (caller logs ``SECURITY_AUTH_LOCKOUT_CLEARED``); ``False``
        when no lockout was in effect.

        Returns:
            ``True`` when an existing failure record was cleared, ``False`` when there
            was nothing to clear.
        """
        username = username.lower()
        # Same write-lock serialisation as ``record_failure``: cache
        # mutation must follow the DB commit AND no concurrent
        # ``record_failure`` can interleave between our commit and
        # our cache pop.
        async with self._write_lock:
            async with (
                self._pool.connection() as conn,
                conn.transaction(),
                conn.cursor() as cur,
            ):
                await cur.execute(
                    "DELETE FROM login_attempts WHERE username = %s",
                    (username,),
                )
            # Cache pop only after the inner async-with commits;
            # commit failure leaves ``_locked`` intact (the user is
            # still locked per durable state until the next attempt
            # actually commits the delete).
            with self._locked_lock:
                return self._locked.pop(username, None) is not None

    async def cleanup_expired(self) -> int:
        """Remove old attempt records outside the recovery horizon.

        Retention is ``window + duration`` so
        :meth:`load_locked`, which scans back by the same interval,
        can always rehydrate every lock that is still active at
        startup.  A shorter retention would silently un-lock users
        whose lockouts are still in effect but whose attempt rows
        were pruned.

        Returns:
            Numeric result of the operation.
        """
        cutoff = self._clock.now() - (self._window + self._duration)
        async with (
            self._pool.connection() as conn,
            conn.transaction(),
            conn.cursor() as cur,
        ):
            await cur.execute(
                "DELETE FROM login_attempts WHERE attempted_at < %s",
                (cutoff,),
            )
            count = cur.rowcount
        if count:
            logger.debug(
                API_AUTH_LOCKOUT_CLEANUP,
                removed=count,
            )
        return count
