"""The boot hook installs the worker execution service before any read.

If any startup hook read ``app_state.worker_execution_service`` before
the install hook ran, the property's lazy
``LifecycleAdvancingExecutionService`` default would materialise and the
once-only ``set_worker_execution_service`` would then raise, failing
startup. A clean startup whose installed service is the builder's
output (never the lifecycle-only default) is the practical proof that
the ordering invariant holds.
"""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from litestar.testing import TestClient

from synthorg.api.app import create_app
from synthorg.api.auth.service import AuthService
from synthorg.budget.tracker import CostTracker
from synthorg.config.provider_schema import ProviderConfig
from synthorg.config.schema import RootConfig
from synthorg.hr.registry import AgentRegistryService
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from synthorg.workers.execution_service import (
    AgentEngineExecutionService,
    LifecycleAdvancingExecutionService,
    NoProviderExecutionService,
)
from tests.unit.api.fakes import FakeMessageBus, FakePersistenceBackend

pytestmark = pytest.mark.integration

_TEST_JWT_SECRET = "integration-test-secret-at-least-32-characters"
_TEST_SETTINGS_KEY = "lKzZcMznksIF8A_2HFFUnKxhxhz9_bxTvVJoZ6mvZrk="


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


def _build_app(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
    *,
    with_provider: bool,
) -> Any:
    root_config = RootConfig(company_name="install-order-test")
    auth_service = AuthService(
        root_config.api.auth.model_copy(update={"jwt_secret": _TEST_JWT_SECRET}),
    )
    settings_service = SettingsService(
        repository=fake_persistence.settings,
        registry=get_registry(),
    )
    provider_registry = (
        ProviderRegistry.from_config(
            {"test-provider": ProviderConfig(driver="scripted")}
        )
        if with_provider
        else None
    )
    return create_app(
        config=root_config,
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=CostTracker(),
        auth_service=auth_service,
        agent_registry=AgentRegistryService(),
        settings_service=settings_service,
        provider_registry=provider_registry,
    )


def test_no_provider_installs_backstop_not_lazy_default(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> None:
    app = _build_app(fake_persistence, fake_message_bus, with_provider=False)
    with TestClient(app) as client:
        service = client.app.state["app_state"].worker_execution_service
    assert isinstance(service, NoProviderExecutionService)
    assert not isinstance(service, LifecycleAdvancingExecutionService)


def test_provider_installs_agent_engine_service(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> None:
    app = _build_app(fake_persistence, fake_message_bus, with_provider=True)
    with TestClient(app) as client:
        service = client.app.state["app_state"].worker_execution_service
    assert isinstance(service, AgentEngineExecutionService)
