"""Tests for provider health endpoint."""

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from typing import Final
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.tracker import CostTracker
from synthorg.config.schema import ProviderConfig, ProviderModelConfig, RootConfig
from synthorg.providers.enums import AuthType
from synthorg.providers.errors import AuthenticationError
from synthorg.providers.health import ProviderHealthRecord
from synthorg.providers.health_tracker import ProviderHealthTracker
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import (
    FakeMessageBus,
    FakePersistenceBackend,
    make_auth_headers,
)

_NOW = datetime.now(UTC)
_HEADERS = make_auth_headers("ceo")


def _make_health_record(
    *,
    provider_name: str = "test-provider",
    timestamp: datetime | None = None,
    success: bool = True,
    response_time_ms: float = 100.0,
    error_message: str | None = None,
) -> ProviderHealthRecord:
    return ProviderHealthRecord(
        provider_name=provider_name,
        timestamp=timestamp or _NOW,
        success=success,
        response_time_ms=response_time_ms,
        error_message=error_message,
    )


_ACOMPLETION = "synthorg.providers.drivers.litellm_driver._litellm.acompletion"

#: Recheck budget the timeout test configures, and how much longer than it
#: the round trip may take. The tolerance covers request plumbing and a
#: loaded CI worker, and is still far below any driver-level timeout, which
#: is the alternative the assertion has to be able to tell it apart from.
_RECHECK_BUDGET_SECONDS: Final[float] = 0.1
_TIMEOUT_TOLERANCE_SECONDS: Final[float] = 5.0
_RECHECK_BUDGET_SETTING: Final[str] = (
    "/api/v1/settings/api/health_recheck_timeout_seconds"
)


def _provider(name: str) -> ProviderConfig:
    """A configured provider the recheck endpoints can actually call.

    ``AuthType.NONE`` because the default is ``API_KEY``, which fails closed
    against this fixture's unresolvable connection: every probe would then
    error on the credential before reaching the driver, and a test asserting
    on the verdict would be measuring the fixture rather than the endpoint.

    The model id carries the provider's name because a fake ``acompletion``
    sees no ``api_base`` and so has the model reference as its only handle on
    who is calling. Sharing one id across providers left call order as the
    only way to single one out, which is not a property any test should rest
    on.

    Returns:
        A single-model provider config keyed under *name* by the caller.
    """
    return ProviderConfig(
        auth_type=AuthType.NONE,
        driver="litellm",
        models=(ProviderModelConfig(id=f"{name}-small-001", alias="small"),),
    )


@asynccontextmanager
async def _recheck_budget(
    client: LoopAsyncClient, *, seconds: float
) -> AsyncIterator[None]:
    """Shrink the recheck ceiling for the body, then put it back.

    The settings row outlives the client that wrote it: the persistence
    fake is session-scoped, so a budget left at a fraction of a second is
    inherited by every later test in the worker. A provider that answers
    in the time a normal probe takes then times out, is dropped from the
    sweep, and the test that reads the sweep fails for a reason that has
    nothing to do with what it is testing.
    """
    resp = await client.put(
        _RECHECK_BUDGET_SETTING,
        json={"value": str(seconds)},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    try:
        yield
    finally:
        # DELETE restores the registered default rather than a value copied
        # into the test, which would be a second place to update.
        restored = await client.delete(_RECHECK_BUDGET_SETTING, headers=_HEADERS)
        assert restored.status_code in {200, 204}


def _completion_response() -> MagicMock:
    """A litellm ``acompletion`` return value standing in for a real reply."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = "pong"
    response.choices[0].finish_reason = "stop"
    response.usage = MagicMock()
    response.usage.prompt_tokens = 1
    response.usage.completion_tokens = 1
    response.id = "test-id"
    return response


@contextmanager
def _answers() -> Iterator[None]:
    """Stand in for every provider completing its probe."""
    with patch(
        _ACOMPLETION, new_callable=AsyncMock, return_value=_completion_response()
    ):
        yield


def _build_provider_client(
    *,
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
    provider_health_tracker: ProviderHealthTracker | None = None,
    cost_tracker: CostTracker | None = None,
    providers: tuple[str, ...] = ("test-provider",),
) -> LoopAsyncClient:
    """Build a LoopAsyncClient with *providers* configured."""
    from synthorg.api.auth.service import AuthService
    from tests._shared import build_test_app as create_app
    from tests.unit.api.conftest import _make_test_auth_service, _seed_test_users

    config = RootConfig(
        company_name="test",
        providers={name: _provider(name) for name in providers},
    )
    auth_service: AuthService = _make_test_auth_service()
    _seed_test_users(fake_persistence, auth_service)
    settings_service = SettingsService(
        repository=fake_persistence.settings,
        registry=get_registry(),
    )
    app = create_app(
        config=config,
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=cost_tracker or CostTracker(),
        auth_service=auth_service,
        settings_service=settings_service,
        provider_health_tracker=provider_health_tracker or ProviderHealthTracker(),
    )
    return LoopAsyncClient(app)


@pytest.mark.unit
class TestProviderHealthRecheck:
    """Health can be re-derived on demand, not only by re-saving a provider.

    The read endpoint replays what was recorded, so a provider whose fault an
    operator has just fixed keeps reporting it. Recheck is the control that
    calls the provider again and reports what that call found.
    """

    async def test_a_recheck_replaces_a_stale_verdict(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        # The whole point: a provider recorded DOWN, whose fault the operator
        # has since fixed, stops reading DOWN without re-saving it.
        #
        # One failure against one success sits exactly on the DOWN threshold,
        # so a single fresh success is enough to cross it. A deeper hole would
        # only prove the counters moved: the verdict is an aggregate over the
        # 24h window, and no one call can outvote a window full of failures.
        tracker = ProviderHealthTracker()
        await tracker.record(
            _make_health_record(success=False, error_message="refused")
        )
        await tracker.record(_make_health_record(success=True))

        async with _build_provider_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
            provider_health_tracker=tracker,
        ) as client:
            before = await client.get(
                "/api/v1/providers/test-provider/health", headers=_HEADERS
            )
            assert before.json()["data"]["health_status"] == "down"

            with _answers():
                resp = await client.post(
                    "/api/v1/providers/test-provider/health/recheck",
                    headers=_HEADERS,
                )

            assert resp.status_code == 201
            data = resp.json()["data"]
            # The fresh call is counted: three calls now, and the 24h error
            # rate falls from 50%. Only a real call can move either, which
            # is the whole point of the endpoint.
            assert data["calls_last_24h"] == 3
            assert data["error_rate_percent_24h"] == pytest.approx(33.33, abs=0.01)
            # The verdict itself, not only the counters it derives from: a
            # result still reading "down" would satisfy both of those and
            # leave the operator exactly where they started.
            assert data["health_status"] == "degraded"

    async def test_a_recheck_on_a_working_provider_reads_healthy(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        # Nothing recorded yet, so the verdict comes entirely from the call
        # this endpoint just made.
        async with _build_provider_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
        ) as client:
            with _answers():
                resp = await client.post(
                    "/api/v1/providers/test-provider/health/recheck",
                    headers=_HEADERS,
                )

            data = resp.json()["data"]
            assert data["calls_last_24h"] == 1
            assert data["error_rate_percent_24h"] == 0.0
            assert data["health_status"] == "up"

    async def test_a_failed_recheck_is_recorded_as_a_failure(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        # A provider that still cannot answer must not read healthier for
        # having been asked.
        tracker = ProviderHealthTracker()
        async with _build_provider_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
            provider_health_tracker=tracker,
        ) as client:
            with patch(
                _ACOMPLETION,
                new_callable=AsyncMock,
                side_effect=AuthenticationError("Invalid key"),
            ):
                resp = await client.post(
                    "/api/v1/providers/test-provider/health/recheck",
                    headers=_HEADERS,
                )

            assert resp.status_code == 201
            data = resp.json()["data"]
            assert data["calls_last_24h"] == 1
            assert data["error_rate_percent_24h"] == 100.0
            assert data["health_status"] == "down"

    async def test_a_recheck_whose_call_raises_does_not_500(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        # ``_do_test_connection`` converts provider faults into an
        # unsuccessful response, so reaching this needs the plumbing itself to
        # break. The endpoint must still answer rather than surfacing a 500.
        async with _build_provider_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
        ) as client:
            with patch(
                "synthorg.api.controllers._provider_helpers._call_provider",
                new_callable=AsyncMock,
                side_effect=RuntimeError("plumbing broke"),
            ):
                resp = await client.post(
                    "/api/v1/providers/test-provider/health/recheck",
                    headers=_HEADERS,
                )

            # Surfaced rather than swallowed: returning the summary already on
            # file under a promise of freshness is what this endpoint exists
            # to stop.
            assert resp.status_code >= 500

    async def test_a_hung_provider_does_not_hold_the_request_open(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        # The call is awaited on the request and is a completion rather than a
        # ping, so without a ceiling one unreachable provider holds the
        # response for whatever the driver's own connect timeout happens to be.
        started = asyncio.Event()

        async def _never_answers(**_kwargs: object) -> object:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError  # unreachable; satisfies the return type

        async with _build_provider_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
        ) as client:
            async with _recheck_budget(client, seconds=_RECHECK_BUDGET_SECONDS):
                with patch(_ACOMPLETION, new=_never_answers):
                    start = time.monotonic()
                    resp = await client.post(
                        "/api/v1/providers/test-provider/health/recheck",
                        headers=_HEADERS,
                    )
                    elapsed = time.monotonic() - start

            assert started.is_set()
            # Measured against the configured budget, not merely against the
            # suite timeout: an endpoint that ignored the setting and returned
            # on the driver's own (much longer) timeout would still finish
            # inside the suite and pass on the status code alone. The ceiling
            # is loose because it covers request plumbing as well as the wait.
            assert elapsed < _RECHECK_BUDGET_SECONDS + _TIMEOUT_TOLERANCE_SECONDS
            # Bounded rather than hung: the budget elapsed and the request
            # came back instead of waiting out the driver.
            assert resp.status_code == 504

    async def test_recheck_of_an_unknown_provider_is_404(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        async with _build_provider_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
        ) as client:
            resp = await client.post(
                "/api/v1/providers/nonexistent/health/recheck",
                headers=_HEADERS,
            )

            assert resp.status_code == 404

    async def test_recheck_requires_auth(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/providers/test-provider/health/recheck",
            headers={"Authorization": "Bearer invalid"},
        )

        assert resp.status_code == 401

    async def test_recheck_all_reports_every_provider(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        # The Overview control asks this one question rather than making an
        # operator open each provider in turn.
        async with _build_provider_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
            providers=("provider-one", "provider-two"),
        ) as client:
            with _answers():
                resp = await client.post(
                    "/api/v1/providers/health/recheck",
                    headers=_HEADERS,
                )

            assert resp.status_code == 201
            data = resp.json()["data"]
            assert set(data) == {"provider-one", "provider-two"}
            assert data["provider-one"]["health_status"] == "up"
            assert data["provider-two"]["health_status"] == "up"

    async def test_one_provider_failing_does_not_lose_the_others(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        # The containment the sweep promises: a bare TaskGroup member would
        # cancel its siblings, discarding verdicts already paid for.

        async def _one_provider_explodes(*_args: object, **kwargs: object) -> object:
            # Pinned to a provider, not to whichever call happens to be
            # first, and raising on every attempt: keyed off call order the
            # outcome turned on whether a retry rescued that first attempt,
            # so the assertion was reading the retry ladder rather than the
            # sweep's containment.
            if "provider-one" in str(kwargs.get("model")):
                msg = "this provider's plumbing broke"
                raise RuntimeError(msg)
            return _completion_response()

        async with _build_provider_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
            providers=("provider-one", "provider-two"),
        ) as client:
            with patch(_ACOMPLETION, new=_one_provider_explodes):
                resp = await client.post(
                    "/api/v1/providers/health/recheck",
                    headers=_HEADERS,
                )

            assert resp.status_code == 201
            # Both still reported: a probe that fails is a verdict, not an
            # error, so the provider that raised carries a fresh unhealthy
            # reading rather than taking the sweep down with it.
            data = resp.json()["data"]
            assert set(data) == {"provider-one", "provider-two"}
            assert data["provider-two"]["health_status"] == "up"
            assert data["provider-one"]["health_status"] != "up"


@pytest.mark.unit
class TestProviderHealth:
    async def test_provider_not_found(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get("/api/v1/providers/nonexistent/health")
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    async def test_auth_required(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get(
            "/api/v1/providers/test-provider/health",
            headers={"Authorization": "Bearer invalid"},
        )
        assert resp.status_code == 401

    async def test_empty_health(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Provider exists but no health records."""
        async with _build_provider_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
        ) as client:
            resp = await client.get(
                "/api/v1/providers/test-provider/health",
                headers=_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["health_status"] == "unknown"
            assert data["last_check_timestamp"] is None
            assert data["avg_response_time_ms"] is None
            assert data["error_rate_percent_24h"] == 0.0
            assert data["calls_last_24h"] == 0

    async def test_healthy_provider(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Provider with all successful calls."""
        tracker = ProviderHealthTracker()
        for i in range(5):
            await tracker.record(
                _make_health_record(
                    timestamp=_NOW - timedelta(minutes=i),
                    response_time_ms=100.0 + i * 10,
                ),
            )
        async with _build_provider_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
            provider_health_tracker=tracker,
        ) as client:
            resp = await client.get(
                "/api/v1/providers/test-provider/health",
                headers=_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["health_status"] == "up"
            assert data["calls_last_24h"] == 5
            assert data["error_rate_percent_24h"] == 0.0
            assert data["avg_response_time_ms"] is not None

    async def test_degraded_provider(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Provider with 20% error rate -> degraded."""
        tracker = ProviderHealthTracker()
        for i in range(10):
            ok = i >= 2  # 2 failures out of 10
            await tracker.record(
                _make_health_record(
                    timestamp=_NOW - timedelta(minutes=i),
                    success=ok,
                    error_message=None if ok else "test error",
                ),
            )
        async with _build_provider_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
            provider_health_tracker=tracker,
        ) as client:
            resp = await client.get(
                "/api/v1/providers/test-provider/health",
                headers=_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["health_status"] == "degraded"
            assert data["error_rate_percent_24h"] == 20.0

    async def test_down_provider(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Provider with 100% error rate -> down."""
        tracker = ProviderHealthTracker()
        for i in range(3):
            await tracker.record(
                _make_health_record(
                    timestamp=_NOW - timedelta(minutes=i),
                    success=False,
                    error_message="test error",
                ),
            )
        async with _build_provider_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
            provider_health_tracker=tracker,
        ) as client:
            resp = await client.get(
                "/api/v1/providers/test-provider/health",
                headers=_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["health_status"] == "down"
            assert data["error_rate_percent_24h"] == 100.0


@pytest.mark.unit
class TestProviderHealthUsageEnrichment:
    """Tests for cost/token enrichment of the health endpoint."""

    async def test_health_includes_zero_usage_when_no_cost_records(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Usage fields present and zero when no cost records exist."""
        async with _build_provider_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
        ) as client:
            resp = await client.get(
                "/api/v1/providers/test-provider/health",
                headers=_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["total_tokens_24h"] == 0
            assert data["total_cost_24h"] == 0.0

    async def test_health_includes_usage_from_cost_tracker(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Usage fields reflect token/cost totals from CostTracker."""
        tracker = CostTracker()
        await tracker.record(
            CostRecord(
                agent_id="alice",
                task_id="task-1",
                provider="test-provider",
                model="test-small-001",
                input_tokens=3000,
                output_tokens=1000,
                cost=0.25,
                currency="EUR",
                timestamp=_NOW - timedelta(minutes=5),
            ),
        )
        await tracker.record(
            CostRecord(
                agent_id="bob",
                task_id="task-2",
                provider="test-provider",
                model="test-small-001",
                input_tokens=2000,
                output_tokens=500,
                cost=0.15,
                currency="EUR",
                timestamp=_NOW - timedelta(minutes=10),
            ),
        )
        async with _build_provider_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
            cost_tracker=tracker,
        ) as client:
            resp = await client.get(
                "/api/v1/providers/test-provider/health",
                headers=_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["total_tokens_24h"] == 6500  # 3000+1000+2000+500
            assert data["total_cost_24h"] == pytest.approx(0.40, abs=1e-9)

    async def test_health_excludes_other_provider_costs(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Costs from other providers are not included."""
        tracker = CostTracker()
        await tracker.record(
            CostRecord(
                agent_id="alice",
                task_id="task-1",
                provider="test-provider",
                model="test-small-001",
                input_tokens=1000,
                output_tokens=500,
                cost=0.10,
                currency="EUR",
                timestamp=_NOW - timedelta(minutes=5),
            ),
        )
        await tracker.record(
            CostRecord(
                agent_id="alice",
                task_id="task-2",
                provider="other-provider",
                model="other-model",
                input_tokens=9000,
                output_tokens=9000,
                cost=9.99,
                currency="EUR",
                timestamp=_NOW - timedelta(minutes=5),
            ),
        )
        async with _build_provider_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
            cost_tracker=tracker,
        ) as client:
            resp = await client.get(
                "/api/v1/providers/test-provider/health",
                headers=_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["total_tokens_24h"] == 1500
            assert data["total_cost_24h"] == 0.10

    async def test_health_graceful_degradation_on_cost_tracker_error(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Endpoint returns 200 with zero usage when CostTracker raises."""
        from unittest.mock import AsyncMock, patch

        tracker = CostTracker()
        with patch.object(
            tracker,
            "get_provider_usage",
            new=AsyncMock(
                spec=tracker.get_provider_usage,
                side_effect=RuntimeError("cost tracker broken"),
            ),
        ):
            async with _build_provider_client(
                fake_persistence=fake_persistence,
                fake_message_bus=fake_message_bus,
                cost_tracker=tracker,
            ) as client:
                resp = await client.get(
                    "/api/v1/providers/test-provider/health",
                    headers=_HEADERS,
                )
                assert resp.status_code == 200
                data = resp.json()["data"]
                assert data["total_tokens_24h"] == 0
                assert data["total_cost_24h"] == 0.0
