"""SSE session-ownership and API-key revocation.

The AG-UI ``session_id`` is the task id: only the human who filed the
task (``Task.requested_by_user_id``) or a CEO may subscribe to its event
stream, enforced at handshake AND on every revalidation tick. API-key
streams carry no JWT jti, so the tick re-fetches the originating key to
honour revocation / expiry.
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from synthorg.api.controllers.events._sse import (
    _api_key_revocation_reason,
    _run_revalidation_tick,
    _session_ownership_reason,
    _user_revocation_reason,
    assert_sse_session_access,
)
from synthorg.api.state import AppState
from synthorg.core.auth.models import ApiKey, AuthenticatedUser, AuthMethod, User
from synthorg.core.auth.roles import HumanRole
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.resilience import SlidingWindowEventLimiter
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskType
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import FakeClock, as_uuid, make_app_state, mock_of

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)


def _task(*, requested_by_user_id: str | None) -> Task:
    return Task(
        id=as_uuid("sess-1"),
        title="A task",
        description="A task description",
        type=TaskType.DEVELOPMENT,
        project="proj",
        created_by="agent-bob",
        requested_by_user_id=requested_by_user_id,
    )


def _api_key(*, revoked: bool = False, expires_at: datetime | None = None) -> ApiKey:
    return ApiKey(
        id="key-1",
        key_hash="hash-deadbeef",
        name="ci-key",
        role=HumanRole.OBSERVER,
        user_id="u-001",
        created_at=_NOW - timedelta(days=1),
        expires_at=expires_at,
        revoked=revoked,
    )


def _persisted_user(role: HumanRole = HumanRole.OBSERVER) -> User:
    return User(
        id="u-001",
        username="alice",
        password_hash="$argon2id$v=19$m=65536,t=3,p=4$cGVwcGVy$abcd1234",
        role=role,
        must_change_password=False,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _auth_user(
    *,
    user_id: str = "u-001",
    role: HumanRole = HumanRole.OBSERVER,
    session_id: str | None = None,
    api_key_id: str | None = None,
) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user_id,
        username="alice",
        role=role,
        auth_method=AuthMethod.JWT if api_key_id is None else AuthMethod.API_KEY,
        session_id=session_id,
        api_key_id=api_key_id,
    )


def _state_with_task(task: Task | None, *, raise_on_get: bool = False) -> AppState:
    tasks_repo = AsyncMock()
    if raise_on_get:
        tasks_repo.get.side_effect = RuntimeError("transient db blip")
    else:
        tasks_repo.get.return_value = task
    persistence = mock_of[PersistenceBackend](tasks=tasks_repo)
    return make_app_state(persistence=persistence, clock=FakeClock(start=_NOW))


# ── _session_ownership_reason ──────────────────────────────────────────


async def test_ownership_owner_match_is_authorised() -> None:
    state = _state_with_task(_task(requested_by_user_id="u-001"))
    reason, ok = await _session_ownership_reason(state, "sess-1", _auth_user())
    assert ok is True
    assert reason is None


async def test_ownership_non_owner_is_denied() -> None:
    state = _state_with_task(_task(requested_by_user_id="someone-else"))
    reason, ok = await _session_ownership_reason(state, "sess-1", _auth_user())
    assert ok is True
    assert reason == "session_not_owned"


async def test_ownership_missing_task_is_denied() -> None:
    state = _state_with_task(None)
    reason, ok = await _session_ownership_reason(state, "sess-1", _auth_user())
    assert ok is True
    assert reason == "session_not_owned"


async def test_ownership_ceo_bypasses_without_lookup() -> None:
    state = _state_with_task(None)
    reason, ok = await _session_ownership_reason(
        state, "sess-1", _auth_user(role=HumanRole.CEO)
    )
    assert ok is True
    assert reason is None


async def test_ownership_transient_error_signals_not_ok() -> None:
    state = _state_with_task(None, raise_on_get=True)
    reason, ok = await _session_ownership_reason(state, "sess-1", _auth_user())
    assert ok is False
    assert reason is None


# ── assert_sse_session_access (handshake) ──────────────────────────────


async def test_handshake_owner_allowed() -> None:
    state = _state_with_task(_task(requested_by_user_id="u-001"))
    await assert_sse_session_access(state, "sess-1", _auth_user())  # no raise


async def test_handshake_non_owner_404() -> None:
    state = _state_with_task(_task(requested_by_user_id="someone-else"))
    with pytest.raises(NotFoundError):
        await assert_sse_session_access(state, "sess-1", _auth_user())


async def test_handshake_ceo_allowed() -> None:
    state = _state_with_task(_task(requested_by_user_id="someone-else"))
    await assert_sse_session_access(state, "sess-1", _auth_user(role=HumanRole.CEO))


async def test_handshake_transient_error_fails_closed() -> None:
    state = _state_with_task(None, raise_on_get=True)
    with pytest.raises(NotFoundError):
        await assert_sse_session_access(state, "sess-1", _auth_user())


# ── _api_key_revocation_reason ─────────────────────────────────────────


def _state_with_api_key(
    api_key: ApiKey | None, *, raise_on_get: bool = False
) -> AppState:
    api_keys_repo = AsyncMock()
    if raise_on_get:
        api_keys_repo.get.side_effect = RuntimeError("transient db blip")
    else:
        api_keys_repo.get.return_value = api_key
    persistence = mock_of[PersistenceBackend](api_keys=api_keys_repo)
    return make_app_state(persistence=persistence, clock=FakeClock(start=_NOW))


async def test_api_key_active_is_authorised() -> None:
    state = _state_with_api_key(_api_key())
    reason, ok = await _api_key_revocation_reason(state, "key-1")
    assert ok is True
    assert reason is None


async def test_api_key_revoked_kicks_stream() -> None:
    state = _state_with_api_key(_api_key(revoked=True))
    reason, ok = await _api_key_revocation_reason(state, "key-1")
    assert ok is True
    assert reason == "api_key_revoked"


async def test_api_key_missing_kicks_stream() -> None:
    state = _state_with_api_key(None)
    reason, ok = await _api_key_revocation_reason(state, "key-1")
    assert ok is True
    assert reason == "api_key_revoked"


async def test_api_key_expired_kicks_stream() -> None:
    state = _state_with_api_key(_api_key(expires_at=_NOW - timedelta(minutes=1)))
    reason, ok = await _api_key_revocation_reason(state, "key-1")
    assert ok is True
    assert reason == "api_key_expired"


async def test_api_key_not_yet_expired_is_authorised() -> None:
    state = _state_with_api_key(_api_key(expires_at=_NOW + timedelta(hours=1)))
    reason, ok = await _api_key_revocation_reason(state, "key-1")
    assert ok is True
    assert reason is None


async def test_api_key_transient_error_signals_not_ok() -> None:
    state = _state_with_api_key(None, raise_on_get=True)
    reason, ok = await _api_key_revocation_reason(state, "key-1")
    assert ok is False
    assert reason is None


# ── _user_revocation_reason: API-key stream path ───────────────────────


async def test_user_revocation_rechecks_api_key_when_no_jwt_session() -> None:
    """An API-key stream (no jti) re-inspects the originating key, so a
    revoked key tears the stream down within one revalidation interval."""
    users_repo = AsyncMock()
    users_repo.get.return_value = _persisted_user()
    api_keys_repo = AsyncMock()
    api_keys_repo.get.return_value = _api_key(revoked=True)
    persistence = mock_of[PersistenceBackend](users=users_repo, api_keys=api_keys_repo)
    state = make_app_state(persistence=persistence, clock=FakeClock(start=_NOW))

    reason, ok = await _user_revocation_reason(state, _auth_user(api_key_id="key-1"))
    assert ok is True
    assert reason == "api_key_revoked"


# ── _run_revalidation_tick: ownership loss ─────────────────────────────


async def test_revalidation_tick_revokes_on_ownership_loss() -> None:
    """An active user whose session task is reassigned / deleted is kicked
    with a ``session_not_owned`` revoked frame on the next tick."""
    users_repo = AsyncMock()
    users_repo.get.return_value = _persisted_user()
    tasks_repo = AsyncMock()
    tasks_repo.get.return_value = _task(requested_by_user_id="someone-else")
    persistence = mock_of[PersistenceBackend](users=users_repo, tasks=tasks_repo)
    state = make_app_state(persistence=persistence, clock=FakeClock(start=_NOW))
    limiter = SlidingWindowEventLimiter(max_events=5, window_seconds=60.0)

    revoked = await _run_revalidation_tick(
        app_state=state,
        user=_auth_user(),
        session_id="sess-1",
        failure_limiter=limiter,
    )
    assert revoked is not None
    assert json.loads(revoked["data"])["reason"] == "session_not_owned"
