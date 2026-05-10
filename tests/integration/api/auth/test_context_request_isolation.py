"""Cross-request isolation + middleware-wiring test for the auth ContextVar.

Drives ``N`` concurrent HTTP requests against a Litestar app whose
middleware stack includes :class:`~synthorg.api.auth.context.AuthContextMiddleware`
running after a small test-only middleware that injects a different
:class:`~synthorg.core.auth.models.AuthenticatedUser` into ``scope["user"]``
per request (so the test does not depend on real JWT setup). Each handler
parks on a barrier so all ``N`` requests are simultaneously in-flight
before any returns.

Honest scope: ContextVars are per-:class:`asyncio.Task` and Litestar's
:class:`TestClient` runs each request on its own Task, so this test
catches **wiring** errors (middleware not in chain, scope-key mismatch,
:class:`AuthContextMissingError` fires inside the handler) rather than
the explicit-reset contract. The reset contract is enforced by the unit
tests in ``tests/unit/api/auth/test_context.py``.
"""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Final, cast

import pytest
from litestar import Litestar, get
from litestar.enums import ScopeType
from litestar.handlers import HTTPRouteHandler
from litestar.testing import TestClient
from litestar.types import ASGIApp, Middleware, Receive, Scope, Send

from synthorg.api.auth.context import (
    AuthContextMiddleware,
    _authenticated_user,
    get_authenticated_user_id,
)
from synthorg.core.auth.models import AuthenticatedUser, AuthMethod
from synthorg.core.auth.roles import HumanRole

pytestmark = pytest.mark.integration

_CONCURRENT_REQUESTS: Final[int] = 8
# Bounded so a missing release cannot wedge the suite; well under the
# pytest 30s per-test timeout so a deadlock surfaces here as a clear
# barrier-broken failure rather than a generic timeout.
#
# Deadlock note: ``threading.Barrier`` deadlocks if a participant errors
# before reaching ``barrier.wait()``. Both tests below put the
# barrier.wait() call BEFORE any code that can raise (the user_id read
# is the only line ahead of it, and it succeeds when the middleware is
# wired correctly). If an early raise ever happens, the bounded timeout
# above turns the deadlock into a ``BrokenBarrierError``, which is a
# loud failure rather than a hang.
_BARRIER_TIMEOUT_SECONDS: Final[float] = 5.0
_TEST_USER_HEADER: Final[str] = "x-test-user-id"


def _user_id_for(index: int) -> str:
    """Stable user-id format shared by header injection and assertions.

    Centralised so a future change to the format cannot desync
    request-side header values from the ``_make_user`` factory or the
    expected-id assertions.
    """
    return f"user-{index:02d}"


def _make_user(user_id: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user_id,
        username=f"{user_id}@example.com",
        role=HumanRole.CEO,
        auth_method=AuthMethod.JWT,
    )


class _ScopeUserInjector:
    """Test-only middleware that fakes auth by reading a header.

    Reads ``X-Test-User-Id`` and writes a synthetic ``AuthenticatedUser``
    into ``scope["user"]`` so :class:`AuthContextMiddleware` (running
    immediately after) has something to bind without real JWT plumbing.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] == ScopeType.HTTP:
            target = _TEST_USER_HEADER.encode("ascii")
            for raw_name, raw_value in scope["headers"]:
                if raw_name == target:
                    user_id = raw_value.decode("ascii")
                    scope["user"] = _make_user(user_id)
                    break
        await self.app(scope, receive, send)


def _build_app(handler: HTTPRouteHandler) -> Litestar:
    """Litestar app with the injector + AuthContextMiddleware pair."""
    middleware: list[Middleware] = [
        cast("Middleware", _ScopeUserInjector),
        AuthContextMiddleware(),
    ]
    return Litestar(
        route_handlers=[handler],
        middleware=middleware,
    )


class TestConcurrentRequestIsolation:
    def test_eight_concurrent_requests_each_see_their_own_user(self) -> None:
        # Barrier ensures every handler is simultaneously parked
        # before any returns; if AuthContextMiddleware wired the var
        # to a single shared slot, at least one handler would observe
        # someone else's id mid-flight.
        barrier = threading.Barrier(_CONCURRENT_REQUESTS)

        @get("/whoami")
        async def whoami() -> dict[str, str]:
            user_id = get_authenticated_user_id()
            await asyncio.to_thread(barrier.wait, _BARRIER_TIMEOUT_SECONDS)
            return {"user_id": user_id}

        app = _build_app(whoami)
        with (
            TestClient(app) as client,
            ThreadPoolExecutor(max_workers=_CONCURRENT_REQUESTS) as pool,
        ):
            futures = [
                pool.submit(
                    client.get,
                    "/whoami",
                    headers={_TEST_USER_HEADER: _user_id_for(i)},
                )
                for i in range(_CONCURRENT_REQUESTS)
            ]
            responses = [f.result(timeout=_BARRIER_TIMEOUT_SECONDS) for f in futures]

        observed_ids = sorted(r.json()["user_id"] for r in responses)
        expected_ids = sorted(_user_id_for(i) for i in range(_CONCURRENT_REQUESTS))
        assert observed_ids == expected_ids
        for response in responses:
            assert response.status_code == 200

        # Catches accidental bleed of the bound user into the test
        # process's main task; would only happen if the middleware
        # forgot to reset on a path that produced a response.
        assert _authenticated_user.get() is None

    def test_failure_path_does_not_poison_var_for_concurrent_requests(self) -> None:
        # Half the handlers raise; remaining requests must still
        # observe their own user id and the var must be unset
        # post-test.
        barrier = threading.Barrier(_CONCURRENT_REQUESTS)

        @get("/whoami-or-fail")
        async def whoami_or_fail() -> dict[str, str]:
            user_id = get_authenticated_user_id()
            await asyncio.to_thread(barrier.wait, _BARRIER_TIMEOUT_SECONDS)
            # Even-numbered users explode after the barrier so half
            # the requests exit via the exception path of the
            # ContextVar finally block.
            if int(user_id.split("-")[-1]) % 2 == 0:
                msg = f"forced failure for {user_id}"
                raise RuntimeError(msg)
            return {"user_id": user_id}

        app = _build_app(whoami_or_fail)
        with (
            TestClient(app, raise_server_exceptions=False) as client,
            ThreadPoolExecutor(max_workers=_CONCURRENT_REQUESTS) as pool,
        ):
            futures = [
                pool.submit(
                    client.get,
                    "/whoami-or-fail",
                    headers={_TEST_USER_HEADER: _user_id_for(i)},
                )
                for i in range(_CONCURRENT_REQUESTS)
            ]
            responses = [f.result(timeout=_BARRIER_TIMEOUT_SECONDS) for f in futures]

        success_ids: list[str] = []
        for response in responses:
            if response.status_code == 200:
                success_ids.append(response.json()["user_id"])
            else:
                # An unhandled RuntimeError flows through Litestar's
                # default exception handler to a 500; tighten the
                # assertion so a 502/503 (which would indicate a
                # different failure mode) does not pass silently.
                assert response.status_code == 500

        expected_success = sorted(
            _user_id_for(i) for i in range(_CONCURRENT_REQUESTS) if i % 2 == 1
        )
        assert sorted(success_ids) == expected_success
        assert _authenticated_user.get() is None

    def test_handler_on_unauthenticated_path_returns_500_typed_error(
        self,
    ) -> None:
        # When a request reaches a handler that calls
        # get_authenticated_user_id() but no AuthenticatedUser was bound
        # (here: the test injector skips the header), the typed
        # AuthContextMissingError surfaces as a 500. Confirms the
        # unset-read contract end-to-end via the real exception
        # handler chain rather than only the unit-level raise test.
        @get("/whoami-strict")
        async def whoami_strict() -> dict[str, str]:
            return {"user_id": get_authenticated_user_id()}

        app = _build_app(whoami_strict)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/whoami-strict")
        assert response.status_code == 500
        assert _authenticated_user.get() is None
