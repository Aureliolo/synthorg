"""Postgres-backed account lockout repository.

Tracks failed login attempts per username and enforces temporary
lockout after exceeding the threshold within a sliding window.  An
in-memory ``{username: monotonic_unlock_time}`` map backs O(1)
synchronous ``is_locked`` checks on the auth hot path.
"""

import threading
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from synthorg.api.auth.config import AuthConfig  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_AUTH_LOCKOUT_CLEANUP,
)
from synthorg.observability.events.security import (
    SECURITY_AUTH_ACCOUNT_LOCKED,
    SECURITY_AUTH_LOCKOUT_CLEARED,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool


def _import_dict_row() -> Any:
    """Lazily resolve ``psycopg.rows.dict_row``."""
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
    ) -> None:
        self._pool = pool
        self._threshold = config.lockout_threshold
        self._window = timedelta(minutes=config.lockout_window_minutes)
        self._duration = timedelta(minutes=config.lockout_duration_minutes)
        self._duration_seconds = config.lockout_duration_minutes * 60
        self._locked: dict[str, float] = {}
        self._locked_lock: threading.Lock = threading.Lock()
        self._dict_row = _import_dict_row()

    @property
    def lockout_duration_seconds(self) -> int:
        """Return the lockout duration in seconds for Retry-After."""
        return self._duration_seconds

    def is_locked(self, username: str) -> bool:
        """Sync O(1) lockout check for the auth hot path."""
        username = username.lower()
        with self._locked_lock:
            locked_until = self._locked.get(username)
            if locked_until is None:
                return False
            if time.monotonic() > locked_until:
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
        """
        dict_row = self._dict_row

        now = datetime.now(UTC)
        scan_start = now - (self._window + self._duration)
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

        mono_now = time.monotonic()
        restored = 0
        with self._locked_lock:
            for row in rows:
                uname = row["username"].lower()
                if uname not in self._locked:
                    max_at = row["max_attempted_at"]
                    locked_until = max_at + self._duration
                    remaining = (locked_until - now).total_seconds()
                    if remaining > 0:
                        self._locked[uname] = mono_now + remaining
                        restored += 1
        if restored:
            logger.info(
                SECURITY_AUTH_ACCOUNT_LOCKED,
                note="Restored lockout state from database",
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
        now = datetime.now(UTC)
        window_start = now - self._window

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
        if count >= self._threshold:
            with self._locked_lock:
                self._locked[username] = time.monotonic() + self._duration_seconds
            logger.warning(
                SECURITY_AUTH_ACCOUNT_LOCKED,
                username=username,
                attempts=count,
                threshold=self._threshold,
                duration_minutes=self._duration.total_seconds() / 60,
            )
            return True
        return False

    async def record_success(self, username: str) -> None:
        """Clear failure count on successful login."""
        username = username.lower()
        async with (
            self._pool.connection() as conn,
            conn.transaction(),
            conn.cursor() as cur,
        ):
            await cur.execute(
                "DELETE FROM login_attempts WHERE username = %s",
                (username,),
            )
        with self._locked_lock:
            was_locked = self._locked.pop(username, None) is not None
        if was_locked:
            logger.info(
                SECURITY_AUTH_LOCKOUT_CLEARED,
                username=username,
            )

    async def cleanup_expired(self) -> int:
        """Remove old attempt records outside the recovery horizon.

        Retention is ``window + duration`` so
        :meth:`load_locked`, which scans back by the same interval,
        can always rehydrate every lock that is still active at
        startup.  A shorter retention would silently un-lock users
        whose lockouts are still in effect but whose attempt rows
        were pruned.
        """
        cutoff = datetime.now(UTC) - (self._window + self._duration)
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
