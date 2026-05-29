"""The boot hook installs the runtime services before any read.

If any startup hook read ``app_state.worker_execution_service`` before
the install hook ran, the property's lazy
``LifecycleAdvancingExecutionService`` default would materialise and the
once-only ``set_worker_execution_service`` would then raise, failing
startup. A clean startup whose installed service is the builder's
output (never the lifecycle-only default) is the practical proof that
the ordering invariant holds. The same provider-present switch wires
the multi-agent coordinator, so ``has_coordinator`` reflects provider
presence: true with a provider, false on the empty-company backstop.
"""

import pytest

from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.workers.execution_service import (
    AgentEngineExecutionService,
    LifecycleAdvancingExecutionService,
    NoProviderExecutionService,
)
from synthorg.workers.state import RuntimeStateSlice
from tests._shared import LoopAsyncClient, mock_of
from tests.integration.api.conftest import build_runtime_app
from tests.unit.api.fakes import FakeMessageBus, FakePersistenceBackend

pytestmark = pytest.mark.integration

_COMPANY_NAME = "install-order-test"


async def test_no_provider_installs_backstop_not_lazy_default(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> None:
    app = build_runtime_app(
        fake_persistence,
        fake_message_bus,
        with_provider=False,
        company_name=_COMPANY_NAME,
    )
    async with LoopAsyncClient(app) as client:
        app_state = client.app.state["app_state"]
        runtime_slice = app_state.slice(RuntimeStateSlice)
        service = runtime_slice.worker_execution_service
        has_coordinator = runtime_slice.coordinator is not None
    assert isinstance(service, NoProviderExecutionService)
    assert not isinstance(service, LifecycleAdvancingExecutionService)
    # Empty company: no coordinator, /coordinate honestly 503s.
    assert has_coordinator is False


async def test_provider_installs_agent_engine_service_and_coordinator(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> None:
    app = build_runtime_app(
        fake_persistence,
        fake_message_bus,
        with_provider=True,
        company_name=_COMPANY_NAME,
    )
    async with LoopAsyncClient(app) as client:
        app_state = client.app.state["app_state"]
        runtime_slice = app_state.slice(RuntimeStateSlice)
        service = runtime_slice.worker_execution_service
        coordinator = runtime_slice.coordinator
        has_coordinator = coordinator is not None
    assert isinstance(service, AgentEngineExecutionService)
    # Same provider switch wires the coordinator behind /coordinate.
    assert has_coordinator is True
    assert isinstance(coordinator, MultiAgentCoordinator)


async def test_injected_coordinator_wins_over_autowired(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> None:
    """An explicitly injected coordinator survives the boot hook.

    ``set_coordinator_if_absent`` keeps the constructor-injected
    coordinator instead of overwriting it with the autowired one (the
    injection-over-autowire convention), even with a provider present.
    """
    injected = mock_of[MultiAgentCoordinator]()
    app = build_runtime_app(
        fake_persistence,
        fake_message_bus,
        with_provider=True,
        company_name=_COMPANY_NAME,
        coordinator=injected,
    )
    async with LoopAsyncClient(app) as client:
        app_state = client.app.state["app_state"]
        coordinator = app_state.slice(RuntimeStateSlice).coordinator
    assert coordinator is injected
