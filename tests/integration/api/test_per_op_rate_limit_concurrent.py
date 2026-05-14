"""Integration test: 100 concurrent mutations exercise the per-op guard.

Builds a full Litestar app with the per-operation rate-limit
middleware wired, authenticates as admin,
then fires 100 concurrent POSTs to a guarded endpoint via a
``ThreadPoolExecutor`` (the sync ``TestClient`` drives parallel
requests through the ASGI transport).  The first ``max_requests``
requests must return the success code; the remainder must return
HTTP 429 with an RFC 9457 envelope carrying
``error_category=rate_limit`` / ``error_code=5001`` / ``retryable=True``
and a ``Retry-After`` header.

Also exercises the :class:`PerOpConcurrencyMiddleware` by tightening
the inflight override for ``memory.fine_tune_preflight`` (a lightweight
endpoint that does not require a fine-tune orchestrator) to
``max_inflight=1`` and firing 5 concurrent POSTs; exactly one must
succeed and four must return 429 with ``error_code=5002``.
"""

from collections.abc import AsyncGenerator, Generator
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Event as ThreadingEvent
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litestar.testing import TestClient

import synthorg.settings.definitions  # noqa: F401 -- trigger registration
from synthorg.api.app import create_app
from synthorg.api.auth.service import AuthService
from synthorg.api.rate_limits.inflight_config import PerOpConcurrencyConfig
from synthorg.budget.tracker import CostTracker
from synthorg.config.schema import RootConfig
from synthorg.hr.registry import AgentRegistryService
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests.unit.api.fakes import FakeMessageBus, FakePersistenceBackend

pytestmark = pytest.mark.integration

_TEST_JWT_SECRET = "integration-test-secret-at-least-32-characters"
_TEST_SETTINGS_KEY = "lKzZcMznksIF8A_2HFFUnKxhxhz9_bxTvVJoZ6mvZrk="
_TEST_USERNAME = "admin"
_TEST_PASSWORD = "secure-pass-12chars"
# Match the decorator default for ``agents.create`` in the controller
# -- the whole point of this test is to confirm the decorator's
# ``max_requests=10`` fires after 10 successes.
_AGENTS_CREATE_MAX_REQUESTS = 10
_HOLD_TIMEOUT_SECONDS = 5.0


@pytest.fixture(autouse=True)
def _required_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required env vars for the API backend."""
    monkeypatch.setenv("SYNTHORG_JWT_SECRET", _TEST_JWT_SECRET)
    monkeypatch.setenv("SYNTHORG_SETTINGS_KEY", _TEST_SETTINGS_KEY)


@pytest.fixture
async def fake_persistence() -> AsyncGenerator[FakePersistenceBackend]:
    backend = FakePersistenceBackend()
    await backend.connect()
    yield backend
    await backend.disconnect()


@pytest.fixture
async def fake_message_bus() -> AsyncGenerator[FakeMessageBus]:
    bus = FakeMessageBus()
    await bus.start()
    yield bus
    await bus.stop()


def _build_app(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
    *,
    concurrency_overrides: dict[str, int] | None = None,
) -> Any:
    """Build the app and authenticate as admin, returning a primed client."""
    root_config = RootConfig(company_name="rate-limit-test-co")
    if concurrency_overrides is not None:
        root_config = root_config.model_copy(
            update={
                "api": root_config.api.model_copy(
                    update={
                        "per_op_concurrency": PerOpConcurrencyConfig(
                            overrides=concurrency_overrides,
                        ),
                    },
                ),
            },
        )
    auth_service = AuthService(
        root_config.api.auth.model_copy(
            update={"jwt_secret": _TEST_JWT_SECRET},
        ),
    )
    agent_registry = AgentRegistryService()
    settings_service = SettingsService(
        repository=fake_persistence.settings,
        registry=get_registry(),
    )
    stub_provider = MagicMock(spec=BaseCompletionProvider)
    provider_registry = ProviderRegistry({"test-provider": stub_provider})
    mock_model = MagicMock()
    mock_model.id = "test-small-001"
    mock_model.alias = None
    mock_provider_config = MagicMock()
    mock_provider_config.models = [mock_model]
    app = create_app(
        config=root_config,
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=CostTracker(),
        auth_service=auth_service,
        agent_registry=agent_registry,
        settings_service=settings_service,
        provider_registry=provider_registry,
    )
    # Wire mock provider management so agent creation validates.
    app_state = app.state["app_state"]
    mock_mgmt = MagicMock()
    mock_mgmt.list_providers = AsyncMock(
        return_value={"test-provider": mock_provider_config},
    )
    app_state._provider_management = mock_mgmt
    return app


def _extract_auth_cookies(resp: Any) -> tuple[str, str]:
    session = ""
    csrf = ""
    for k, v in resp.headers.multi_items():
        if k != "set-cookie":
            continue
        if v.startswith("session="):
            session = v.split("session=")[1].split(";")[0]
        elif v.startswith("csrf_token="):
            csrf = v.split("csrf_token=")[1].split(";")[0]
    return session, csrf


@pytest.fixture
def authed_client(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> Generator[TestClient[Any]]:
    """A TestClient that has completed the admin setup + login flow."""
    app = _build_app(fake_persistence, fake_message_bus)
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/auth/setup",
            json={"username": _TEST_USERNAME, "password": _TEST_PASSWORD},
        )
        assert resp.status_code == 201, resp.text
        session_token, csrf_token = _extract_auth_cookies(resp)
        # Prime cookies + CSRF for all subsequent requests.
        client.headers["Cookie"] = f"session={session_token}; csrf_token={csrf_token}"
        client.headers["X-CSRF-Token"] = csrf_token
        yield client


class TestConcurrentBurstAgainstAgentsCreate:
    """100 concurrent POSTs to agents.create exhaust the 10/60s bucket."""

    def test_fires_100_concurrent_mutations_and_asserts_429s(
        self,
        authed_client: TestClient[Any],
    ) -> None:
        # The agent-create payload requires a valid shape; distinct names
        # avoid the 409-conflict path, so every 2xx response is unambiguously
        # a success and every 429 is unambiguously a rate-limit denial.
        def _payload(i: int) -> dict[str, Any]:
            return {
                "name": f"agent-{i:03d}",
                "role": "PAIR_PROGRAMMER",
                "department": "Engineering",
                "model_provider": "test-provider",
                "model_id": "test-small-001",
            }

        concurrency = 100
        # ``max_workers`` must match the submission count so all 100
        # requests actually race for the permit at the same time.  A
        # smaller pool would queue 68 of them and turn the
        # 100-way-concurrent-burst contract into a serialised walk
        # through the bucket, weakening the race the guard is
        # supposed to handle.
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [
                pool.submit(authed_client.post, "/api/v1/agents/", json=_payload(i))
                for i in range(concurrency)
            ]
            responses = [f.result() for f in futures]

        rate_limit_denials = [r for r in responses if r.status_code == 429]
        non_throttled = [r for r in responses if r.status_code != 429]
        statuses = [r.status_code for r in responses]
        status_summary = {s: statuses.count(s) for s in set(statuses)}
        # Strict equality: the sliding-window guard must admit
        # exactly ``max_requests`` requests per window and deny the
        # remainder.  The original assertions used ``<=`` / ``>=``
        # slack to tolerate thread-pool timing jitter, but with
        # ``max_workers=concurrency`` the race is large enough that
        # all 100 contend in a single window -- anything other than
        # an exact ``max_requests`` / ``concurrency - max_requests``
        # split is a real regression worth failing on.
        assert len(non_throttled) == _AGENTS_CREATE_MAX_REQUESTS, (
            f"Expected exactly {_AGENTS_CREATE_MAX_REQUESTS} non-throttled "
            f"responses, got {len(non_throttled)}; "
            f"status distribution: {status_summary}"
        )
        assert len(rate_limit_denials) == concurrency - _AGENTS_CREATE_MAX_REQUESTS, (
            f"Expected exactly {concurrency - _AGENTS_CREATE_MAX_REQUESTS} "
            f"rate-limit denials, got {len(rate_limit_denials)}; "
            f"status distribution: {status_summary}"
        )
        # Every 429 must carry the RFC 9457 rate-limit envelope.
        for resp in rate_limit_denials:
            body = resp.json()
            assert body["success"] is False
            assert body["error_detail"]["error_category"] == "rate_limit"
            assert body["error_detail"]["error_code"] == 5001
            assert body["error_detail"]["retryable"] is True
            assert "Retry-After" in resp.headers
            assert int(resp.headers["Retry-After"]) >= 1


class TestRateLimitMetaTriggerCycle:
    """``meta.trigger_cycle`` is the tightest registered policy at (1, 60).

    A regression in the sliding-window middleware that loosened the cap
    here would silently let operators DOS the improvement-cycle compute.
    Fire two sequential POSTs and assert the second one is denied.
    """

    def test_second_request_is_denied(
        self,
        authed_client: TestClient[Any],
    ) -> None:
        first = authed_client.post("/api/v1/meta/cycle")
        # The first call should pass the guard; whether the body returns
        # 200 or some downstream error (e.g. cycle service not wired in
        # the test app) is not what this test cares about -- what matters
        # is that the GUARD admits the first call and denies the second.
        assert first.status_code != 429, (
            f"First call hit the rate limiter unexpectedly: {first.status_code} "
            f"{first.text}"
        )

        second = authed_client.post("/api/v1/meta/cycle")
        assert second.status_code == 429, (
            f"Expected 429 on the second call within the 60 s window, got "
            f"{second.status_code} {second.text}"
        )
        body = second.json()
        assert body["success"] is False
        assert body["error_detail"]["error_category"] == "rate_limit"
        assert body["error_detail"]["error_code"] == 5001
        assert body["error_detail"]["retryable"] is True
        assert "Retry-After" in second.headers
        assert int(second.headers["Retry-After"]) >= 1


class TestConcurrencyGuardAgainstFinetunePreflight:
    """5 concurrent POSTs to a concurrency-guarded op yield 1 ok + 4 denied."""

    def test_inflight_cap_fires_5002(  # noqa: C901 -- barrier orchestration
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        # Override: tighten ``memory.fine_tune_preflight`` to max_inflight=1
        # (the endpoint does not declare per_op_concurrency by default
        # because preflight is cheap; we inject the inflight cap here to
        # exercise the middleware end-to-end without spinning up a real
        # fine-tune orchestrator).
        app = _build_app(
            fake_persistence,
            fake_message_bus,
            concurrency_overrides={"memory.fine_tune_preflight": 1},
        )
        # Shared barrier: the first request enters the preflight checks
        # (which run on the asyncio thread pool), blocks on ``release``,
        # and holds the permit while the other four hit the middleware
        # and are denied with 5002.  Patching the inner sync helper is
        # the least invasive hook -- the handler stays untouched and
        # ``threading.Event`` is natural for code running on a worker
        # thread via ``asyncio.to_thread``.
        entered = ThreadingEvent()
        release = ThreadingEvent()

        def _held_preflight_checks(*_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
            # ``to_thread`` runs this synchronously on a worker, so
            # ``threading.Event`` is the right primitive here.
            # Signal entry (the holder has the permit), then park on
            # ``release`` until the test releases explicitly.  Both
            # events carry a bounded timeout so a wiring bug never
            # wedges the suite.
            entered.set()
            release.wait(_HOLD_TIMEOUT_SECONDS)
            return ()

        def _held_batch_size(*_args: Any, **_kwargs: Any) -> int:
            # Accept and ignore any kwargs the production helper grows
            # (currently ``default_batch_size``) so the patch stays
            # signature-tolerant across controller changes.
            return 1

        # The handler invokes two helpers via ``asyncio.to_thread``;
        # patch both so the overall ``TaskGroup`` blocks deterministically.
        from synthorg.api.controllers import (
            memory as _memory_mod,
        )

        # Monkey-patch the inflight opt so the middleware actually
        # inspects this route.  The opt is a plain dict and settable.
        found = False
        for route in app.routes:
            for handler in getattr(route, "route_handlers", []) or []:
                if getattr(handler, "handler_name", "") == "run_preflight":
                    opt = dict(handler.opt or {})
                    opt["per_op_concurrency"] = (
                        "memory.fine_tune_preflight",
                        1,
                        "user",
                    )
                    handler.opt = opt
                    found = True
        assert found, "run_preflight handler not found on the app"

        with (
            patch.object(
                _memory_mod,
                "_run_preflight_checks",
                _held_preflight_checks,
            ),
            patch.object(
                _memory_mod,
                "_recommend_batch_size",
                _held_batch_size,
            ),
            TestClient(app) as client,
        ):
            resp = client.post(
                "/api/v1/auth/setup",
                json={"username": _TEST_USERNAME, "password": _TEST_PASSWORD},
            )
            assert resp.status_code == 201, resp.text
            session_token, csrf_token = _extract_auth_cookies(resp)
            client.headers["Cookie"] = (
                f"session={session_token}; csrf_token={csrf_token}"
            )
            client.headers["X-CSRF-Token"] = csrf_token

            payload = {
                "source_dir": "/tmp/fake-source",  # noqa: S108 -- test payload; never written
                "base_model": "test-small-001",
            }

            def _fire() -> Any:
                return client.post(
                    "/api/v1/admin/memory/fine-tune/preflight",
                    json=payload,
                )

            concurrency = 5
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [pool.submit(_fire) for _ in range(concurrency)]
                # Deterministic handshake:
                #   1. Wait for the holder to enter the patched body.
                #   2. Wait for ``concurrency - 1`` siblings to
                #      complete -- they must be the 429 denials
                #      because the holder is still parked.
                #   3. Release the holder so the last future resolves.
                if not entered.wait(_HOLD_TIMEOUT_SECONDS):
                    msg = "Handler never signalled 'entered' within timeout"
                    raise AssertionError(msg)
                pending = set(futures)
                denied_seen = 0
                while denied_seen < concurrency - 1 and pending:
                    done, pending = wait(
                        pending,
                        timeout=_HOLD_TIMEOUT_SECONDS,
                        return_when=FIRST_COMPLETED,
                    )
                    if not done:
                        # No progress within the budget -- either the
                        # middleware never denied a sibling, or the
                        # TestClient transport wedged.  Fail fast so
                        # the diagnostic points at this invariant
                        # rather than a generic pytest timeout.
                        release.set()
                        msg = (
                            "wait() timed out without a completed "
                            f"future (denied_seen={denied_seen}, "
                            f"expected_denials={concurrency - 1})"
                        )
                        raise AssertionError(msg)
                    denied_seen += len(done)
                release.set()
                responses = [f.result() for f in futures]

        # Litestar defaults POST success to 201.
        successes = [r for r in responses if r.status_code == 201]
        concurrency_denials = [r for r in responses if r.status_code == 429]
        # Exact split: one holder + (concurrency - 1) denials.  Any
        # deviation means the middleware did not actually cap at 1
        # inflight and is a regression worth failing on.
        assert len(successes) == 1, (
            f"Expected exactly 1 success with max_inflight=1, got "
            f"{len(successes)}; statuses: {[r.status_code for r in responses]}"
        )
        assert len(concurrency_denials) == concurrency - 1, (
            f"Expected {concurrency - 1} concurrency denials, got "
            f"{len(concurrency_denials)}; statuses: "
            f"{[r.status_code for r in responses]}"
        )
        for resp in concurrency_denials:
            body = resp.json()
            assert body["error_detail"]["error_code"] == 5002
            assert body["error_detail"]["error_category"] == "rate_limit"


class TestHighTierOpsCarryGuards:
    """Regression guard: HIGH-tier endpoints must keep both guards wired.

    A full-request flow against memory.fine_tune / checkpoint_* /
    providers.pull_model / providers.discover_models requires a live
    fine-tune orchestrator and provider backend the integration stub
    doesn't carry -- but the audit wiring itself is verifiable at the
    route-manifest level: every HIGH-tier op must carry a
    ``per_op_rate_limit`` guard AND a ``per_op_concurrency`` opt entry
    with its designated operation string.  This catches the
    regression where someone removes a decorator without realising
    both guards are required for the high-tier surface.
    """

    # Each entry: (handler_name, rate_limit_op, concurrency_op).
    # ``memory.fine_tune`` is intentionally the SHARED inflight
    # bucket key for both ``start_fine_tune`` and ``resume_fine_tune``
    # so operators cannot bypass the cap by starting then resuming a
    # separate flow.  The sliding-window guards use DISTINCT operation
    # names (``memory.fine_tune`` vs ``memory.fine_tune_resume``) so
    # start and resume rates can be tuned independently.  Keep both
    # entries here so a refactor that accidentally decouples the
    # inflight bucket (or collapses the sliding-window ops) trips
    # this regression test.
    _HIGH_TIER_OPS: tuple[tuple[str, str, str], ...] = (
        ("start_fine_tune", "memory.fine_tune", "memory.fine_tune"),
        ("resume_fine_tune", "memory.fine_tune_resume", "memory.fine_tune"),
        ("deploy_checkpoint", "memory.checkpoint_deploy", "memory.checkpoint_deploy"),
        (
            "rollback_checkpoint",
            "memory.checkpoint_rollback",
            "memory.checkpoint_rollback",
        ),
        ("pull_model", "providers.pull_model", "providers.pull_model"),
        (
            "discover_models",
            "providers.discover_models",
            "providers.discover_models",
        ),
    )

    def test_high_tier_ops_have_both_guards_wired(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        app = _build_app(fake_persistence, fake_message_bus)
        # Walk every handler once so the assertion survives route
        # reorganisations (new routers, nested controllers, etc.).
        handler_by_name: dict[str, Any] = {}
        for route in app.routes:
            for handler in getattr(route, "route_handlers", []) or []:
                handler_by_name[getattr(handler, "handler_name", "")] = handler

        missing: list[str] = []
        for handler_name, rl_op, inflight_op in self._HIGH_TIER_OPS:
            handler = handler_by_name.get(handler_name)
            if handler is None:
                missing.append(f"{handler_name}: handler not found")
                continue
            guards = getattr(handler, "guards", None) or ()
            guard_names = tuple(getattr(g, "__name__", "") for g in guards)
            if not any(
                name.startswith(f"per_op_rate_limit[{rl_op}]") for name in guard_names
            ):
                missing.append(
                    f"{handler_name}: per_op_rate_limit[{rl_op}] guard missing "
                    f"(found guards: {guard_names})"
                )
            opt = getattr(handler, "opt", None) or {}
            inflight_opt = opt.get("per_op_concurrency")
            if inflight_opt is None:
                missing.append(
                    f"{handler_name}: per_op_concurrency opt missing "
                    f"(opt keys: {list(opt)})"
                )
                continue
            if (
                not isinstance(inflight_opt, (tuple, list))
                or len(inflight_opt) != 3
                or inflight_opt[0] != inflight_op
            ):
                missing.append(
                    f"{handler_name}: per_op_concurrency opt malformed "
                    f"(expected operation={inflight_op!r}, got {inflight_opt!r})"
                )
        assert not missing, "HIGH-tier guard wiring regression:\n" + "\n".join(missing)
