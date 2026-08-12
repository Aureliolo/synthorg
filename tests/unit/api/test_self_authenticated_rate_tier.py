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

from synthorg.api.rate_limits.tiers import build_throttle_gates

pytestmark = pytest.mark.unit

_PREFIX = "/api/v1"
throttle_when_anonymous, throttle_when_authenticated = build_throttle_gates(_PREFIX)


def _request(
    path: str, *, user: object | None = None, bearer: str | None = "run-token"
) -> Request[object, object, State]:
    """Build a real request carrying *path*, *user* and an authorization header.

    Args:
        path: The request path.
        user: Value for ``scope["user"]``.
        bearer: Verbatim ``Authorization`` header value, or ``None`` to send
            none at all.

    Returns:
        A Litestar request the tier gates can read.
    """
    headers = {} if bearer is None else {"Authorization": bearer}
    request = RequestFactory().get(path, headers=headers)
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
        assert throttle_when_anonymous(_request(path, bearer="Bearer r-1")) is False

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
        assert throttle_when_authenticated(_request(path, bearer="Bearer r-1")) is True

    def test_the_scheme_is_read_case_insensitively(self) -> None:
        # RFC 7235 makes the scheme token case-insensitive, and a client that
        # sends "bearer" is an agent, not a stranger.
        request = _request("/api/v1/gateway/v1/chat/completions", bearer="bearer r-1")
        assert throttle_when_authenticated(request) is True


class TestCredentiallessTraffic:
    """The path says where a request was aimed, not who sent it.

    The authenticated tier is 300x the anonymous one, so admitting a caller on
    the URL alone would hand that budget to anyone who typed it.
    """

    @pytest.mark.parametrize(
        "bearer",
        [None, "", "   ", "Bearer", "Bearer ", "Basic dXNlcjpwdw==", "Token r-1"],
    )
    def test_no_well_formed_bearer_stays_anonymous(self, bearer: str | None) -> None:
        request = _request("/api/v1/gateway/v1/chat/completions", bearer=bearer)
        assert throttle_when_anonymous(request) is True
        assert throttle_when_authenticated(request) is False


class TestOrdinaryTraffic:
    def test_an_unauthenticated_request_stays_anonymous(self) -> None:
        request = _request("/api/v1/agents", bearer=None)
        assert throttle_when_anonymous(request) is True
        assert throttle_when_authenticated(request) is False

    def test_an_authenticated_request_stays_on_the_user_tier(self) -> None:
        signed_in = _request("/api/v1/agents", user=SimpleNamespace(user_id="u-1"))
        assert throttle_when_anonymous(signed_in) is False
        assert throttle_when_authenticated(signed_in) is True

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/gateway-admin/settings",
            "/api/v1/mcp-gateway-admin/tools",
            "/api/v1/agents/gatewayish",
            # A route of the same name under a different root: unanchored, the
            # segment alone would hand it the authenticated tier's budget.
            "/other/gateway",
            "/other/mcp-gateway/mcp",
            "/api/v2/gateway/v1/chat/completions",
        ],
    )
    def test_a_sibling_route_does_not_inherit_the_tier(self, path: str) -> None:
        # The exemption is built from the configured API prefix and anchored
        # the same way the auth exclusion is, so a neighbouring route cannot
        # pick up an unauthenticated budget of 6000.
        assert throttle_when_anonymous(_request(path, bearer="Bearer r-1")) is True
