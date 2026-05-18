"""post_setup_reinit wakes BOTH runtime services on provider config.

An empty company boots with no provider: the worker seam is the
``NoProviderExecutionService`` backstop and ``/coordinate`` honestly
503s (no coordinator). When the operator configures a provider and
``/setup/complete`` runs, ``post_setup_reinit`` must rebuild the
runtime services and hot-swap in BOTH the live worker execution
service AND the multi-agent coordinator, so ``/coordinate`` comes
online without a process restart. A wake that brought only the worker
seam online (the pre-#1958 behaviour) would leave ``/coordinate``
permanently 503 until a restart.
"""

import pytest
from litestar.testing import AsyncTestClient

from synthorg.api.controllers.setup.agent_helpers import post_setup_reinit
from synthorg.config.provider_schema import ProviderConfig
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.providers.registry import ProviderRegistry
from synthorg.workers.execution_service import (
    AgentEngineExecutionService,
    NoProviderExecutionService,
)
from tests.integration.api.conftest import build_runtime_app
from tests.unit.api.fakes import FakeMessageBus, FakePersistenceBackend

pytestmark = pytest.mark.integration

_COMPANY_NAME = "reinit-wake-test"


async def test_reinit_wakes_worker_and_coordinator_on_provider_config(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> None:
    app = build_runtime_app(
        fake_persistence,
        fake_message_bus,
        with_provider=False,
        company_name=_COMPANY_NAME,
    )
    async with AsyncTestClient(app=app) as client:
        app_state = client.app.state["app_state"]

        # Empty company at boot: no coordinator, backstop worker seam.
        coordinator_at_boot = app_state.has_coordinator
        assert coordinator_at_boot is False
        assert isinstance(
            app_state.worker_execution_service,
            NoProviderExecutionService,
        )

        # Operator configures a provider: the provider registry becomes
        # populated (the state post_setup_reinit's provider-reload step
        # produces). The #1958 delta under test is that the subsequent
        # runtime-services rebuild now wakes the coordinator too, not
        # just the worker seam.
        app_state.swap_provider_registry(
            ProviderRegistry.from_config(
                {"test-provider": ProviderConfig(driver="scripted")},
            ),
        )

        await post_setup_reinit(app_state)

        # Both runtime services are now live, no restart.
        active_provider = app_state.has_active_provider
        coordinator_after_wake = app_state.has_coordinator
        assert active_provider is True
        assert coordinator_after_wake is True
        coordinator = app_state.coordinator
        worker = app_state.worker_execution_service
        assert isinstance(coordinator, MultiAgentCoordinator)
        assert isinstance(worker, AgentEngineExecutionService)
