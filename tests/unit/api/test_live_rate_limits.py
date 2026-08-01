"""Tests for globally rate-limiting against a cap that can change live.

The point of the class under test is that the cap is read per request
rather than fixed when Litestar built the app, so these assert the cap
actually moves and that the tiers stay coherent while it does.
"""

import pytest
from pydantic import ValidationError

from synthorg.api.rate_limits.live_global import (
    LiveRateLimitConfig,
    LiveRateLimitMiddleware,
    RateLimitTier,
    _cap_for,
)
from synthorg.config.rate_limits import LiveRateLimits


def _limits(**overrides: object) -> LiveRateLimits:
    base: dict[str, object] = {
        "floor_max_requests": 1000,
        "unauth_max_requests": 20,
        "auth_max_requests": 600,
        "auth_endpoint_max_requests": 10,
    }
    return LiveRateLimits(**(base | overrides))  # type: ignore[arg-type]


@pytest.mark.unit
class TestTierSelection:
    """Each middleware instance enforces its own tier's cap."""

    @pytest.mark.parametrize(
        ("tier", "expected"),
        [
            (RateLimitTier.FLOOR, 1000),
            (RateLimitTier.UNAUTH, 20),
            (RateLimitTier.AUTH, 600),
            (RateLimitTier.AUTH_ENDPOINT, 10),
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
        limits = _limits(floor_max_requests=600, auth_max_requests=600)
        assert limits.floor_max_requests == limits.auth_max_requests

    def test_raising_one_tier_past_the_floor_is_refused(self) -> None:
        # A live edit moves one key at a time, which is precisely where
        # the pair drifts apart. The subscriber rebuilds the whole model
        # from current values, so raising the auth cap alone is caught
        # here rather than quietly enforcing the old floor.
        settled = _limits()
        with pytest.raises(ValidationError):
            LiveRateLimits(
                **(settled.model_dump() | {"auth_max_requests": 5_000}),
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
