"""A sandboxed agent is not a stranger with an IP.

The LLM gateway and the credentialed-tool MCP server verify their own per-run
signed bearer inside the handler, which is exactly why both are excluded from
session auth. That leaves ``scope["user"]`` unset, and the rate limiter reads
an unset user as anonymous: the tier sized for a stranger, twenty requests a
minute. An agent doing ordinary work spends that in seconds, and the run then
dies on a 429 issued by its own control plane. Two OpenHands runs died that way
in one A/B recording, on the MCP endpoint, mid-task.
"""

from types import SimpleNamespace
from typing import cast

import pytest
from litestar import Request
from litestar.datastructures import State
from litestar.testing import RequestFactory

from synthorg.api.middleware_factory import (
    _throttle_when_anonymous,
    _throttle_when_authenticated,
)

pytestmark = pytest.mark.unit


def _request(
    path: str, *, user: object | None = None
) -> Request[object, object, State]:
    """Build a real request carrying *path* and *user*.

    Args:
        path: The request path.
        user: Value for ``scope["user"]``.

    Returns:
        A Litestar request the tier gates can read.
    """
    request = RequestFactory().get(path)
    request.scope["user"] = user
    return cast("Request[object, object, State]", request)


class TestSelfAuthenticatedPaths:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/gateway/v1/chat/completions",
            "/api/v1/mcp-gateway/mcp",
        ],
    )
    def test_a_bearer_bearing_path_is_not_anonymous(self, path: str) -> None:
        assert _throttle_when_anonymous(_request(path)) is False

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/gateway/v1/chat/completions",
            "/api/v1/mcp-gateway/mcp",
        ],
    )
    def test_a_bearer_bearing_path_takes_the_authenticated_tier(
        self, path: str
    ) -> None:
        # Exactly one tier must claim it: counted by neither would leave the
        # endpoint unlimited, counted by both would halve its real budget.
        assert _throttle_when_authenticated(_request(path)) is True


class TestOrdinaryTraffic:
    def test_an_unauthenticated_request_stays_anonymous(self) -> None:
        assert _throttle_when_anonymous(_request("/api/v1/agents")) is True
        assert _throttle_when_authenticated(_request("/api/v1/agents")) is False

    def test_an_authenticated_request_stays_on_the_user_tier(self) -> None:
        signed_in = _request("/api/v1/agents", user=SimpleNamespace(user_id="u-1"))
        assert _throttle_when_anonymous(signed_in) is False
        assert _throttle_when_authenticated(signed_in) is True

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/gateway-admin/settings",
            "/api/v1/mcp-gateway-admin/tools",
            "/api/v1/agents/gatewayish",
        ],
    )
    def test_a_sibling_route_does_not_inherit_the_tier(self, path: str) -> None:
        # The exemption is anchored the same way the auth exclusion is, so a
        # neighbouring route cannot pick up an unauthenticated budget of 6000.
        assert _throttle_when_anonymous(_request(path)) is True
