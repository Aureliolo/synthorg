"""Acceptance: the synthetic ``_demo`` feature is reachable end-to-end.

The demo feature ships nothing but a ``feature.py`` manifest in its own
directory (``src/synthorg/_demo/``): a state slice, a construction wirer, a
REST controller, and one MCP tool. This test proves the feature-manifest
substrate discovers and wires all of it with ZERO edits to ``api/app.py`` /
``api/state.py`` / any central wiring -- the boot-wired app exposes the demo
route, the discovery registry + dispatch table carry the demo tool, and the
construction wirer populated the slice (the route 200s rather than 503-ing).

The demo feature is a permanent regression guard: if the composition root ever
stops consuming a manifest limb, one of these assertions fails.
"""

from collections.abc import AsyncGenerator

import pytest

from synthorg._core.features import discover_features
from synthorg._demo.state import DemoStateSlice
from synthorg.budget.tracker import CostTracker
from synthorg.config.schema import RootConfig
from synthorg.meta.mcp.domains import build_full_registry
from synthorg.meta.mcp.handlers import build_handler_map
from tests._shared import LoopAsyncClient
from tests._shared import build_test_app as create_app
from tests.unit.api.conftest import (
    _make_test_auth_service,
    _seed_test_users,
    make_auth_headers,
)
from tests.unit.api.fakes import FakeMessageBus, FakePersistenceBackend

pytestmark = pytest.mark.e2e

_TEST_JWT_SECRET = "integration-test-secret-at-least-32-characters"
_TEST_SETTINGS_KEY = "lKzZcMznksIF8A_2HFFUnKxhxhz9_bxTvVJoZ6mvZrk="
_DEMO_TOOL = "synthorg_demo_greet"
_DEMO_GREETING = "hello from the demo feature"


@pytest.fixture(autouse=True)
def _required_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
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


@pytest.fixture
async def demo_client(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> AsyncGenerator[LoopAsyncClient]:
    """A boot-wired app with NO demo-specific kwargs (discovery only)."""
    auth_service = _make_test_auth_service()
    _seed_test_users(fake_persistence, auth_service)
    app = create_app(
        config=RootConfig(company_name="demo-discovery"),
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=CostTracker(),
        auth_service=auth_service,
    )
    async with LoopAsyncClient(app) as client:
        yield client


class TestDemoFeatureDiscovery:
    """The substrate wires the demo feature from its manifest alone."""

    async def test_demo_route_returns_greeting(
        self,
        demo_client: LoopAsyncClient,
    ) -> None:
        """The demo controller mounts and its service is construction-wired.

        A 200 (not 503) proves the construction wirer populated the slice;
        a 200 (not 404) proves ``collect_route_handlers`` mounted the
        manifest-declared controller.
        """
        headers = make_auth_headers("ceo")
        resp = await demo_client.get("/api/v1/demo", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["greeting"] == _DEMO_GREETING

    def test_demo_feature_is_discovered(self) -> None:
        """``discover_features`` finds the underscore-prefixed demo package."""
        names = {feature.name for feature in discover_features()}
        assert "demo" in names

    def test_demo_state_slice_is_declared(self) -> None:
        """The demo manifest declares its own state slice."""
        demo = next(f for f in discover_features() if f.name == "demo")
        assert demo.state_slice is DemoStateSlice
        assert demo.construction_wirer is not None

    def test_demo_tool_in_registry_and_dispatch(self) -> None:
        """The demo MCP tool is in BOTH the registry and the handler map."""
        registry = build_full_registry()
        assert registry.get(_DEMO_TOOL).name == _DEMO_TOOL
        assert _DEMO_TOOL in build_handler_map()
