"""The boot hook installs the worker execution service before any read.

If any startup hook read ``app_state.worker_execution_service`` before
the install hook ran, the property's lazy
``LifecycleAdvancingExecutionService`` default would materialise and the
once-only ``set_worker_execution_service`` would then raise, failing
startup. A clean startup whose installed service is the builder's
output (never the lifecycle-only default) is the practical proof that
the ordering invariant holds.
"""

import pytest
from litestar.testing import TestClient

from synthorg.workers.execution_service import (
    AgentEngineExecutionService,
    LifecycleAdvancingExecutionService,
    NoProviderExecutionService,
)
from tests.integration.api.conftest import build_runtime_app
from tests.unit.api.fakes import FakeMessageBus, FakePersistenceBackend

pytestmark = pytest.mark.integration

_COMPANY_NAME = "install-order-test"


def test_no_provider_installs_backstop_not_lazy_default(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> None:
    app = build_runtime_app(
        fake_persistence,
        fake_message_bus,
        with_provider=False,
        company_name=_COMPANY_NAME,
    )
    with TestClient(app) as client:
        service = client.app.state["app_state"].worker_execution_service
    assert isinstance(service, NoProviderExecutionService)
    assert not isinstance(service, LifecycleAdvancingExecutionService)


def test_provider_installs_agent_engine_service(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> None:
    app = build_runtime_app(
        fake_persistence,
        fake_message_bus,
        with_provider=True,
        company_name=_COMPANY_NAME,
    )
    with TestClient(app) as client:
        service = client.app.state["app_state"].worker_execution_service
    assert isinstance(service, AgentEngineExecutionService)
