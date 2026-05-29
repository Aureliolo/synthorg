"""Typed application-state container.

``AppState`` is the composition root over the feature-manifest state
slices (``AppStateSliceMixin``) plus the cross-cutting mutable
primitives a frozen slice cannot own (request locks, bridge-config
snapshots, WS timeouts, background-task sets, the shutdown event), which
live on ``_RequestLockPrimitivesMixin`` + ``_BridgeConfigPrimitivesMixin``.

Every domain service is read through its feature slice
(``app_state.slice(XStateSlice).field`` or a ``*_of`` accessor); the
load-bearing hot-swap seams below stay as thin shims over
``AppStateSliceMixin.wire`` so the boot install and ``post_setup_reinit``
keep their once-only / if-absent / hot-replace semantics.
"""

import asyncio
import threading
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, cast, override

from synthorg.api.state_services_bridge import _BridgeConfigPrimitivesMixin
from synthorg.api.state_services_locks import _RequestLockPrimitivesMixin
from synthorg.api.state_slices import AppStateSliceMixin
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_SERVICE_UNAVAILABLE
from synthorg.settings.bridge_configs import (
    ApiBridgeConfig,
    MemoryBridgeConfig,
    WorkersBridgeConfig,
)

if TYPE_CHECKING:
    from synthorg.config.schema import RootConfig
    from synthorg.engine.coordination.service import MultiAgentCoordinator
    from synthorg.engine.pipeline.entry.protocol import WorkEntryAdapter
    from synthorg.engine.pipeline.entry.task_board_adapter import (
        TaskBoardEntryAdapter,
    )
    from synthorg.engine.pipeline.protocol import WorkPipeline
    from synthorg.notifications.dispatcher import NotificationDispatcher
    from synthorg.providers.registry import ProviderRegistry
    from synthorg.workers.execution_service import WorkerExecutionService

logger = get_logger(__name__)


class AppState(
    AppStateSliceMixin,
    _RequestLockPrimitivesMixin,
    _BridgeConfigPrimitivesMixin,
):
    """Composition root: feature state slices + cross-cutting primitives.

    Domain services are read through their feature slice; the seams
    below preserve the once-only / if-absent / hot-replace contracts the
    boot install and ``post_setup_reinit`` rebuild rely on.
    """

    __slots__ = (
        "_api_bridge_config",
        "_api_bridge_config_lock",
        "_auth_revalidate_max_failures",
        "_auth_revalidate_window_seconds",
        "_bridge_config_applied",
        "_brownfield_background_tasks",
        "_memory_bridge_config",
        "_memory_bridge_config_lock",
        "_objective_background_tasks",
        "_per_op_concurrency_config",
        "_per_op_rate_limit_config",
        "_request_lock_refs",
        "_request_locks",
        "_request_locks_guard",
        "_shutdown_requested",
        "_workers_bridge_config",
        "_workers_bridge_config_lock",
        "_ws_auth_timeout_seconds",
        "_ws_frame_timeout_seconds",
        "clock",
        "config",
        "startup_time",
    )

    def __init__(
        self,
        *,
        config: RootConfig,
        clock: Clock | None = None,
        startup_time: float | None = None,
    ) -> None:
        """Build the composition root with empty slices + default primitives.

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
        self._init_primitives()
        # Per-feature typed state slices, composed at boot by the
        # feature-manifest substrate (``compose_feature_slices``).
        self._init_slice_store()

    def _init_primitives(self) -> None:
        """Initialise the cross-cutting mutable primitives to their defaults."""
        # Bridge-config snapshots: default-constructed so consumers see
        # valid defaults before ``_apply_bridge_config`` runs; each lock
        # guards its ``mutate_*`` read-modify-write.
        self._api_bridge_config: ApiBridgeConfig = ApiBridgeConfig()
        self._api_bridge_config_lock: threading.Lock = threading.Lock()
        self._workers_bridge_config: WorkersBridgeConfig = WorkersBridgeConfig()
        self._workers_bridge_config_lock: threading.Lock = threading.Lock()
        self._memory_bridge_config: MemoryBridgeConfig = MemoryBridgeConfig()
        self._memory_bridge_config_lock: threading.Lock = threading.Lock()
        # One-shot flag: bridge config applied exactly once per lifetime
        # even across re-entered lifespans (shared-app test fixtures).
        self._bridge_config_applied: bool = False
        # Per-op rate-limit + concurrency configs, hot-swapped by the
        # settings subscribers; ``None`` until the startup snapshot lands.
        self._per_op_rate_limit_config = None
        self._per_op_concurrency_config = None
        # WS / auth-revalidation knobs (read_only_post_init); sane
        # built-in defaults so the handler never reaches the resolver.
        self._ws_auth_timeout_seconds: float = 10.0
        self._ws_frame_timeout_seconds: int = 30
        self._auth_revalidate_window_seconds: int = 60
        self._auth_revalidate_max_failures: int = 5
        # Per-request-id lifecycle-lock registry (bounded, refcounted).
        self._request_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._request_locks_guard: threading.Lock = threading.Lock()
        self._request_lock_refs: dict[str, int] = {}
        # Background-task sets for the objective / brownfield entry paths.
        self._objective_background_tasks: set[asyncio.Task[None]] = set()
        self._brownfield_background_tasks: set[asyncio.Task[None]] = set()
        # Shutdown flag observable by long-lived subsystems; constructed
        # eagerly so concurrent first-reads share one ``Event``.
        self._shutdown_requested: asyncio.Event = asyncio.Event()

    @override
    def _require_service[T](self, service: T | None, name: str) -> T:
        """Return *service* or raise 503 if not configured.

        Returns:
            The non-``None`` service.

        Raises:
            ServiceUnavailableError: When *service* is ``None``.
        """
        if service is None:
            logger.warning(API_SERVICE_UNAVAILABLE, service=name)
            msg = f"{name.replace('_', ' ').title()} not configured"
            raise ServiceUnavailableError(msg)
        return service

    @property
    def shutdown_requested(self) -> asyncio.Event:
        """Shutdown flag set by the signal handlers; observed by loops.

        Returns:
            The process-shared shutdown ``asyncio.Event``.
        """
        return self._shutdown_requested

    @property
    def bridge_config_applied(self) -> bool:
        """Whether the one-shot bridge-config apply has already run.

        Returns:
            ``True`` once ``mark_bridge_config_applied`` has been called.
        """
        return self._bridge_config_applied

    def mark_bridge_config_applied(self) -> None:
        """Flip :attr:`bridge_config_applied` to ``True`` (one-way)."""
        self._bridge_config_applied = True

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
        adapter: WorkEntryAdapter[Any],
    ) -> None:
        """Install the intake entry adapter only if not already wired."""
        from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

        self.wire_if_field_absent(EngineStateSlice, "intake_entry_adapter", adapter)

    def swap_intake_entry_adapter(self, adapter: WorkEntryAdapter[Any]) -> None:
        """Hot-replace the intake entry adapter (setup-complete reinit)."""
        from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

        self.wire(EngineStateSlice, intake_entry_adapter=adapter)

    def set_objective_entry_adapter_if_absent(
        self,
        adapter: WorkEntryAdapter[Any],
    ) -> None:
        """Install the objective entry adapter only if not already wired."""
        from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

        self.wire_if_field_absent(EngineStateSlice, "objective_entry_adapter", adapter)

    def swap_objective_entry_adapter(
        self,
        adapter: WorkEntryAdapter[Any],
    ) -> None:
        """Hot-replace the objective entry adapter (setup-complete reinit)."""
        from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

        self.wire(EngineStateSlice, objective_entry_adapter=adapter)

    def set_brownfield_entry_adapter_if_absent(
        self,
        adapter: WorkEntryAdapter[Any],
    ) -> None:
        """Install the brownfield entry adapter only if not already wired."""
        from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

        self.wire_if_field_absent(EngineStateSlice, "brownfield_entry_adapter", adapter)

    def swap_brownfield_entry_adapter(
        self,
        adapter: WorkEntryAdapter[Any],
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
