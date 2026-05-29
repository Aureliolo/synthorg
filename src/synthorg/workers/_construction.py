# module-kind: code
"""Workers feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.workers.state import RuntimeStateSlice

if TYPE_CHECKING:
    from synthorg.api.construction_wiring import ConstructionDeps
    from synthorg.api.state import AppState


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Populate the runtime slice and late-bind the dispatcher bridge config.

    The distributed dispatcher is built (in phase-1 auto-wiring) before
    ``AppState`` exists, so its workers-bridge-config provider is late-bound
    here against the live state. Each publish then reads the current snapshot,
    so an operator hot-reload of a ``workers.dispatcher_publish_*`` setting
    takes effect without restarting the dispatcher.
    """
    phase1 = deps.phase1
    app_state.swap_slice(
        RuntimeStateSlice.model_construct(
            coordinator=deps.coordinator,
            distributed_task_queue=phase1.distributed_task_queue,
            distributed_backend_services=phase1.distributed_backend_services,
        )
    )
    if phase1.distributed_dispatcher is not None:
        phase1.distributed_dispatcher.set_workers_bridge_provider(
            lambda: app_state.workers_bridge_config,
        )
