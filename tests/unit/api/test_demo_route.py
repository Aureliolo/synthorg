"""The discovery-mounted ``/api/v1/demo`` route returns the greeting.

Complements ``tests/e2e/test_demo_feature_discovery_e2e.py`` (hook-gated) with a
locally-runnable check that the demo route, mounted purely from the feature
manifest, actually serves a 200 with the construction-wired greeting -- not just
that the controller registers.
"""

import pytest

from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers

pytestmark = pytest.mark.unit

_DEMO_GREETING = "hello from the demo feature"


async def test_demo_route_returns_greeting(
    async_test_client: LoopAsyncClient,
) -> None:
    """``GET /api/v1/demo`` serves the construction-wired greeting."""
    resp = await async_test_client.get("/api/v1/demo", headers=make_auth_headers("ceo"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["greeting"] == _DEMO_GREETING
