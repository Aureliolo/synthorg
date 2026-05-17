"""Unit tests for the per-operation rate limit guard."""

from typing import Any

import pytest
from litestar import Litestar, get
from litestar.datastructures import State
from litestar.testing import TestClient

from synthorg.api.exception_handlers import EXCEPTION_HANDLERS
from synthorg.api.rate_limits.config import PerOpRateLimitConfig
from synthorg.api.rate_limits.guard import per_op_rate_limit
from synthorg.api.rate_limits.in_memory import InMemorySlidingWindowStore
from synthorg.api.rate_limits.policies import (
    RATE_LIMIT_POLICIES,
    per_op_rate_limit_from_policy,
)

pytestmark = pytest.mark.unit


def _make_test_app(
    handler: Any,
    *,
    config: PerOpRateLimitConfig | None = None,
) -> Litestar:
    """Build a Litestar app with the rate-limit store wired into state."""
    store = InMemorySlidingWindowStore()
    cfg = config or PerOpRateLimitConfig()
    return Litestar(
        route_handlers=[handler],
        state=State(
            {
                "per_op_rate_limit_store": store,
                "per_op_rate_limit_config": cfg,
            },
        ),
        exception_handlers=dict(EXCEPTION_HANDLERS),  # type: ignore[arg-type]
    )


class TestGuardThrottling:
    """Over-limit requests return 429 with Retry-After + RFC 9457 body."""

    def test_allows_up_to_limit(self) -> None:
        guard = per_op_rate_limit(
            "test.op",
            max_requests=3,
            window_seconds=60,
            key="ip",
        )

        @get("/t", guards=[guard])
        async def handler() -> dict[str, bool]:
            return {"ok": True}

        with TestClient(_make_test_app(handler)) as client:
            for _ in range(3):
                resp = client.get("/t")
                assert resp.status_code == 200

    def test_rejects_over_limit_with_rfc_9457_body(self) -> None:
        guard = per_op_rate_limit(
            "test.over",
            max_requests=2,
            window_seconds=60,
            key="ip",
        )

        @get("/t", guards=[guard])
        async def handler() -> dict[str, bool]:
            return {"ok": True}

        with TestClient(_make_test_app(handler)) as client:
            for _ in range(2):
                assert client.get("/t").status_code == 200
            denied = client.get("/t")
            assert denied.status_code == 429
            # RFC 9457 structured error detail.
            body = denied.json()
            assert body["success"] is False
            assert body["error_detail"]["error_category"] == "rate_limit"
            assert body["error_detail"]["retryable"] is True
            # Retry-After header propagated.
            assert "Retry-After" in denied.headers
            assert int(denied.headers["Retry-After"]) >= 1

    def test_overrides_take_precedence_over_defaults(self) -> None:
        """Config overrides tighten the decorator-time defaults."""
        guard = per_op_rate_limit(
            "test.tunable",
            max_requests=100,
            window_seconds=60,
            key="ip",
        )

        @get("/t", guards=[guard])
        async def handler() -> dict[str, bool]:
            return {"ok": True}

        config = PerOpRateLimitConfig(overrides={"test.tunable": (1, 60)})
        with TestClient(_make_test_app(handler, config=config)) as client:
            assert client.get("/t").status_code == 200
            assert client.get("/t").status_code == 429

    def test_disabled_config_skips_guard(self) -> None:
        guard = per_op_rate_limit(
            "test.disabled",
            max_requests=1,
            window_seconds=60,
            key="ip",
        )

        @get("/t", guards=[guard])
        async def handler() -> dict[str, bool]:
            return {"ok": True}

        config = PerOpRateLimitConfig(enabled=False)
        with TestClient(_make_test_app(handler, config=config)) as client:
            for _ in range(10):
                assert client.get("/t").status_code == 200

    def test_override_to_zero_disables_operation(self) -> None:
        """Setting an override of (0, 0) disables just one operation."""
        guard = per_op_rate_limit(
            "test.off",
            max_requests=1,
            window_seconds=60,
            key="ip",
        )

        @get("/t", guards=[guard])
        async def handler() -> dict[str, bool]:
            return {"ok": True}

        config = PerOpRateLimitConfig(overrides={"test.off": (0, 0)})
        with TestClient(_make_test_app(handler, config=config)) as client:
            for _ in range(5):
                assert client.get("/t").status_code == 200

    def test_invalid_construction(self) -> None:
        with pytest.raises(ValueError, match="max_requests"):
            per_op_rate_limit("bad", max_requests=0, window_seconds=60)
        with pytest.raises(ValueError, match="window_seconds"):
            per_op_rate_limit("bad", max_requests=10, window_seconds=0)


class TestTrainingEndpointBurstRejection:
    """The two rate-limited training endpoints reject burst traffic.

    Builds the guard from the real policy registry for the exact
    ``training.create_plan`` / ``training.update_overrides`` keys (the
    same call the controllers make) and drives it past its policy
    ``max_requests`` to assert burst traffic is rejected with 429 plus
    a ``Retry-After`` header.
    """

    @pytest.mark.parametrize(
        "operation",
        ["training.create_plan", "training.update_overrides"],
    )
    def test_burst_past_policy_limit_is_rejected(self, operation: str) -> None:
        max_requests, _window = RATE_LIMIT_POLICIES[operation]
        # ``key="ip"`` keeps the test auth-free while still exercising
        # the real policy-resolved guard the controllers attach.
        guard = per_op_rate_limit_from_policy(operation, key="ip")

        @get("/t", guards=[guard])
        async def handler() -> dict[str, bool]:
            return {"ok": True}

        with TestClient(_make_test_app(handler)) as client:
            for _ in range(max_requests):
                assert client.get("/t").status_code == 200
            blocked = client.get("/t")
            assert blocked.status_code == 429
            assert "retry-after" in {k.lower() for k in blocked.headers}
