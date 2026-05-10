"""Tests for ``synthorg.api.auth.context``.

Covers the ``ContextVar`` accessors, the ``authenticated_user_scope``
async context manager, and the ``AuthContextMiddleware`` ASGI middleware
that binds ``scope["user"]`` into the per-task ContextVar.
"""

from collections.abc import Awaitable, Callable
from typing import Any, cast

import pytest

from synthorg.api.auth.context import (
    AuthContextMiddleware,
    AuthContextMissingError,
    _authenticated_user,
    authenticated_user_scope,
    get_authenticated_user,
    get_authenticated_user_id,
)
from synthorg.core.auth.models import AuthenticatedUser, AuthMethod
from synthorg.core.auth.roles import HumanRole

pytestmark = pytest.mark.unit


def _make_user(
    user_id: str = "user-42",
    username: str = "alice@example.com",
) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user_id,
        username=username,
        role=HumanRole.CEO,
        auth_method=AuthMethod.JWT,
    )


class TestGetAuthenticatedUser:
    async def test_unset_raises_auth_context_missing(self) -> None:
        assert _authenticated_user.get() is None
        with pytest.raises(AuthContextMissingError):
            get_authenticated_user()

    async def test_unset_id_raises_auth_context_missing(self) -> None:
        with pytest.raises(AuthContextMissingError):
            get_authenticated_user_id()

    async def test_returns_bound_user_inside_scope(self) -> None:
        user = _make_user()
        async with authenticated_user_scope(user):
            assert get_authenticated_user() is user
            assert get_authenticated_user_id() == "user-42"

    async def test_default_message_used_when_unset(self) -> None:
        with pytest.raises(
            AuthContextMissingError,
            match="Authentication context is not bound",
        ):
            get_authenticated_user()

    async def test_status_code_is_500(self) -> None:
        # Reading while unset is a server invariant violation, not a
        # client error: the typed status must surface as 500 so the
        # exception handler does not mask middleware misconfiguration
        # behind a 401.
        assert AuthContextMissingError.status_code == 500


class TestAuthenticatedUserScope:
    async def test_scope_resets_on_normal_exit(self) -> None:
        user = _make_user()
        async with authenticated_user_scope(user):
            assert _authenticated_user.get() is user
        assert _authenticated_user.get() is None

    async def test_scope_resets_on_exception(self) -> None:
        user = _make_user()

        async def _enter_and_raise() -> None:
            async with authenticated_user_scope(user):
                msg = "boom"
                raise RuntimeError(msg)

        with pytest.raises(RuntimeError, match="boom"):
            await _enter_and_raise()
        assert _authenticated_user.get() is None

    async def test_nested_scope_shadows_and_restores(self) -> None:
        outer = _make_user("outer-id", "outer@example.com")
        inner = _make_user("inner-id", "inner@example.com")
        async with authenticated_user_scope(outer):
            assert get_authenticated_user_id() == "outer-id"
            async with authenticated_user_scope(inner):
                assert get_authenticated_user_id() == "inner-id"
            assert get_authenticated_user_id() == "outer-id"
        assert _authenticated_user.get() is None

    async def test_child_task_inherits_scope_without_polluting_parent(
        self,
    ) -> None:
        # ContextVars are copied (not shared) into child Tasks. The
        # child sees the parent's binding, can rebind locally, and the
        # parent's binding remains intact when the child returns.
        import asyncio

        parent = _make_user("parent-id", "parent@example.com")
        child = _make_user("child-id", "child@example.com")
        observed: dict[str, str] = {}

        async def _child_task() -> None:
            observed["inherited"] = get_authenticated_user_id()
            async with authenticated_user_scope(child):
                observed["child_local"] = get_authenticated_user_id()

        async with authenticated_user_scope(parent):
            await asyncio.create_task(_child_task())
            observed["parent_after"] = get_authenticated_user_id()
        assert observed == {
            "inherited": "parent-id",
            "child_local": "child-id",
            "parent_after": "parent-id",
        }
        assert _authenticated_user.get() is None


class _CaptureApp:
    """Stub ``next_app`` that records the active ContextVar when invoked."""

    def __init__(self) -> None:
        self.observed_user: AuthenticatedUser | None = None
        self.called: bool = False

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        del scope, receive, send
        self.observed_user = _authenticated_user.get()
        self.called = True


class _BoomApp:
    """Stub ``next_app`` that raises after observing the ContextVar.

    Lets a test confirm the middleware still resets the ContextVar on
    the exception path.
    """

    def __init__(self) -> None:
        self.observed_user: AuthenticatedUser | None = None

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        del scope, receive, send
        self.observed_user = _authenticated_user.get()
        msg = "downstream failure"
        raise RuntimeError(msg)


async def _noop_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


async def _noop_send(message: dict[str, Any]) -> None:
    del message


async def _drive(
    middleware: AuthContextMiddleware,
    scope: dict[str, Any],
    next_app: _CaptureApp | _BoomApp,
) -> None:
    # Litestar's ASGI types are very precise (TypedDict per event kind);
    # matching them here would add boilerplate without raising the
    # signal of these tests. Cast to Any at this single seam.
    await middleware.handle(
        cast("Any", scope),
        cast("Any", _noop_receive),
        cast("Any", _noop_send),
        cast("Any", next_app),
    )


class TestAuthContextMiddleware:
    async def test_binds_when_scope_user_is_authenticated_user(self) -> None:
        next_app = _CaptureApp()
        middleware = AuthContextMiddleware()
        user = _make_user()
        scope: dict[str, Any] = {
            "type": "http",
            "path": "/api/test",
            "user": user,
        }
        await _drive(middleware, scope, next_app)
        assert next_app.called
        assert next_app.observed_user is user

    async def test_passthrough_when_scope_user_missing(self) -> None:
        next_app = _CaptureApp()
        middleware = AuthContextMiddleware()
        scope: dict[str, Any] = {"type": "http", "path": "/api/health"}
        await _drive(middleware, scope, next_app)
        assert next_app.called
        assert next_app.observed_user is None

    async def test_passthrough_when_scope_user_not_authenticated_user(self) -> None:
        next_app = _CaptureApp()
        middleware = AuthContextMiddleware()
        scope: dict[str, Any] = {
            "type": "http",
            "path": "/api/health",
            "user": {"id": "spoof"},
        }
        await _drive(middleware, scope, next_app)
        assert next_app.called
        assert next_app.observed_user is None

    async def test_resets_after_dispatch(self) -> None:
        # The scope-isolation effect of asyncio.Task would hide a
        # forgotten .reset(token); reading the var post-dispatch is
        # what enforces the explicit-reset contract.
        next_app = _CaptureApp()
        middleware = AuthContextMiddleware()
        user = _make_user()
        scope: dict[str, Any] = {"type": "http", "path": "/api/test", "user": user}
        await _drive(middleware, scope, next_app)
        assert _authenticated_user.get() is None

    async def test_resets_after_dispatch_exception(self) -> None:
        next_app = _BoomApp()
        middleware = AuthContextMiddleware()
        user = _make_user()
        scope: dict[str, Any] = {"type": "http", "path": "/api/test", "user": user}
        with pytest.raises(RuntimeError, match="downstream failure"):
            await _drive(middleware, scope, next_app)
        assert next_app.observed_user is user
        assert _authenticated_user.get() is None

    async def test_skipped_path_clears_inherited_principal(self) -> None:
        # If the middleware's skipped branch (excluded paths or a
        # non-AuthenticatedUser scope.user) bypassed the ContextVar,
        # an outer binding inherited via context-copy semantics could
        # leak into helpers reading the var from the inner dispatch.
        # Bind a user in the surrounding context, then run the
        # middleware with no scope.user, and assert the dispatch sees
        # None -- not the inherited principal.
        next_app = _CaptureApp()
        middleware = AuthContextMiddleware()
        outer = _make_user("outer-id", "outer@example.com")
        async with authenticated_user_scope(outer):
            assert _authenticated_user.get() is outer
            scope: dict[str, Any] = {"type": "http", "path": "/api/health"}
            await _drive(middleware, scope, next_app)
            assert next_app.observed_user is None
            # Outer binding restored after the middleware's reset.
            assert _authenticated_user.get() is outer

    async def test_skipped_path_clears_when_scope_user_wrong_type(self) -> None:
        # Same invariant for the "present but not AuthenticatedUser"
        # branch: an inherited binding must not leak through.
        next_app = _CaptureApp()
        middleware = AuthContextMiddleware()
        outer = _make_user("outer-id", "outer@example.com")
        async with authenticated_user_scope(outer):
            scope: dict[str, Any] = {
                "type": "http",
                "path": "/api/health",
                "user": {"id": "spoof"},
            }
            await _drive(middleware, scope, next_app)
            assert next_app.observed_user is None
            assert _authenticated_user.get() is outer
