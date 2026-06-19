"""Typed application-state composition root.

``AppState`` composes the per-feature state slices (``AppStateSliceMixin``)
with its own lifecycle identity (clock, config, uptime baseline, shutdown
event, background-task sets) and the cohesive primitive owner objects that
hold the cross-cutting mutable state a frozen slice cannot: the
bridge-config snapshots (``bridge_config``), the per-op rate-limit /
concurrency configs (``per_op_limits``), the request-lock registry
(``request_locks``), and the WS/auth timeout knobs (``ws_auth_limits``).

Every domain service is read through its feature slice
(``app_state.slice(XStateSlice).field`` or a ``*_of`` accessor); the
load-bearing hot-swap seams below stay as thin shims over
``AppStateSliceMixin.wire`` so the boot install and ``post_setup_reinit``
keep their once-only / if-absent / hot-replace semantics.
"""

import asyncio
from typing import Final, cast

from synthorg.api.state_bridge_config import BridgeConfigState
from synthorg.api.state_per_op_limits import PerOpLimitsState
from synthorg.api.state_request_locks import RequestLockRegistry
from synthorg.api.state_slices import AppStateSliceMixin
from synthorg.api.state_ws_auth_limits import WsAuthLimits
from synthorg.client.models import ClientRequest
from synthorg.config.schema import RootConfig
from synthorg.core.clock import Clock, SystemClock
from synthorg.engine.brownfield.models import CodebaseImportSubmission
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.pipeline.entry.objective_adapter import ObjectiveSubmission
from synthorg.engine.pipeline.entry.protocol import WorkEntryAdapter
from synthorg.engine.pipeline.entry.task_board_adapter import TaskBoardEntryAdapter
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.engine.shutdown import CooperativeTimeoutStrategy, ShutdownManager
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.providers.registry import ProviderRegistry
from synthorg.workers.execution_service import WorkerExecutionService

# Grace window the cooperative shutdown manager waits for in-flight
# multi-agent parallel tasks to exit at a turn boundary before it
# force-cancels stragglers. Kept under the task-engine shutdown budget
# (8s in api/lifecycle.py) so initiating the cooperative drain at the top
# of teardown cannot push the total past the orchestrator SIGKILL
# deadline (75s in api/server.py).
_SHUTDOWN_GRACE_SECONDS: Final[float] = 8.0
_SHUTDOWN_CLEANUP_SECONDS: Final[float] = 2.0

# Grace window the objective / brownfield entry background tasks get to
# finish at a turn boundary on shutdown before stragglers are cancelled.
# These are fire-and-forget entry-processing tasks tracked only in their
# in-memory sets, so without an explicit drain they are abandoned mid-flight
# when the loop tears down (silent work loss). Kept short so the two drains
# stay well within the orchestrator SIGKILL deadline.
_ENTRY_TASK_DRAIN_GRACE_SECONDS: Final[float] = 3.0


class AppState(AppStateSliceMixin):
    """Composition root: feature state slices + identity + primitive owners.

    Domain services are read through their feature slice; the cross-cutting
    mutable primitives live on cohesive owner objects (``bridge_config`` /
    ``per_op_limits`` / ``request_locks`` / ``ws_auth_limits``). The seams
    below preserve the once-only / if-absent / hot-replace contracts the
    boot install and ``post_setup_reinit`` rebuild rely on.
    """

    __slots__ = ()

    # Lifecycle identity + primitive owners live in ``__dict__`` (the base
    # ``AppStateSliceMixin`` carries no slots). These are bare annotations
    # for readability, not slots, so ``__slots__`` stays empty.
    config: RootConfig
    clock: Clock
    startup_time: float
    bridge_config: BridgeConfigState
    per_op_limits: PerOpLimitsState
    request_locks: RequestLockRegistry
    ws_auth_limits: WsAuthLimits

    def __init__(
        self,
        *,
        config: RootConfig,
        clock: Clock | None = None,
        startup_time: float | None = None,
    ) -> None:
        """Build the composition root with its identity, owners, and slices.

        Args:
            config: The resolved root configuration.
            clock: Clock seam (``SystemClock`` default; tests inject ``FakeClock``).
            startup_time: Monotonic uptime baseline (defaults to ``clock.monotonic()``).
        """
        self.config = config
        # Clock seam: controllers/services read time via ``app_state.clock``
        # so tests inject a ``FakeClock`` without monkey-patching time.
        self.clock: Clock = clock or SystemClock()
        self.startup_time = (
            startup_time if startup_time is not None else self.clock.monotonic()
        )
        # Background-task sets for the objective / brownfield entry paths.
        self._objective_background_tasks: set[asyncio.Task[None]] = set()
        self._brownfield_background_tasks: set[asyncio.Task[None]] = set()
        # Shutdown flag observable by long-lived subsystems; constructed
        # eagerly so concurrent first-reads share one ``Event``.
        self._shutdown_requested: asyncio.Event = asyncio.Event()
        # Cooperative shutdown manager: the multi-agent coordinator
        # registers each in-flight parallel agent task with it, so on
        # SIGTERM new tasks are rejected (drain gate) and in-flight tasks
        # get a bounded grace-then-cancel via ``initiate_shutdown``.
        self._shutdown_manager: ShutdownManager = ShutdownManager(
            CooperativeTimeoutStrategy(
                grace_seconds=_SHUTDOWN_GRACE_SECONDS,
                cleanup_seconds=_SHUTDOWN_CLEANUP_SECONDS,
                clock=self.clock,
            ),
        )
        # Cohesive owners of the cross-cutting mutable primitives a frozen
        # slice cannot hold.
        self.bridge_config = BridgeConfigState()
        self.per_op_limits = PerOpLimitsState()
        self.request_locks = RequestLockRegistry()
        self.ws_auth_limits = WsAuthLimits()
        # Per-feature typed state slices, composed at boot by the
        # feature-manifest substrate (``compose_feature_slices``).
        self._init_slice_store()

    @property
    def shutdown_requested(self) -> asyncio.Event:
        """Shutdown flag set by the signal handlers; observed by loops.

        Returns:
            The process-shared shutdown ``asyncio.Event``.
        """
        return self._shutdown_requested

    @property
    def shutdown_manager(self) -> ShutdownManager:
        """Cooperative shutdown manager for in-flight multi-agent tasks.

        Returns:
            The process-shared :class:`ShutdownManager` the coordinator
            registers parallel agent tasks with.
        """
        return self._shutdown_manager

    @property
    def objective_background_tasks(self) -> set[asyncio.Task[None]]:
        """Live set of in-flight objective-entry background tasks.

        Returns:
            The mutable task set (callers add/discard their own tasks).
        """
        return self._objective_background_tasks

    @property
    def brownfield_background_tasks(self) -> set[asyncio.Task[None]]:
        """Live set of in-flight brownfield-entry background tasks.

        Returns:
            The mutable task set (callers add/discard their own tasks).
        """
        return self._brownfield_background_tasks

    async def drain_entry_background_tasks(self) -> None:
        """Drain in-flight objective / brownfield entry background tasks.

        Gives the live tasks a bounded grace
        (``_ENTRY_TASK_DRAIN_GRACE_SECONDS``) to finish at a turn boundary,
        then cancels any straggler and awaits its cancellation so the
        coroutine unwinds cleanly rather than being abandoned when the
        loop tears down. Snapshots the sets up front because a completing
        task's done-callback discards itself from the live set (mutation
        during iteration). Idempotent and safe when both sets are empty.
        """
        pending = self._objective_background_tasks | self._brownfield_background_tasks
        pending = {task for task in pending if not task.done()}
        if not pending:
            return
        _, still_running = await asyncio.wait(
            pending,
            timeout=_ENTRY_TASK_DRAIN_GRACE_SECONDS,
        )
        for task in still_running:
            task.cancel()
        if still_running:
            # Await the cancellations so the coroutines unwind before the
            # loop closes; ``return_exceptions`` keeps one task's failure
            # from masking the others.
            await asyncio.gather(*still_running, return_exceptions=True)

    # -- Hot-swap seams (thin shims over ``wire``) -----------------------
    # Public names preserved for the boot install + ``post_setup_reinit``.

    def swap_provider_registry(self, registry: ProviderRegistry) -> None:
        """Hot-replace the provider registry (setup-complete reinit)."""
        from synthorg.providers.state import ProvidersStateSlice  # noqa: PLC0415

        self.wire(ProvidersStateSlice, registry=registry)

    def set_worker_execution_service(
        self,
        service: WorkerExecutionService,
    ) -> None:
        """Install the worker execution service once at boot.

        Raises:
            RuntimeError: If a service is already installed.
        """
        from synthorg.workers.state import RuntimeStateSlice  # noqa: PLC0415

        self.set_field_once(
            RuntimeStateSlice,
            "worker_execution_service",
            service,
            "Worker execution service",
        )

    def swap_worker_execution_service(
        self,
        service: WorkerExecutionService,
    ) -> None:
        """Hot-replace the worker execution service (setup-complete reinit)."""
        from synthorg.workers.state import RuntimeStateSlice  # noqa: PLC0415

        self.wire(RuntimeStateSlice, worker_execution_service=service)

    def set_coordinator_if_absent(
        self,
        coordinator: MultiAgentCoordinator,
    ) -> None:
        """Install the coordinator only if one is not already wired."""
        from synthorg.workers.state import RuntimeStateSlice  # noqa: PLC0415

        self.wire_if_field_absent(RuntimeStateSlice, "coordinator", coordinator)

    def swap_coordinator(self, coordinator: MultiAgentCoordinator) -> None:
        """Hot-replace the multi-agent coordinator (setup-complete reinit)."""
        from synthorg.workers.state import RuntimeStateSlice  # noqa: PLC0415

        self.wire(RuntimeStateSlice, coordinator=coordinator)

    def set_work_pipeline_if_absent(self, work_pipeline: WorkPipeline) -> None:
        """Install the work-pipeline spine only if not already wired."""
        from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

        self.wire_if_field_absent(EngineStateSlice, "work_pipeline", work_pipeline)

    def swap_work_pipeline(self, work_pipeline: WorkPipeline) -> None:
        """Hot-replace the work-pipeline spine (setup-complete reinit)."""
        from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

        self.wire(EngineStateSlice, work_pipeline=work_pipeline)

    def set_intake_entry_adapter_if_absent(
        self,
        adapter: WorkEntryAdapter[ClientRequest],
    ) -> None:
        """Install the intake entry adapter only if not already wired."""
        from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

        self.wire_if_field_absent(EngineStateSlice, "intake_entry_adapter", adapter)

    def swap_intake_entry_adapter(
        self, adapter: WorkEntryAdapter[ClientRequest]
    ) -> None:
        """Hot-replace the intake entry adapter (setup-complete reinit)."""
        from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

        self.wire(EngineStateSlice, intake_entry_adapter=adapter)

    def set_objective_entry_adapter_if_absent(
        self,
        adapter: WorkEntryAdapter[ObjectiveSubmission],
    ) -> None:
        """Install the objective entry adapter only if not already wired."""
        from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

        self.wire_if_field_absent(EngineStateSlice, "objective_entry_adapter", adapter)

    def swap_objective_entry_adapter(
        self,
        adapter: WorkEntryAdapter[ObjectiveSubmission],
    ) -> None:
        """Hot-replace the objective entry adapter (setup-complete reinit)."""
        from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

        self.wire(EngineStateSlice, objective_entry_adapter=adapter)

    def set_brownfield_entry_adapter_if_absent(
        self,
        adapter: WorkEntryAdapter[CodebaseImportSubmission],
    ) -> None:
        """Install the brownfield entry adapter only if not already wired."""
        from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

        self.wire_if_field_absent(EngineStateSlice, "brownfield_entry_adapter", adapter)

    def swap_brownfield_entry_adapter(
        self,
        adapter: WorkEntryAdapter[CodebaseImportSubmission],
    ) -> None:
        """Hot-replace the brownfield entry adapter (setup-complete reinit)."""
        from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

        self.wire(EngineStateSlice, brownfield_entry_adapter=adapter)

    def set_task_board_entry_adapter_if_absent(
        self,
        adapter: TaskBoardEntryAdapter,
    ) -> None:
        """Install the task-board entry adapter only if not already wired."""
        from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

        self.wire_if_field_absent(EngineStateSlice, "task_board_entry_adapter", adapter)

    def swap_task_board_entry_adapter(
        self,
        adapter: TaskBoardEntryAdapter,
    ) -> None:
        """Hot-replace the task-board entry adapter (setup-complete reinit)."""
        from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

        self.wire(EngineStateSlice, task_board_entry_adapter=adapter)

    def swap_notification_dispatcher(
        self,
        dispatcher: NotificationDispatcher,
    ) -> NotificationDispatcher | None:
        """Hot-replace the notification dispatcher, returning the previous.

        The caller (bridge-config apply) awaits ``aclose()`` on the
        returned previous dispatcher to release its HTTP-bearing sinks.

        Returns:
            The dispatcher previously wired, or ``None`` on first install.
        """
        from synthorg.notifications.state import (  # noqa: PLC0415
            NotificationsStateSlice,
        )

        previous = self.swap_field_returning_previous(
            NotificationsStateSlice, "dispatcher", dispatcher
        )
        return cast("NotificationDispatcher | None", previous)
