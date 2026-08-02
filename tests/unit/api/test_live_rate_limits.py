"""Tests for globally rate-limiting against a cap that can change live.

The point of the class under test is that the cap is read per request
rather than fixed when Litestar built the app, so these assert the cap
actually moves and that the tiers stay coherent while it does.
"""

from contextlib import AbstractContextManager

import litestar
import pytest
from litestar import Litestar, get
from litestar.datastructures import State
from litestar.testing import TestClient, create_test_client
from pydantic import ValidationError

from synthorg.api.rate_limits.live_global import (
    LiveRateLimitConfig,
    LiveRateLimitMiddleware,
    RateLimitTier,
    _cap_for,
)
from synthorg.api.state import AppState
from synthorg.config.rate_limits import LiveRateLimits
from synthorg.config.schema import RootConfig

# ``LiveRateLimitMiddleware.__call__`` mirrors Litestar's dispatch and calls
# inherited helpers by name (``retrieve_cached_history``, ``set_cached_history``,
# ``cache_key_from_request``, ``create_send_wrapper``, ``self.unit``). None of
# those is a documented extension point, so a Litestar upgrade can change one
# without any signal here beyond this pin.
_REVIEWED_LITESTAR_VERSION = "2.24.0"


def _limits(
    *,
    enabled: bool = True,
    floor_max_requests: int = 1000,
    unauth_max_requests: int = 20,
    auth_max_requests: int = 600,
) -> LiveRateLimits:
    return LiveRateLimits(
        enabled=enabled,
        floor_max_requests=floor_max_requests,
        unauth_max_requests=unauth_max_requests,
        auth_max_requests=auth_max_requests,
    )


@pytest.mark.unit
class TestTierSelection:
    """Each middleware instance enforces its own tier's cap."""

    @pytest.mark.parametrize(
        ("tier", "expected"),
        [
            (RateLimitTier.FLOOR, 1000),
            (RateLimitTier.UNAUTH, 20),
            (RateLimitTier.AUTH, 600),
        ],
    )
    def test_each_tier_reads_its_own_cap(
        self, tier: RateLimitTier, expected: int
    ) -> None:
        assert _cap_for(_limits(), tier) == expected

    def test_config_binds_the_live_middleware(self) -> None:
        config = LiveRateLimitConfig(
            rate_limit=("minute", 10),
            tier=RateLimitTier.AUTH,
        )
        # Litestar instantiates middleware_class from the config, so an
        # unbound config would silently fall back to the baked cap.
        assert config.middleware_class is LiveRateLimitMiddleware
        assert config.tier is RateLimitTier.AUTH


@pytest.mark.unit
class TestFloorInvariant:
    """The floor wraps both tiers, so it may never sit below either."""

    def test_a_floor_below_a_tier_is_refused(self) -> None:
        # Accepting this would silently cap the authenticated budget at
        # the floor, well under the value an operator just configured.
        with pytest.raises(ValidationError):
            _limits(floor_max_requests=100, auth_max_requests=600)

    def test_a_floor_equal_to_the_highest_tier_is_allowed(self) -> None:
        assert _limits(floor_max_requests=600, auth_max_requests=600) is not None

    def test_raising_one_tier_past_the_floor_is_refused(self) -> None:
        # A live edit moves one key at a time, which is precisely where
        # the pair drifts apart. The subscriber rebuilds the whole model
        # from current values, so raising the auth cap alone is caught
        # here rather than quietly enforcing the old floor.
        settled = _limits()
        with pytest.raises(ValidationError):
            LiveRateLimits.model_validate(
                settled.model_dump() | {"auth_max_requests": 5_000}
            )


@pytest.mark.unit
class TestDisabling:
    """The switch is read live, and the windows outlive it."""

    def test_disabled_is_carried_on_the_live_config(self) -> None:
        assert _limits(enabled=False).enabled is False

    def test_enabled_defaults_on(self) -> None:
        # A missing value must not read as "limiter off": that would turn
        # a settings-read hiccup into an uncapped API.
        assert _limits().enabled is True


def _client_with_live_limits(
    limits: LiveRateLimits | None,
    baked_cap: int = 10_000,
) -> AbstractContextManager[TestClient[Litestar]]:
    """Build a one-route app whose limiter reads *limits* per request.

    Args:
        limits: The live config to install, or ``None`` for the state
            before the boot snapshot lands.
        baked_cap: What Litestar builds the middleware with. The default
            sits far above every live cap under test, so a passing
            assertion can only come from the per-request read; a test of
            the fallback lowers it so the baked cap is reachable.

    Returns:
        A context manager yielding the configured test client.
    """

    @get("/probe", sync_to_thread=False)
    def _probe() -> str:
        return "ok"

    config = LiveRateLimitConfig(
        rate_limit=("minute", baked_cap),
        tier=RateLimitTier.UNAUTH,
    )
    app_state = AppState(config=RootConfig(company_name="test"))
    if limits is not None:
        app_state.per_op_limits.swap_global_config(limits)
    return create_test_client(
        route_handlers=[_probe],
        middleware=[config.middleware],
        state=State({"app_state": app_state}),
    )


@pytest.mark.unit
class TestLiveDispatch:
    """The mirrored dispatch itself, exercised end to end.

    Everything else here asserts the cap is *selected* correctly. These
    assert it is *enforced*, which is the half that runs Litestar's
    inherited store, window and header helpers.
    """

    def test_the_reviewed_litestar_version_is_the_installed_one(self) -> None:
        installed = (
            f"{litestar.__version__.major}."
            f"{litestar.__version__.minor}."
            f"{litestar.__version__.patch}"
        )
        assert installed == _REVIEWED_LITESTAR_VERSION, (
            "LiveRateLimitMiddleware mirrors Litestar's rate-limit dispatch; "
            "re-read RateLimitMiddleware.__call__ against this version before "
            "moving the pin"
        )

    def test_requests_under_the_live_cap_are_served(self) -> None:
        with _client_with_live_limits(_limits(unauth_max_requests=3)) as client:
            assert [client.get("/probe").status_code for _ in range(3)] == [
                200,
                200,
                200,
            ]

    def test_the_live_cap_is_enforced_not_the_baked_one(self) -> None:
        with _client_with_live_limits(_limits(unauth_max_requests=2)) as client:
            for _ in range(2):
                assert client.get("/probe").status_code == 200
            # The config was built with 10_000, so a 429 here can only come
            # from the per-request read.
            assert client.get("/probe").status_code == 429

    def test_disabled_hands_off_without_limiting(self) -> None:
        limits = _limits(unauth_max_requests=1, enabled=False)
        with _client_with_live_limits(limits) as client:
            assert [client.get("/probe").status_code for _ in range(4)] == [200] * 4

    def test_no_snapshot_falls_back_to_the_built_cap(self) -> None:
        # Before the boot snapshot lands there is no live config. Serving
        # under Litestar's own cap beats serving under none at all, so the
        # baked cap is lowered until it bites: a single 200 would pass just
        # as well against a fallback that skipped limiting altogether.
        with _client_with_live_limits(None, baked_cap=2) as client:
            for _ in range(2):
                assert client.get("/probe").status_code == 200
            assert client.get("/probe").status_code == 429
