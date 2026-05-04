"""SQLite-backed account lockout repository.

Tracks failed login attempts per username and enforces temporary
lockout after exceeding the threshold within a sliding window.  An
in-memory ``{username: monotonic_unlock_time}`` map backs O(1)
synchronous ``is_locked`` checks on the auth hot path.

Single-instance deployment assumption: the cache is process-local,
so horizontally-scaled deployments would see per-node drift.
Multi-instance deployments require a shared lock store.
"""

import asyncio
import threading
from datetime import datetime, timedelta

import aiosqlite  # noqa: TC002

from synthorg.core.auth.config import AuthConfig  # noqa: TC001
from synthorg.core.clock import Clock, SystemClock
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_AUTH_LOCKOUT_CLEANUP,
    API_AUTH_LOCKOUT_RESTORED,
)
from synthorg.persistence._shared import format_iso_utc, parse_iso_utc

logger = get_logger(__name__)


class SQLiteLockoutRepository:
    """SQLite-backed account lockout repository.

    Args:
        db: Open aiosqlite connection with ``row_factory`` set.
        config: Auth configuration with lockout thresholds.
        write_lock: Optional shared ``asyncio.Lock`` serializing
            multi-statement write transactions against the backend's
            single aiosqlite connection.  The backend owns this lock
            and passes it to every repository that emits
            ``BEGIN IMMEDIATE``; callers that construct the repo
            directly (e.g. one-off tests) can omit it to get a
            per-repo lock.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        config: AuthConfig,
        *,
        write_lock: asyncio.Lock | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._db = db
        self._threshold = config.lockout_threshold
        self._window = timedelta(minutes=config.lockout_window_minutes)
        self._duration = timedelta(minutes=config.lockout_duration_minutes)
        self._duration_seconds = config.lockout_duration_minutes * 60
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._locked: dict[str, float] = {}
        self._locked_lock: threading.Lock = threading.Lock()
        self._write_lock = write_lock if write_lock is not None else asyncio.Lock()

    @property
    def lockout_duration_seconds(self) -> int:
        """Return the lockout duration in seconds for Retry-After."""
        return self._duration_seconds

    @property
    def threshold(self) -> int:
        """Failed-attempt threshold; used by the controller's audit log."""
        return self._threshold

    def is_locked(self, username: str) -> bool:
        """Sync O(1) lockout check for the auth hot path."""
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
        not silently dropped.  Counts are taken over the window
        ending at each user's most-recent attempt, so extending the
        scan range does not inflate the threshold check.
        """
        scan_now = self._clock.now()
        scan_start = format_iso_utc(scan_now - (self._window + self._duration))
        cursor = await self._db.execute(
            "SELECT username, attempted_at FROM login_attempts "
            "WHERE attempted_at >= ? "
            "ORDER BY username ASC, attempted_at DESC",
            (scan_start,),
        )
        rows = await cursor.fetchall()
        per_user: dict[str, list[datetime]] = {}
        for row in rows:
            uname = row["username"].lower()
            per_user.setdefault(uname, []).append(
                parse_iso_utc(row["attempted_at"]),
            )

        # Resample wall-clock AFTER the DB read so query latency does
        # not inflate the remaining lockout duration. ``mono_now`` and
        # ``restore_now`` are sampled together as the post-query
        # anchor; computing ``remaining`` from the pre-query
        # ``scan_now`` would extend every restored lock by the
        # observed query duration.
        restore_now = self._clock.now()
        mono_now = self._clock.monotonic()
        restored = 0
        with self._locked_lock:
            for uname, attempts in per_user.items():
                if uname in self._locked or not attempts:
                    continue
                max_at = attempts[0]  # sorted DESC
                window_floor = max_at - self._window
                cnt_in_window = sum(1 for a in attempts if a >= window_floor)
                if cnt_in_window < self._threshold:
                    continue
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
        """Record a failed login attempt.  Return ``True`` if now locked."""
        username = username.lower()
        now = self._clock.now()
        window_start = format_iso_utc(now - self._window)
        async with self._write_lock:
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                await self._db.execute(
                    "INSERT INTO login_attempts "
                    "(username, attempted_at, ip_address) "
                    "VALUES (?, ?, ?)",
                    (username, format_iso_utc(now), ip_address),
                )
                cursor = await self._db.execute(
                    "SELECT COUNT(*) AS cnt FROM login_attempts "
                    "WHERE username = ? AND attempted_at >= ?",
                    (username, window_start),
                )
                row = await cursor.fetchone()
                count = row["cnt"] if row else 0
                now_locked = count >= self._threshold
                await self._db.commit()
            except MemoryError, RecursionError:
                raise
            except Exception:
                await self._db.rollback()
                raise
            # Cache mutation MUST happen while still holding
            # ``_write_lock`` so a concurrent ``record_success``
            # cannot interleave between our commit and our cache
            # write -- otherwise two writers can commit DB-order
            # T1->T2 but mutate the cache T2->T1, leaving
            # ``_locked`` inconsistent with durable state.
            # ``_locked_lock`` is taken briefly for the dict op so
            # the auth hot-path ``is_locked`` reader is not blocked
            # by the surrounding async tx.
            if now_locked:
                with self._locked_lock:
                    self._locked[username] = (
                        self._clock.monotonic() + self._duration_seconds
                    )
        # Caller logs SECURITY_AUTH_ACCOUNT_LOCKED with the contextual
        # fields (attempts, threshold, duration) the controller
        # already tracks; persistence does not emit decision events
        # (#1599 persistence-boundary rule).
        return now_locked

    async def record_success(self, username: str) -> bool:
        """Clear failure count on successful login.

        Returns ``True`` if a previously-locked account was unlocked
        (caller logs ``SECURITY_AUTH_LOCKOUT_CLEARED``); ``False``
        when there was no prior lockout (no audit emission warranted).
        """
        username = username.lower()
        async with self._write_lock:
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                await self._db.execute(
                    "DELETE FROM login_attempts WHERE username = ?",
                    (username,),
                )
                await self._db.commit()
            except MemoryError, RecursionError:
                raise
            except Exception:
                await self._db.rollback()
                raise
            # Cache pop while still holding ``_write_lock`` so a
            # concurrent ``record_failure`` cannot insert + flip
            # ``_locked`` between our commit and our pop.
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
        """
        retention = self._window + self._duration
        cutoff = format_iso_utc(self._clock.now() - retention)
        async with self._write_lock:
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self._db.execute(
                    "DELETE FROM login_attempts WHERE attempted_at < ?",
                    (cutoff,),
                )
                count = cursor.rowcount
                await self._db.commit()
            except MemoryError, RecursionError:
                raise
            except Exception:
                await self._db.rollback()
                raise
        if count:
            logger.debug(
                API_AUTH_LOCKOUT_CLEANUP,
                removed=count,
            )
        return count
