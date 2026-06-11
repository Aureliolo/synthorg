# module-kind: code
"""Structural handles for the optional distributed task-queue stack.

The concrete distributed types (``JetStreamTaskQueue``, ``DistributedDispatcher``,
``DistributedBackendServices``) live behind the optional ``synthorg[distributed]``
extra and sit in a cold-import cycle (``workers.config`` -> ``communication.config``).
Construction-phase auto-wiring (``synthorg.api.auto_wire_phase1``) runs early enough
that importing those concrete types there would regress the cold-import path, yet it
must name them in the ``Phase1Result`` it returns. These ``@runtime_checkable``
Protocols give that result (and the runtime state slice) a runtime-resolvable handle
type without the concrete import: the real objects satisfy them structurally.

They live in ``workers`` (not ``api``) so the workers-owned ``RuntimeStateSlice`` can
annotate its distributed fields against them without a reverse ``workers -> api`` edge.
"""

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from synthorg.engine.task_engine_models import TaskStateChanged
from synthorg.settings.bridge_configs import WorkersBridgeConfig


@runtime_checkable
class DistributedTaskQueueHandle(Protocol):
    """The distributed task queue's start/stop/running surface.

    Satisfied by ``synthorg.workers.claim.JetStreamTaskQueue``.
    """

    @property
    def is_running(self) -> bool:
        """Whether the queue client is connected and serving."""
        ...

    async def start(self) -> None:
        """Connect and provision the stream and consumer."""
        ...

    async def stop(self) -> None:
        """Drain and close the connection; idempotent."""
        ...


@runtime_checkable
class DistributedDispatcherHandle(Protocol):
    """The distributed dispatcher's observer + bridge-provider surface.

    Satisfied by ``synthorg.workers.dispatcher.DistributedDispatcher``. The engine
    registers ``on_task_state_changed`` as a task-state observer; the construction
    seam late-binds the live workers-bridge-config provider once ``AppState`` exists.
    """

    async def on_task_state_changed(self, event: TaskStateChanged) -> None:
        """Handle a task-state-changed event from the engine."""
        ...

    def set_workers_bridge_provider(
        self,
        provider: Callable[[], WorkersBridgeConfig],
    ) -> None:
        """Late-bind the live workers-bridge-config snapshot provider."""
        ...


@runtime_checkable
class DistributedBackendServicesHandle(Protocol):
    """The distributed backend-services bundle's start/stop surface.

    Satisfied by ``synthorg.workers.backend_services.DistributedBackendServices``.
    Unlike ``DistributedTaskQueueHandle`` this omits ``is_running``: the API
    lifecycle only starts and stops the bundle, never interrogating its health
    through the handle (the concrete type tracks running state internally to
    compose its sub-components).
    """

    async def start(self) -> None:
        """Start the distributed-path background services."""
        ...

    async def stop(self) -> None:
        """Stop the distributed-path background services; idempotent."""
        ...
