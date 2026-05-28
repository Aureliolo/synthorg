"""post_setup_reinit wakes BOTH runtime services on provider config.

An empty company boots with no provider: the worker seam is the
``NoProviderExecutionService`` backstop and ``/coordinate`` honestly
503s (no coordinator). When the operator configures a provider and
``/setup/complete`` runs, ``post_setup_reinit`` must rebuild the
runtime services and hot-swap in BOTH the live worker execution
service AND the multi-agent coordinator, so ``/coordinate`` comes
online without a process restart. Waking only the worker seam would
leave ``/coordinate`` permanently 503 until a restart, so the rebuild
must swap both.
"""

import pytest
from litestar.testing import AsyncTestClient

from synthorg.api.controllers.setup.agent_helpers import post_setup_reinit
from synthorg.config.provider_schema import ProviderConfig
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.errors import RuntimeServicesBuildError
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.state import has_active_provider
from synthorg.workers.execution_service import (
    AgentEngineExecutionService,
    NoProviderExecutionService,
)
from synthorg.workers.state import RuntimeStateSlice
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
        runtime_slice_boot = app_state.slice(RuntimeStateSlice)
        coordinator_at_boot = runtime_slice_boot.coordinator is not None
        assert coordinator_at_boot is False
        assert isinstance(
            runtime_slice_boot.worker_execution_service,
            NoProviderExecutionService,
        )

        # Operator configures a provider: the provider registry becomes
        # populated (the state post_setup_reinit's provider-reload step
        # produces), so the subsequent runtime-services rebuild must wake
        # the coordinator as well as the worker seam.
        app_state.swap_provider_registry(
            ProviderRegistry.from_config(
                {"test-provider": ProviderConfig(driver="scripted")},
            ),
        )

        await post_setup_reinit(app_state)

        # Both runtime services are now live, no restart.
        runtime_slice_after = app_state.slice(RuntimeStateSlice)
        assert has_active_provider(app_state) is True
        coordinator = runtime_slice_after.coordinator
        worker = runtime_slice_after.worker_execution_service
        assert coordinator is not None
        assert isinstance(coordinator, MultiAgentCoordinator)
        assert isinstance(worker, AgentEngineExecutionService)


async def test_reinit_raises_when_coordinator_swap_fails(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed coordinator swap must abort reinit (typed, re-raised).

    If the worker swap succeeds but the coordinator swap raises, the
    whole rebuild must raise (a typed ``RuntimeServicesBuildError``) so
    ``post_setup_reinit``'s caller keeps ``setup_complete=false`` rather
    than presenting a half-configured runtime as complete.
    """
    app = build_runtime_app(
        fake_persistence,
        fake_message_bus,
        with_provider=False,
        company_name=_COMPANY_NAME,
    )
    async with AsyncTestClient(app=app) as client:
        app_state = client.app.state["app_state"]
        app_state.swap_provider_registry(
            ProviderRegistry.from_config(
                {"test-provider": ProviderConfig(driver="scripted")},
            ),
        )

        def _boom(_coordinator: object) -> None:
            msg = "coordinator swap failed"
            raise RuntimeError(msg)

        monkeypatch.setattr(app_state, "swap_coordinator", _boom)

        with pytest.raises(RuntimeServicesBuildError):
            await post_setup_reinit(app_state)
