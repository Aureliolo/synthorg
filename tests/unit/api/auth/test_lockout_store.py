"""Tests for the account lockout store."""

import time
from unittest.mock import patch

import aiosqlite
import pytest

from synthorg.core.auth.config import AuthConfig
from synthorg.persistence.sqlite.lockout_repo import (
    SQLiteLockoutRepository as LockoutStore,
)

pytestmark = pytest.mark.unit


def _make_config(
    *,
    threshold: int = 3,
    window_minutes: int = 15,
    duration_minutes: int = 10,
) -> AuthConfig:
    return AuthConfig(
        lockout_threshold=threshold,
        lockout_window_minutes=window_minutes,
        lockout_duration_minutes=duration_minutes,
    )


@pytest.fixture
def db(migrated_db: aiosqlite.Connection) -> aiosqlite.Connection:
    """Alias for the shared migrated_db fixture."""
    return migrated_db


@pytest.fixture
async def store(db: aiosqlite.Connection) -> LockoutStore:
    return LockoutStore(db, _make_config())


class TestLockoutIsLocked:
    def test_not_locked_by_default(self, store: LockoutStore) -> None:
        assert store.is_locked("alice") is False

    def test_locked_after_threshold(self, store: LockoutStore) -> None:
        # Manually set lock
        store._locked["alice"] = time.monotonic() + 600
        assert store.is_locked("alice") is True

    def test_auto_unlocks_after_duration(self, store: LockoutStore) -> None:
        # Set lock in the past
        store._locked["alice"] = time.monotonic() - 1
        assert store.is_locked("alice") is False
        # Entry should be cleaned up
        assert "alice" not in store._locked


class TestLockoutRecordFailure:
    async def test_below_threshold_not_locked(self, store: LockoutStore) -> None:
        locked = await store.record_failure("alice")
        assert locked is False
        locked = await store.record_failure("alice")
        assert locked is False
        assert store.is_locked("alice") is False

    async def test_at_threshold_locks(self, store: LockoutStore) -> None:
        for _ in range(2):
            await store.record_failure("alice")
        locked = await store.record_failure("alice")
        assert locked is True
        assert store.is_locked("alice") is True

    async def test_different_users_independent(self, store: LockoutStore) -> None:
        for _ in range(3):
            await store.record_failure("alice")
        assert store.is_locked("alice") is True
        assert store.is_locked("bob") is False


class TestLockoutRecordSuccess:
    async def test_clears_attempts(self, store: LockoutStore) -> None:
        for _ in range(2):
            await store.record_failure("alice")

        await store.record_success("alice")

        # Should need full threshold again
        locked = await store.record_failure("alice")
        assert locked is False

    async def test_clears_lock(self, store: LockoutStore) -> None:
        for _ in range(3):
            await store.record_failure("alice")
        assert store.is_locked("alice") is True

        await store.record_success("alice")
        assert store.is_locked("alice") is False


class TestLockoutCleanup:
    async def test_removes_old_records(
        self,
        db: aiosqlite.Connection,
    ) -> None:
        config = _make_config(window_minutes=1)
        store = LockoutStore(db, config)

        # Insert an old record directly
        await db.execute(
            "INSERT INTO login_attempts "
            "(username, attempted_at, ip_address) "
            "VALUES (?, ?, ?)",
            ("alice", "2020-01-01T00:00:00+00:00", "127.0.0.1"),
        )
        await db.commit()

        removed = await store.cleanup_expired()
        assert removed == 1


class TestLockoutDurationSeconds:
    async def test_returns_correct_duration(self, db: aiosqlite.Connection) -> None:
        config = _make_config(duration_minutes=15)
        store_obj = LockoutStore(db, config)
        assert store_obj.lockout_duration_seconds == 900


class TestLockoutWithMockedTime:
    async def test_lock_expires_after_duration(
        self,
        db: aiosqlite.Connection,
    ) -> None:
        """Lock expires when monotonic time passes the duration."""
        config = _make_config(threshold=2, duration_minutes=5)
        store = LockoutStore(db, config)

        # Trigger lockout
        await store.record_failure("alice")
        await store.record_failure("alice")
        assert store.is_locked("alice") is True

        # Fast-forward past the lock duration
        with patch.object(
            time,
            "monotonic",
            return_value=time.monotonic() + 301,
        ):
            assert store.is_locked("alice") is False
