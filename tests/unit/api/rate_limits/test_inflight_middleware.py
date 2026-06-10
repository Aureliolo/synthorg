"""Unit tests for PerOpConcurrencyMiddleware.

Concurrent scenarios use :class:`TestClient` (sync) driven by a
``ThreadPoolExecutor`` so requests land in the Litestar app truly in
parallel.  ``AsyncTestClient`` + ``httpx.ASGITransport`` serialises
requests in this harness and hides concurrency races -- see
``test_per_op_rate_limit_concurrent.py`` for the end-to-end integration
spec that mirrors this pattern.
"""

import asyncio
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Event as ThreadingEvent
from typing import Any

import httpx
import pytest
from litestar import Litestar, post
from litestar.datastructures import State
from litestar.testing import AsyncTestClient, TestClient

from synthorg.api.exception_handlers import EXCEPTION_HANDLERS
from synthorg.api.rate_limits.in_memory_inflight import InMemoryInflightStore
from synthorg.api.rate_limits.inflight_config import PerOpConcurrencyConfig
from synthorg.api.rate_limits.inflight_guard import per_op_concurrency
from synthorg.api.rate_limits.inflight_middleware import PerOpConcurrencyMiddleware

pytestmark = pytest.mark.unit

# Bounded timeout on the per-handler hold so a bug that forgets to
# call ``release()`` cannot wedge the suite -- 5s is far longer than
# any legitimate test path needs and still below the pytest per-test
# timeout (30s) so the failure surfaces as "hold never released"
# rather than a generic test timeout.
_HOLD_TIMEOUT_SECONDS = 5.0


async def _signal_entry_and_wait(
    entered: ThreadingEvent,
    release: ThreadingEvent,
) -> None:
    """Handler barrier: signal "entered", then park until released.

    Runs inside the permit-holder's handler coroutine.  Setting
    ``entered`` tells the test harness the holder is inside the
    handler (and so has the permit); the subsequent ``release.wait``
    parks the coroutine on a worker thread so the event loop keeps
    dispatching sibling requests that should be denied by the
    middleware.  Replaces the previous ``asyncio.sleep(0.3)`` pattern
    which made the unit suite timing-sensitive under ``-n 8``.
    """
    entered.set()
    await asyncio.to_thread(release.wait, _HOLD_TIMEOUT_SECONDS)


def _make_test_app(  # type: ignore[explicit-any]  # litestar route-handler union
    handler: Any,
    *,
    config: PerOpConcurrencyConfig | None = None,
) -> Litestar:
    """Build a Litestar app with the inflight store + middleware wired."""
    store = InMemoryInflightStore()
    cfg = config or PerOpConcurrencyConfig()
    return Litestar(
        route_handlers=[handler],
        middleware=[PerOpConcurrencyMiddleware()],
        state=State(
            {
                "per_op_inflight_store": store,
                "per_op_inflight_config": cfg,
            },
        ),
        exception_handlers=dict(EXCEPTION_HANDLERS),  # type: ignore[arg-type]
    )


def _fire_concurrent_posts(  # noqa: PLR0913 -- test helper with optional knobs
    client: TestClient[Litestar],
    path: str,
    count: int,
    *,
    entered: ThreadingEvent | None = None,
    release: ThreadingEvent | None = None,
    holders: int = 1,
) -> list[httpx.Response]:
    """Fire ``count`` POSTs in parallel via a thread pool.

    When ``entered`` and ``release`` are both provided the helper runs
    a fully deterministic handshake:

      1. Submit ``count`` concurrent ``client.post`` futures.
      2. Wait for ``entered`` -- set by the first permit-holder once
         its handler has entered the body.
      3. Wait for ``count - holders`` futures to complete -- those
         are the siblings that the middleware denied with 429
         (``holders`` futures are still parked in the handler, so by
         pigeonhole the completed ones are the denials).
      4. Set ``release`` so every parked holder finishes and the
         remaining futures resolve.

    No ``time.sleep`` anywhere: every transition is gated on a
    primitive that a prior step observably set.  The bounded waits
    fall back to pytest's per-test timeout if the wiring is broken,
    so a bug surfaces as a clear failure rather than a hang.
    """
    if (entered is None) != (release is None):
        msg = "entered and release must be provided together or not at all"
        raise ValueError(msg)
    if holders < 1 or holders > count:
        msg = f"holders={holders} must be in [1, {count}]"
        raise ValueError(msg)

    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(client.post, path) for _ in range(count)]
        if entered is not None and release is not None:
            if not entered.wait(_HOLD_TIMEOUT_SECONDS):
                msg = "Handler never signalled 'entered' within the timeout"
                raise AssertionError(msg)
            pending = set(futures)
            denied_seen = 0
            expected_denials = count - holders
            while denied_seen < expected_denials and pending:
                done, pending = wait(
                    pending,
                    timeout=_HOLD_TIMEOUT_SECONDS,
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    # Timed out with no progress -- the middleware
                    # did not deny a sibling within the budget.  Fail
                    # fast rather than spin: the test's invariant is
                    # broken, and masking the failure behind a
                    # generic pytest timeout loses the diagnostic.
                    msg = (
                        "wait() timed out without a completed future "
                        f"(denied_seen={denied_seen}, "
                        f"expected_denials={expected_denials})"
                    )
                    raise AssertionError(msg)
                denied_seen += len(done)
            release.set()
        return [f.result() for f in futures]


class TestUnannotatedHandlersPassThrough:
    """Handlers without ``opt[per_op_concurrency]`` are unaffected."""

    async def test_no_opt_means_no_enforcement(self) -> None:
        @post("/t")
        async def handler() -> dict[str, bool]:
            return {"ok": True}

        async with AsyncTestClient(app=_make_test_app(handler)) as client:
            resp = await client.post("/t")
            assert resp.status_code == 201


class TestConcurrencyEnforcement:
    """Opt-annotated handlers enforce inflight caps."""

    def test_concurrent_requests_partially_denied(self) -> None:
        """5 concurrent requests with max_inflight=2 -> 2 ok, 3 denied."""
        entered = ThreadingEvent()
        release = ThreadingEvent()

        @post(
            "/t",
            opt=per_op_concurrency("test.cap", max_inflight=2, key="ip"),
        )
        async def handler() -> dict[str, bool]:
            await _signal_entry_and_wait(entered, release)
            return {"ok": True}

        with TestClient(app=_make_test_app(handler)) as client:
            results = _fire_concurrent_posts(
                client,
                "/t",
                5,
                entered=entered,
                release=release,
                holders=2,
            )
        statuses = [r.status_code for r in results]
        assert statuses.count(201) == 2
        assert statuses.count(429) == 3
        # Every 429 carries the concurrency error code + Retry-After.
        for resp in results:
            if resp.status_code == 429:
                body = resp.json()
                assert body["error_detail"]["error_category"] == "rate_limit"
                assert body["error_detail"]["error_code"] == 5002
                assert body["error_detail"]["retryable"] is True
                assert int(resp.headers["Retry-After"]) >= 1

    async def test_permit_released_on_success(self) -> None:
        @post(
            "/t",
            opt=per_op_concurrency("test.released", max_inflight=1, key="ip"),
        )
        async def handler() -> dict[str, bool]:
            return {"ok": True}

        async with AsyncTestClient(app=_make_test_app(handler)) as client:
            for _ in range(5):
                resp = await client.post("/t")
                assert resp.status_code == 201

    async def test_permit_released_on_handler_exception(self) -> None:
        fail_first = {"n": 1}

        @post(
            "/t",
            opt=per_op_concurrency(
                "test.exception",
                max_inflight=1,
                key="ip",
            ),
        )
        async def handler() -> dict[str, bool]:
            if fail_first["n"] > 0:
                fail_first["n"] -= 1
                msg = "first-try-boom"
                raise RuntimeError(msg)
            return {"ok": True}

        async with AsyncTestClient(app=_make_test_app(handler)) as client:
            first = await client.post("/t")
            assert first.status_code == 500  # handler raised
            # If the permit leaked, this would be 429.  It should be 201.
            second = await client.post("/t")
            assert second.status_code == 201


class TestConfigGates:
    """Config ``enabled=False`` and zero-override disable enforcement."""

    async def test_disabled_config_bypasses_middleware(self) -> None:
        # Config master switch takes the middleware out of the path,
        # so a handler that returns immediately suffices -- there is
        # nothing to serialize when the guard is disabled.
        @post(
            "/t",
            opt=per_op_concurrency(
                "test.disabled",
                max_inflight=1,
                key="ip",
            ),
        )
        async def handler() -> dict[str, bool]:
            return {"ok": True}

        cfg = PerOpConcurrencyConfig(enabled=False)
        async with AsyncTestClient(
            app=_make_test_app(handler, config=cfg),
        ) as client:
            results = await asyncio.gather(
                *(client.post("/t") for _ in range(3)),
            )
            for resp in results:
                assert resp.status_code == 201

    async def test_zero_override_disables_single_operation(self) -> None:
        # Same rationale as ``test_disabled_config_bypasses_middleware``:
        # a per-op override of ``0`` disables enforcement for that op,
        # so contention does not need to be simulated here.
        @post(
            "/t",
            opt=per_op_concurrency(
                "test.zero",
                max_inflight=1,
                key="ip",
            ),
        )
        async def handler() -> dict[str, bool]:
            return {"ok": True}

        cfg = PerOpConcurrencyConfig(overrides={"test.zero": 0})
        async with AsyncTestClient(
            app=_make_test_app(handler, config=cfg),
        ) as client:
            results = await asyncio.gather(
                *(client.post("/t") for _ in range(3)),
            )
            for resp in results:
                assert resp.status_code == 201


class TestOverrides:
    """Config overrides replace decorator defaults."""

    def test_override_tightens_default(self) -> None:
        entered = ThreadingEvent()
        release = ThreadingEvent()

        @post(
            "/t",
            opt=per_op_concurrency(
                "test.tightened",
                max_inflight=100,
                key="ip",
            ),
        )
        async def handler() -> dict[str, bool]:
            await _signal_entry_and_wait(entered, release)
            return {"ok": True}

        cfg = PerOpConcurrencyConfig(overrides={"test.tightened": 1})
        with TestClient(app=_make_test_app(handler, config=cfg)) as client:
            results = _fire_concurrent_posts(
                client,
                "/t",
                3,
                entered=entered,
                release=release,
                holders=1,
            )
        statuses = [r.status_code for r in results]
        assert statuses.count(201) == 1
        assert statuses.count(429) == 2


class TestBucketSharing:
    """Two operations passing the SAME operation name share a bucket."""

    def test_same_operation_shares_bucket(self) -> None:
        entered = ThreadingEvent()
        release = ThreadingEvent()

        @post(
            "/start",
            opt=per_op_concurrency(
                "test.shared",
                max_inflight=1,
                key="ip",
            ),
        )
        async def start() -> dict[str, bool]:
            await _signal_entry_and_wait(entered, release)
            return {"ok": True}

        @post(
            "/resume",
            opt=per_op_concurrency(
                "test.shared",
                max_inflight=1,
                key="ip",
            ),
        )
        async def resume() -> dict[str, bool]:
            await _signal_entry_and_wait(entered, release)
            return {"ok": True}

        store = InMemoryInflightStore()
        cfg = PerOpConcurrencyConfig()
        app = Litestar(
            route_handlers=[start, resume],
            middleware=[PerOpConcurrencyMiddleware()],
            state=State(
                {
                    "per_op_inflight_store": store,
                    "per_op_inflight_config": cfg,
                },
            ),
            exception_handlers=dict(EXCEPTION_HANDLERS),  # type: ignore[arg-type]
        )
        with TestClient(app=app) as client, ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(client.post, "/start"),
                pool.submit(client.post, "/resume"),
            ]
            # Deterministic handshake: wait for the holder to enter
            # the handler, then wait for the sibling future to resolve
            # (must be the 429), then release the holder.
            if not entered.wait(_HOLD_TIMEOUT_SECONDS):
                msg = "Handler never signalled 'entered' within the timeout"
                raise AssertionError(msg)
            pending = set(futures)
            _, pending = wait(
                pending,
                timeout=_HOLD_TIMEOUT_SECONDS,
                return_when=FIRST_COMPLETED,
            )
            release.set()
            results = [f.result() for f in futures]
        statuses = sorted(r.status_code for r in results)
        # Exactly one succeeded; the other was denied by the shared bucket.
        assert statuses == [201, 429]


class TestGuardFactoryValidation:
    """``per_op_concurrency`` opt-factory rejects invalid arguments."""

    def test_empty_operation_rejected(self) -> None:
        with pytest.raises(ValueError, match="operation"):
            per_op_concurrency("", max_inflight=1)

    def test_whitespace_only_operation_rejected(self) -> None:
        with pytest.raises(ValueError, match="operation"):
            per_op_concurrency("   ", max_inflight=1)

    def test_zero_max_inflight_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_inflight"):
            per_op_concurrency("test.op", max_inflight=0)

    def test_negative_max_inflight_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_inflight"):
            per_op_concurrency("test.op", max_inflight=-1)


class TestWiringError:
    """Missing store/config fails loud and closed (503)."""

    def test_missing_store_raises_503(self) -> None:
        @post(
            "/t",
            opt=per_op_concurrency("test.missing", max_inflight=1, key="ip"),
        )
        async def handler() -> dict[str, bool]:
            return {"ok": True}

        # Wire config but NOT store -- simulates a deployment that
        # constructed the config but forgot to build the store.
        app = Litestar(
            route_handlers=[handler],
            middleware=[PerOpConcurrencyMiddleware()],
            state=State(
                {
                    "per_op_inflight_config": PerOpConcurrencyConfig(),
                    # per_op_inflight_store deliberately missing
                },
            ),
            exception_handlers=dict(EXCEPTION_HANDLERS),  # type: ignore[arg-type]
        )
        with TestClient(app=app) as client:
            resp = client.post("/t")
        assert resp.status_code == 503
        body = resp.json()
        assert body["success"] is False
        assert body["error_detail"]["error_category"] == "internal"

    def test_missing_config_raises_503(self) -> None:
        @post(
            "/t",
            opt=per_op_concurrency("test.missing2", max_inflight=1, key="ip"),
        )
        async def handler() -> dict[str, bool]:
            return {"ok": True}

        store = InMemoryInflightStore()
        app = Litestar(
            route_handlers=[handler],
            middleware=[PerOpConcurrencyMiddleware()],
            state=State(
                {
                    "per_op_inflight_store": store,
                    # per_op_inflight_config deliberately missing
                },
            ),
            exception_handlers=dict(EXCEPTION_HANDLERS),  # type: ignore[arg-type]
        )
        with TestClient(app=app) as client:
            resp = client.post("/t")
        assert resp.status_code == 503


class TestKeyPolicyVariants:
    """Guard bucketing honours all three key policies."""

    def test_user_or_ip_policy_accepted(self) -> None:
        """Sanity: the user_or_ip key policy wires end-to-end.

        Unit tests drive requests from a single IP with no auth scope,
        so user_or_ip and ip collapse to the same bucket.  The test
        verifies the middleware does not explode on the non-ip key
        policy and still enforces the cap.
        """
        entered = ThreadingEvent()
        release = ThreadingEvent()

        @post(
            "/t",
            opt=per_op_concurrency(
                "test.user_or_ip",
                max_inflight=2,
                key="user_or_ip",
            ),
        )
        async def handler() -> dict[str, bool]:
            await _signal_entry_and_wait(entered, release)
            return {"ok": True}

        with TestClient(app=_make_test_app(handler)) as client:
            results = _fire_concurrent_posts(
                client,
                "/t",
                5,
                entered=entered,
                release=release,
                holders=2,
            )
        statuses = [r.status_code for r in results]
        assert statuses.count(201) == 2
        assert statuses.count(429) == 3

    def test_user_policy_falls_back_to_ip_when_anonymous(self) -> None:
        """``key="user"`` with no auth degrades to IP + logs a warning.

        The fallback is a safety net: unauthenticated requests
        still get bucketed (by IP) rather than bypassing the guard.
        """
        entered = ThreadingEvent()
        release = ThreadingEvent()

        @post(
            "/t",
            opt=per_op_concurrency(
                "test.user_fallback",
                max_inflight=2,
                key="user",
            ),
        )
        async def handler() -> dict[str, bool]:
            await _signal_entry_and_wait(entered, release)
            return {"ok": True}

        with TestClient(app=_make_test_app(handler)) as client:
            results = _fire_concurrent_posts(
                client,
                "/t",
                5,
                entered=entered,
                release=release,
                holders=2,
            )
        statuses = [r.status_code for r in results]
        assert statuses.count(201) == 2
        assert statuses.count(429) == 3


class TestZeroOverrideAuditLog:
    """Disabling an operation via override still lets requests through.

    End-to-end behavioural check: when an operator sets an override of
    ``0`` on an inflight-guarded operation, all concurrent requests must
    succeed (the guard is opt-out, not a silent cap of ``max=0``).  The
    middleware separately logs an ``API_GUARD_DENIED`` WARNING with a
    note so the deliberately-uncapped state surfaces in audit logs,
    verified by reading the logs manually; structlog processors bypass
    pytest's ``caplog`` unless configured, so the log emission itself
    is not asserted here.  See ``inflight_middleware.py`` for the log
    call site.
    """

    def test_override_zero_passes_all_requests_through(self) -> None:
        # Override of 0 disables enforcement: no need to hold permits,
        # every request should succeed immediately.
        @post(
            "/t",
            opt=per_op_concurrency("test.auditable", max_inflight=1, key="ip"),
        )
        async def handler() -> dict[str, bool]:
            return {"ok": True}

        cfg = PerOpConcurrencyConfig(overrides={"test.auditable": 0})
        with TestClient(app=_make_test_app(handler, config=cfg)) as client:
            results = _fire_concurrent_posts(client, "/t", 5)
        # All five must succeed -- override=0 disables the cap entirely.
        for resp in results:
            assert resp.status_code == 201
