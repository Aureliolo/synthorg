"""Runtime feature state slice (worker execution + coordination).

Holds the worker execution service, the multi-agent coordinator, and
the distributed task-queue + backend services. The execution service
and coordinator are installed at boot behind the provider switch
(empty-company runs leave the coordinator ``None``); the distributed
services are wired only when a NATS/JetStream backend is configured.
All fields are ``None`` until wired; readers guard accordingly.
"""

from typing import TYPE_CHECKING

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.engine.coordination.service import (
    MultiAgentCoordinator,  # noqa: TC001
)
from synthorg.workers.backend_services import (
    DistributedBackendServices,  # noqa: TC001
)
from synthorg.workers.claim import JetStreamTaskQueue  # noqa: TC001
from synthorg.workers.execution_service import (
    WorkerExecutionService,  # noqa: TC001
)

if TYPE_CHECKING:
    from synthorg.api.state_slices import AppStateSliceMixin


class RuntimeStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the runtime feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    worker_execution_service: WorkerExecutionService | None = None
    coordinator: MultiAgentCoordinator | None = None
    distributed_task_queue: JetStreamTaskQueue | None = None
    distributed_backend_services: DistributedBackendServices | None = None


def worker_execution_service_of(
    app_state: AppStateSliceMixin,
) -> WorkerExecutionService:
    """Resolve the worker-callable execution service, wiring the default.

    Returns the boot-installed service (the agent-runtime service when a
    provider is configured, the no-provider backstop otherwise). When no
    boot install ran first (bare-state tests), lazily composes the
    baseline lifecycle-advancing service so the worker-callable execute
    endpoint still advances task lifecycle.

    Returns:
        The wired or lazily-composed worker execution service.
    """
    slice_ = app_state.slice(RuntimeStateSlice)
    if slice_.worker_execution_service is not None:
        return slice_.worker_execution_service
    from synthorg.engine.state import task_engine_of  # noqa: PLC0415
    from synthorg.workers.execution_service import (  # noqa: PLC0415
        LifecycleAdvancingExecutionService,
    )

    service = LifecycleAdvancingExecutionService(task_engine=task_engine_of(app_state))
    app_state.wire(RuntimeStateSlice, worker_execution_service=service)
    return service
