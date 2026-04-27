"""Conformance tests for auth repositories (session / refresh / lockout).

Issue #1457 consolidates the three auth stores into the persistence
boundary, and the matching unit tests only exercise SQLite -- the
previous ``test_postgres_session_store.py`` and siblings relied on
mocks for the Postgres side, so a divergence between SQLite and
Postgres behaviour would not have been caught.  These conformance
tests drive each repository through the shared :class:`PersistenceBackend`
fixture, so every assertion runs twice (once against SQLite, once
against a real Postgres container).
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.api.auth.config import AuthConfig
from synthorg.api.auth.models import User
from synthorg.api.auth.refresh_record import RefreshRejectReason
from synthorg.api.auth.session import Session
from synthorg.api.guards import HumanRole
from synthorg.core.types import NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _now() -> datetime:
    # Use real wall-clock time: the repositories compare against
    # ``datetime.now(UTC)`` internally for expiry and "active session"
    # filters, so a fixed historical timestamp makes newly-created
    # sessions / tokens look already-expired.
    return datetime.now(UTC)


async def _ensure_user(
    backend: PersistenceBackend,
    user_id: str,
    username: str = "alice",
) -> None:
    """Create a User row -- sessions/refresh tokens need a parent user."""
    now = _now()
    await backend.users.save(
        User(
            id=NotBlankStr(user_id),
            username=NotBlankStr(username),
            password_hash=NotBlankStr(
                "$argon2id$v=19$m=65536,t=3,p=4$cGVwcGVy$abcd1234"
            ),
            role=HumanRole.MANAGER,
            must_change_password=False,
            org_roles=(),
            scoped_departments=(),
            created_at=now,
            updated_at=now,
        ),
    )


async def _make_session(  # noqa: PLR0913 -- scenario builder wants full control
    backend: PersistenceBackend,
    session_id: str = "sess_1",
    user_id: str = "user_alice",
    username: str = "alice",
    expires_delta: timedelta = timedelta(hours=1),
    *,
    revoked: bool = False,
) -> Session:
    """Build a Session and make sure its owning user exists."""
    await _ensure_user(backend, user_id, username)
    now = _now()
    return Session(
        session_id=NotBlankStr(session_id),
        user_id=NotBlankStr(user_id),
        username=NotBlankStr(username),
        role=HumanRole.MANAGER,
        ip_address="127.0.0.1",
        user_agent="pytest",
        created_at=now,
        last_active_at=now,
        expires_at=now + expires_delta,
        revoked=revoked,
    )


# ── SessionRepository ───────────────────────────────────────────


class TestSessionRepository:
    async def test_create_and_get(self, backend: PersistenceBackend) -> None:
        session = await _make_session(backend)
        await backend.sessions.create(session)
        fetched = await backend.sessions.get("sess_1")
        assert fetched is not None
        assert fetched.session_id == "sess_1"
        assert fetched.user_id == "user_alice"
        assert fetched.revoked is False

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.sessions.get("missing") is None

    async def test_list_by_user(self, backend: PersistenceBackend) -> None:
        await backend.sessions.create(
            await _make_session(backend, "sess_a", "user_bob", "bob"),
        )
        await backend.sessions.create(
            await _make_session(backend, "sess_b", "user_bob", "bob"),
        )
        await backend.sessions.create(
            await _make_session(backend, "sess_c", "user_eve", "eve"),
        )
        rows = await backend.sessions.list_by_user("user_bob")
        assert {s.session_id for s in rows} == {"sess_a", "sess_b"}

    async def test_list_all_active(self, backend: PersistenceBackend) -> None:
        await backend.sessions.create(await _make_session(backend, "sess_x"))
        await backend.sessions.create(
            await _make_session(backend, "sess_y", "user_b", "bea"),
        )
        rows = await backend.sessions.list_all()
        assert {s.session_id for s in rows} >= {"sess_x", "sess_y"}

    async def test_list_all_pagination(self, backend: PersistenceBackend) -> None:
        # Sessions are ordered by created_at DESC; build them with
        # forced timestamps so the page contents are deterministic
        # under both backends.
        now = datetime.now(UTC)
        for i in range(4):
            sess = await _make_session(backend, f"sess_pg_{i}")
            sess = sess.model_copy(update={"created_at": now - timedelta(seconds=i)})
            await backend.sessions.create(sess)

        # newest-first: sess_pg_0, sess_pg_1, sess_pg_2, sess_pg_3
        page = await backend.sessions.list_all(limit=2, offset=1)

        assert [s.session_id for s in page] == ["sess_pg_1", "sess_pg_2"]

    async def test_list_by_user_pagination(self, backend: PersistenceBackend) -> None:
        now = datetime.now(UTC)
        for i in range(3):
            sess = await _make_session(
                backend,
                f"sess_user_{i}",
                "user_carol",
                "carol",
            )
            sess = sess.model_copy(update={"created_at": now - timedelta(seconds=i)})
            await backend.sessions.create(sess)

        page = await backend.sessions.list_by_user(
            "user_carol",
            limit=2,
            offset=0,
        )

        assert [s.session_id for s in page] == ["sess_user_0", "sess_user_1"]

    async def test_revoke_marks_session(self, backend: PersistenceBackend) -> None:
        await backend.sessions.create(await _make_session(backend, "sess_r"))
        assert backend.sessions.is_revoked("sess_r") is False
        assert await backend.sessions.revoke("sess_r") is True
        assert backend.sessions.is_revoked("sess_r") is True

    async def test_revoke_missing_returns_false(
        self, backend: PersistenceBackend
    ) -> None:
        assert await backend.sessions.revoke("never_existed") is False

    async def test_revoke_all_for_user(self, backend: PersistenceBackend) -> None:
        await backend.sessions.create(
            await _make_session(backend, "sess_u1", "user_mass", "mallory"),
        )
        await backend.sessions.create(
            await _make_session(backend, "sess_u2", "user_mass", "mallory"),
        )
        await backend.sessions.create(
            await _make_session(backend, "sess_keep", "user_other", "oscar"),
        )
        count = await backend.sessions.revoke_all_for_user("user_mass")
        assert count == 2
        assert backend.sessions.is_revoked("sess_u1")
        assert backend.sessions.is_revoked("sess_u2")
        assert not backend.sessions.is_revoked("sess_keep")

    async def test_load_revoked_rehydrates_cache(
        self, backend: PersistenceBackend
    ) -> None:
        # Persist a revoked session, clear in-memory cache, reload.
        await backend.sessions.create(
            await _make_session(backend, "sess_rv", revoked=True),
        )
        backend.sessions._revoked.clear()
        await backend.sessions.load_revoked()
        assert backend.sessions.is_revoked("sess_rv") is True

    async def test_enforce_session_limit_trims_oldest(
        self, backend: PersistenceBackend
    ) -> None:
        await _ensure_user(backend, "user_limit", "limus")
        for idx in range(4):
            session = Session(
                session_id=NotBlankStr(f"sess_l{idx}"),
                user_id=NotBlankStr("user_limit"),
                username=NotBlankStr("limus"),
                role=HumanRole.MANAGER,
                ip_address="127.0.0.1",
                user_agent="pytest",
                created_at=_now() + timedelta(minutes=idx),
                last_active_at=_now() + timedelta(minutes=idx),
                expires_at=_now() + timedelta(hours=1),
            )
            await backend.sessions.create(session)
        revoked = await backend.sessions.enforce_session_limit(
            "user_limit", max_sessions=2
        )
        assert revoked >= 2
        remaining = await backend.sessions.list_by_user("user_limit")
        assert len(remaining) <= 2

    async def test_cleanup_expired_removes_old(
        self, backend: PersistenceBackend
    ) -> None:
        await _ensure_user(backend, "user_alice", "alice")
        past = _now() - timedelta(days=2)
        expired = Session(
            session_id=NotBlankStr("sess_exp"),
            user_id=NotBlankStr("user_alice"),
            username=NotBlankStr("alice"),
            role=HumanRole.MANAGER,
            ip_address="127.0.0.1",
            user_agent="pytest",
            created_at=past,
            last_active_at=past,
            expires_at=past + timedelta(minutes=5),
        )
        await backend.sessions.create(expired)
        count = await backend.sessions.cleanup_expired()
        assert count >= 1
        assert await backend.sessions.get("sess_exp") is None


# ── RefreshTokenRepository ──────────────────────────────────────


async def _seed_session(
    backend: PersistenceBackend,
    session_id: str,
    user_id: str,
    username: str,
) -> None:
    """Insert the parent session row refresh tokens FK-reference."""
    session = await _make_session(backend, session_id, user_id, username)
    await backend.sessions.create(session)


class TestRefreshTokenRepository:
    async def test_create_and_consume_single_use(
        self, backend: PersistenceBackend
    ) -> None:
        await _seed_session(backend, "sess_rt1", "user_rt", "rita")
        await backend.refresh_tokens.create(
            token_hash="t_hash_1",
            session_id="sess_rt1",
            user_id="user_rt",
            expires_at=_now() + timedelta(hours=1),
        )
        first = await backend.refresh_tokens.consume("t_hash_1")
        assert first.record is not None
        assert first.reject_reason is None
        assert first.record.session_id == "sess_rt1"
        # Second consume must reject as replay -- single-use rotation.
        second = await backend.refresh_tokens.consume("t_hash_1")
        assert second.record is None
        assert second.reject_reason is RefreshRejectReason.REPLAY_DETECTED

    async def test_consume_missing_returns_not_found(
        self, backend: PersistenceBackend
    ) -> None:
        outcome = await backend.refresh_tokens.consume("nope")
        assert outcome.record is None
        assert outcome.reject_reason is RefreshRejectReason.NOT_FOUND_OR_EXPIRED

    async def test_consume_expired_returns_not_found(
        self, backend: PersistenceBackend
    ) -> None:
        # Can't build a Session with expires_at in the past (temporal
        # validator).  Seed a fresh session, then stamp an already-
        # expired refresh token against it.
        await _seed_session(backend, "sess_tk_exp", "user_exp", "xavier")
        past = _now() - timedelta(hours=1)
        await backend.refresh_tokens.create(
            token_hash="t_exp",
            session_id="sess_tk_exp",
            user_id="user_exp",
            expires_at=past,
        )
        outcome = await backend.refresh_tokens.consume("t_exp")
        assert outcome.record is None
        assert outcome.reject_reason is RefreshRejectReason.NOT_FOUND_OR_EXPIRED

    async def test_consume_respects_session_revocation_callback(
        self, backend: PersistenceBackend
    ) -> None:
        await _seed_session(backend, "sess_rv", "user_rv", "roderick")
        await backend.refresh_tokens.create(
            token_hash="t_rev",
            session_id="sess_rv",
            user_id="user_rv",
            expires_at=_now() + timedelta(hours=1),
        )
        outcome = await backend.refresh_tokens.consume(
            "t_rev",
            is_session_revoked=lambda _sid: True,
        )
        assert outcome.record is None
        assert outcome.reject_reason is RefreshRejectReason.SESSION_REVOKED

    async def test_revoke_by_session(self, backend: PersistenceBackend) -> None:
        await _seed_session(backend, "sess_group", "user_g", "gaby")
        await backend.refresh_tokens.create(
            token_hash="t_a",
            session_id="sess_group",
            user_id="user_g",
            expires_at=_now() + timedelta(hours=1),
        )
        await backend.refresh_tokens.create(
            token_hash="t_b",
            session_id="sess_group",
            user_id="user_g",
            expires_at=_now() + timedelta(hours=1),
        )
        count = await backend.refresh_tokens.revoke_by_session("sess_group")
        assert count >= 2
        for ref in ("t_a", "t_b"):
            outcome = await backend.refresh_tokens.consume(ref)
            assert outcome.record is None
            # Revoke flags ``used = TRUE`` -- consume sees a replay.
            assert outcome.reject_reason is RefreshRejectReason.REPLAY_DETECTED

    async def test_revoke_by_user(self, backend: PersistenceBackend) -> None:
        await _seed_session(backend, "sess_ubu", "user_mass_rt", "marta")
        await backend.refresh_tokens.create(
            token_hash="t_u1",
            session_id="sess_ubu",
            user_id="user_mass_rt",
            expires_at=_now() + timedelta(hours=1),
        )
        count = await backend.refresh_tokens.revoke_by_user("user_mass_rt")
        assert count >= 1
        outcome = await backend.refresh_tokens.consume("t_u1")
        assert outcome.record is None
        assert outcome.reject_reason is RefreshRejectReason.REPLAY_DETECTED

    async def test_cleanup_expired(self, backend: PersistenceBackend) -> None:
        await _seed_session(backend, "sess_cl", "user_cl", "cleo")
        await backend.refresh_tokens.create(
            token_hash="t_cleanup",
            session_id="sess_cl",
            user_id="user_cl",
            expires_at=_now() - timedelta(days=1),
        )
        removed = await backend.refresh_tokens.cleanup_expired()
        assert removed >= 1


# ── LockoutRepository ──────────────────────────────────────────


class TestLockoutRepository:
    def _config(self) -> AuthConfig:
        return AuthConfig(
            lockout_threshold=3,
            lockout_window_minutes=15,
            lockout_duration_minutes=15,
        )

    async def test_record_failure_below_threshold_does_not_lock(
        self, backend: PersistenceBackend
    ) -> None:
        repo = backend.build_lockouts(self._config())
        locked = await repo.record_failure("bob", ip_address="127.0.0.1")
        assert locked is False
        assert repo.is_locked("bob") is False

    async def test_record_failure_reaches_threshold_and_locks(
        self, backend: PersistenceBackend
    ) -> None:
        repo = backend.build_lockouts(self._config())
        for _ in range(3):
            await repo.record_failure("eve", ip_address="127.0.0.1")
        assert repo.is_locked("eve") is True

    async def test_record_success_clears_failures(
        self, backend: PersistenceBackend
    ) -> None:
        repo = backend.build_lockouts(self._config())
        for _ in range(2):
            await repo.record_failure("carol", ip_address="127.0.0.1")
        await repo.record_success("carol")
        assert repo.is_locked("carol") is False
        # One more failure after success must NOT re-lock immediately
        await repo.record_failure("carol", ip_address="127.0.0.1")
        assert repo.is_locked("carol") is False

    async def test_lockout_duration_exposed_for_retry_after(
        self, backend: PersistenceBackend
    ) -> None:
        repo = backend.build_lockouts(self._config())
        assert repo.lockout_duration_seconds == 15 * 60

    async def test_load_locked_is_idempotent(self, backend: PersistenceBackend) -> None:
        repo = backend.build_lockouts(self._config())
        first = await repo.load_locked()
        second = await repo.load_locked()
        assert first == second
