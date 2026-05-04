"""Tests for the refresh token store."""

from datetime import UTC, datetime, timedelta

import aiosqlite
import pytest

from synthorg.core.auth.refresh_record import RefreshRejectReason
from synthorg.persistence.sqlite.refresh_repo import (
    SQLiteRefreshTokenRepository as RefreshStore,
)

pytestmark = pytest.mark.unit

_NOW = datetime.now(UTC)
_PAST = _NOW - timedelta(days=1)
_FUTURE = _NOW + timedelta(days=7)


@pytest.fixture
def db(migrated_db: aiosqlite.Connection) -> aiosqlite.Connection:
    """Alias for the shared migrated_db fixture."""
    return migrated_db


@pytest.fixture
async def store(db: aiosqlite.Connection) -> RefreshStore:
    return RefreshStore(db)


class TestRefreshCreate:
    async def test_create_stores_token(self, store: RefreshStore) -> None:
        await store.create(
            token_hash="hash-1",
            session_id="sess-1",
            user_id="user-1",
            expires_at=_FUTURE,
        )
        # Verify via consume
        outcome = await store.consume("hash-1")
        assert outcome.record is not None
        assert outcome.record.session_id == "sess-1"
        assert outcome.record.user_id == "user-1"
        assert outcome.record.used is True


class TestRefreshConsume:
    async def test_consume_marks_used(self, store: RefreshStore) -> None:
        await store.create(
            token_hash="hash-c1",
            session_id="sess-1",
            user_id="user-1",
            expires_at=_FUTURE,
        )
        outcome = await store.consume("hash-c1")
        assert outcome.record is not None
        assert outcome.record.used is True

    async def test_consume_single_use(self, store: RefreshStore) -> None:
        """Second consume of the same token rejects with REPLAY_DETECTED."""
        await store.create(
            token_hash="hash-c2",
            session_id="sess-1",
            user_id="user-1",
            expires_at=_FUTURE,
        )
        first = await store.consume("hash-c2")
        assert first.record is not None
        second = await store.consume("hash-c2")
        assert second.record is None
        assert second.reject_reason is RefreshRejectReason.REPLAY_DETECTED

    async def test_consume_nonexistent_rejects_not_found(
        self,
        store: RefreshStore,
    ) -> None:
        outcome = await store.consume("nonexistent-hash")
        assert outcome.record is None
        assert outcome.reject_reason is RefreshRejectReason.NOT_FOUND_OR_EXPIRED

    async def test_consume_expired_rejects_not_found(
        self,
        store: RefreshStore,
    ) -> None:
        await store.create(
            token_hash="hash-expired",
            session_id="sess-1",
            user_id="user-1",
            expires_at=_PAST,
        )
        outcome = await store.consume("hash-expired")
        assert outcome.record is None
        assert outcome.reject_reason is RefreshRejectReason.NOT_FOUND_OR_EXPIRED

    async def test_consume_rejects_revoked_session(self, store: RefreshStore) -> None:
        """Token belonging to a revoked session reports session_revoked."""
        await store.create(
            token_hash="hash-revoked-sess",
            session_id="revoked-sess",
            user_id="user-1",
            expires_at=_FUTURE,
        )
        outcome = await store.consume(
            "hash-revoked-sess",
            is_session_revoked=lambda sid: sid == "revoked-sess",
        )
        assert outcome.record is None
        assert outcome.reject_reason is RefreshRejectReason.SESSION_REVOKED

    async def test_consume_allows_non_revoked_session(
        self, store: RefreshStore
    ) -> None:
        """Token with a valid session passes the revocation check."""
        await store.create(
            token_hash="hash-valid-sess",
            session_id="valid-sess",
            user_id="user-1",
            expires_at=_FUTURE,
        )
        outcome = await store.consume(
            "hash-valid-sess",
            is_session_revoked=lambda sid: False,
        )
        assert outcome.record is not None
        assert outcome.record.session_id == "valid-sess"


class TestRefreshRevoke:
    async def test_revoke_by_session(self, store: RefreshStore) -> None:
        await store.create(
            token_hash="h1",
            session_id="sess-1",
            user_id="user-1",
            expires_at=_FUTURE,
        )
        await store.create(
            token_hash="h2",
            session_id="sess-1",
            user_id="user-1",
            expires_at=_FUTURE,
        )
        await store.create(
            token_hash="h3",
            session_id="sess-2",
            user_id="user-1",
            expires_at=_FUTURE,
        )

        revoked = await store.revoke_by_session("sess-1")
        assert revoked == 2

        # h1 and h2 should be unusable -- revoke flags ``used = TRUE``
        # so consume sees a replay.
        for ref in ("h1", "h2"):
            outcome = await store.consume(ref)
            assert outcome.record is None
            assert outcome.reject_reason is RefreshRejectReason.REPLAY_DETECTED
        # h3 should still work
        h3_outcome = await store.consume("h3")
        assert h3_outcome.record is not None

    async def test_revoke_by_user(self, store: RefreshStore) -> None:
        await store.create(
            token_hash="u1-h1",
            session_id="sess-1",
            user_id="user-1",
            expires_at=_FUTURE,
        )
        await store.create(
            token_hash="u2-h1",
            session_id="sess-2",
            user_id="user-2",
            expires_at=_FUTURE,
        )

        revoked = await store.revoke_by_user("user-1")
        assert revoked == 1
        revoked_outcome = await store.consume("u1-h1")
        assert revoked_outcome.record is None
        assert revoked_outcome.reject_reason is RefreshRejectReason.REPLAY_DETECTED
        active_outcome = await store.consume("u2-h1")
        assert active_outcome.record is not None


class TestRefreshCleanup:
    async def test_cleanup_removes_only_expired(
        self,
        db: aiosqlite.Connection,
    ) -> None:
        store = RefreshStore(db)
        # Expired token -- will be removed
        await store.create(
            token_hash="expired",
            session_id="s1",
            user_id="u1",
            expires_at=_PAST,
        )
        # Used but not expired -- retained for replay detection
        await store.create(
            token_hash="used",
            session_id="s2",
            user_id="u1",
            expires_at=_FUTURE,
        )
        await store.consume("used")
        # Active token
        await store.create(
            token_hash="active",
            session_id="s3",
            user_id="u1",
            expires_at=_FUTURE,
        )

        removed = await store.cleanup_expired()
        assert removed == 1  # only the expired row
        # Used token still in DB (replay detection works)
        used_outcome = await store.consume("used")
        assert used_outcome.record is None
        assert used_outcome.reject_reason is RefreshRejectReason.REPLAY_DETECTED
        # Active token still consumable
        active_outcome = await store.consume("active")
        assert active_outcome.record is not None
