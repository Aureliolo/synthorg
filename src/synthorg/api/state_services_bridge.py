"""Per-op rate-limit / concurrency + bridge-config snapshot primitives.

Hosts the cross-cutting mutable config primitives that a frozen feature
slice cannot own: the per-op rate-limit / concurrency configs and the
``Api`` / ``Workers`` / ``Memory`` bridge-config snapshots (hot-swapped
by the settings subscribers under their per-config locks). Mixed into
``AppState`` directly; the backing attributes are allocated in
``AppState.__slots__`` and initialised in ``AppState.__init__``.
"""

from typing import TYPE_CHECKING

from synthorg.api.rate_limits.config import PerOpRateLimitConfig  # noqa: TC001
from synthorg.api.rate_limits.inflight_config import (
    PerOpConcurrencyConfig,  # noqa: TC001
)
from synthorg.observability import get_logger
from synthorg.observability.events.settings import SETTINGS_SERVICE_SWAPPED
from synthorg.settings.bridge_configs import (  # noqa: TC001
    ApiBridgeConfig,
    MemoryBridgeConfig,
    WorkersBridgeConfig,
)

if TYPE_CHECKING:
    import threading

    from pydantic import BaseModel

logger = get_logger(__name__)


class _BridgeConfigPrimitivesMixin:
    """Mixin hosting per-op + bridge-config snapshot primitives.

    Mixed into ``AppState`` directly. ``_require_service`` is provided by
    the concrete ``AppState`` (the per-op getters surface 503 through it
    before the startup snapshot is applied).
    """

    def _require_service[T](  # pragma: no cover
        self, service: T | None, name: str
    ) -> T:
        """Return *service* or raise (implemented on concrete ``AppState``)."""
        raise NotImplementedError

    # Slot attrs the mixin reads directly (populated on concrete class).
    _per_op_rate_limit_config: PerOpRateLimitConfig | None
    _per_op_concurrency_config: PerOpConcurrencyConfig | None
    _api_bridge_config: ApiBridgeConfig
    _api_bridge_config_lock: threading.Lock
    _workers_bridge_config: WorkersBridgeConfig
    _workers_bridge_config_lock: threading.Lock
    _memory_bridge_config: MemoryBridgeConfig
    _memory_bridge_config_lock: threading.Lock

    @property
    def has_per_op_rate_limit_config(self) -> bool:
        """Check whether the per-op sliding-window config is set.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._per_op_rate_limit_config is not None

    @property
    def per_op_rate_limit_config(self) -> PerOpRateLimitConfig:
        """Return the current per-op sliding-window config or raise 503.

        Returns:
            ``PerOpRateLimitConfig`` instance.
        """
        return self._require_service(
            self._per_op_rate_limit_config,
            "per_op_rate_limit_config",
        )

    def set_per_op_rate_limit_config(
        self,
        config: PerOpRateLimitConfig,
    ) -> None:
        """Attach the per-op sliding-window config at startup (once).

        Guards and middleware read through :attr:`per_op_rate_limit_config`
        at request time, so swapping this reference is how the settings
        subscriber applies runtime overrides without restarting the app.
        """
        self._per_op_rate_limit_config = config

    def swap_per_op_rate_limit_config(
        self,
        config: PerOpRateLimitConfig,
    ) -> None:
        """Replace the per-op sliding-window config (hot-reload).

        Called by the settings subscriber when operators change
        ``api.per_op_rate_limit_enabled`` or
        ``api.per_op_rate_limit_overrides``.  The store itself is not
        rebuilt -- only the config object swaps, so already-queued
        timestamps remain in place and a ``backend`` flip still needs
        a restart (it is marked ``restart_required=True``).
        """
        old_enabled = (
            self._per_op_rate_limit_config.enabled
            if self._per_op_rate_limit_config is not None
            else None
        )
        self._per_op_rate_limit_config = config
        logger.info(
            SETTINGS_SERVICE_SWAPPED,
            service="per_op_rate_limit_config",
            old_enabled=old_enabled,
            new_enabled=config.enabled,
            override_count=len(config.overrides),
        )

    @property
    def has_per_op_concurrency_config(self) -> bool:
        """Check whether the per-op inflight config is set.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._per_op_concurrency_config is not None

    @property
    def per_op_concurrency_config(self) -> PerOpConcurrencyConfig:
        """Return the current per-op inflight config or raise 503.

        Returns:
            ``PerOpConcurrencyConfig`` instance.
        """
        return self._require_service(
            self._per_op_concurrency_config,
            "per_op_concurrency_config",
        )

    def set_per_op_concurrency_config(
        self,
        config: PerOpConcurrencyConfig,
    ) -> None:
        """Attach the per-op inflight config at startup (once).

        Paired swap target for the inflight subscriber path; mirrors
        :meth:`set_per_op_rate_limit_config` so the two per-op guards
        have symmetric wiring.
        """
        self._per_op_concurrency_config = config

    def swap_per_op_concurrency_config(
        self,
        config: PerOpConcurrencyConfig,
    ) -> None:
        """Replace the per-op inflight config (hot-reload).

        Called by the settings subscriber on
        ``api.per_op_concurrency_enabled`` or
        ``api.per_op_concurrency_overrides`` change.  The inflight
        store keeps its counters -- only the enforcement config
        changes.
        """
        old_enabled = (
            self._per_op_concurrency_config.enabled
            if self._per_op_concurrency_config is not None
            else None
        )
        self._per_op_concurrency_config = config
        logger.info(
            SETTINGS_SERVICE_SWAPPED,
            service="per_op_concurrency_config",
            old_enabled=old_enabled,
            new_enabled=config.enabled,
            override_count=len(config.overrides),
        )

    @property
    def api_bridge_config(self) -> ApiBridgeConfig:
        """Return the current ``ApiBridgeConfig`` snapshot.

        Always non-None: ``__init__`` default-constructs an
        ``ApiBridgeConfig()`` so consumers see valid defaults even
        before ``_apply_bridge_config`` runs or when the resolver is
        unreachable.  Operator overrides land via
        :meth:`swap_api_bridge_config` from the startup snapshot path
        and :meth:`mutate_api_bridge_config` from the
        ``ApiBridgeSettingsSubscriber`` hot-reload path.

        Returns:
            ``ApiBridgeConfig`` instance.
        """
        return self._api_bridge_config

    def _swap_bridge_config(
        self,
        *,
        lock: threading.Lock,
        attr: str,
        service: str,
        config: BaseModel,
    ) -> None:
        """Replace a bridge-config snapshot wholesale under its lock.

        Shared body for the ``api`` / ``workers`` / ``memory`` swap
        accessors. Acquiring *lock* keeps a concurrent ``mutate_*``
        from interleaving its read with this assignment and losing the
        partial update.
        """
        with lock:
            previous: BaseModel = getattr(self, attr)
            setattr(self, attr, config)
        if previous is config:
            return
        prev_fields = previous.model_dump()
        new_fields = config.model_dump()
        changed = sorted(k for k in new_fields if prev_fields.get(k) != new_fields[k])
        logger.info(
            SETTINGS_SERVICE_SWAPPED,
            service=service,
            transition="swap",
            changed_fields=changed,
        )

    def _mutate_bridge_config(
        self,
        *,
        lock: threading.Lock,
        attr: str,
        service: str,
        updates: dict[str, object],
    ) -> None:
        """Re-validate ``updates`` onto a bridge snapshot under its lock.

        Shared body for the ``api`` / ``workers`` / ``memory`` mutate
        accessors. Re-validation is forced via ``model_validate(...)``
        rather than ``model_copy(update=...)`` because Pydantic v2
        skips validators on the bare ``update=`` path -- an
        out-of-range operator value would otherwise land silently in
        the snapshot. Re-validation raises ``ValidationError``, leaving
        the prior snapshot in place and propagating the failure to the
        subscriber's error log. The whole read-modify-write runs inside
        *lock* so two concurrent operator edits cannot both build from
        the same prior value and lose each other's update.
        """
        with lock:
            previous: BaseModel = getattr(self, attr)
            merged = previous.model_dump()
            merged.update(updates)
            new_config = type(previous).model_validate(merged)
            setattr(self, attr, new_config)
        logger.info(
            SETTINGS_SERVICE_SWAPPED,
            service=service,
            transition="mutate",
            changed_fields=sorted(updates),
        )

    def swap_api_bridge_config(self, config: ApiBridgeConfig) -> None:
        """Replace the ``ApiBridgeConfig`` snapshot wholesale.

        Used by ``_apply_bridge_config`` at startup with the value
        resolved through ``ConfigResolver.get_api_bridge_config`` (full
        snapshot, not a diff).  Hot-reload paths must use
        :meth:`mutate_api_bridge_config` instead so the read-modify-
        write is serialised against concurrent updates.

        Acquires ``_api_bridge_config_lock`` so a concurrent
        ``mutate_api_bridge_config`` cannot interleave its read with
        this assignment and lose the partial update.
        """
        self._swap_bridge_config(
            lock=self._api_bridge_config_lock,
            attr="_api_bridge_config",
            service="api_bridge_config",
            config=config,
        )

    def mutate_api_bridge_config(self, updates: dict[str, object]) -> None:
        """Apply ``updates`` to the current snapshot under a lock.

        Combines a re-validating partial update and the swap into a
        single critical section so two concurrent operator edits cannot
        both build a new snapshot from the same prior value and lose
        each other's update.  The watched-key check in
        :class:`~synthorg.settings.subscribers.api_bridge_subscriber.ApiBridgeSettingsSubscriber`
        already restricts ``updates`` to fields declared on
        ``ApiBridgeConfig``.

        Re-validation is forced via ``model_validate(<dict>)`` rather
        than ``model_copy(update=...)`` because Pydantic v2 skips
        validators on the bare ``update=`` path -- an out-of-range
        operator-supplied value (e.g. ``50`` against
        ``Field(ge=100, le=1_000_000)``) would otherwise land silently
        in the snapshot.  Re-validation raises ``ValidationError``,
        leaving the prior snapshot in place and propagating the failure
        to the subscriber's error log.
        """
        self._mutate_bridge_config(
            lock=self._api_bridge_config_lock,
            attr="_api_bridge_config",
            service="api_bridge_config",
            updates=updates,
        )

    @property
    def workers_bridge_config(self) -> WorkersBridgeConfig:
        """Return the current ``WorkersBridgeConfig`` snapshot.

        Always non-None: ``__init__`` default-constructs a
        ``WorkersBridgeConfig()`` (Field defaults == the registered
        ``workers.*`` defaults) so a dispatcher built before
        ``_apply_bridge_config`` or under a resolver outage still
        observes the documented retry budget.

        Returns:
            ``WorkersBridgeConfig`` instance.
        """
        return self._workers_bridge_config

    def swap_workers_bridge_config(self, config: WorkersBridgeConfig) -> None:
        """Replace the ``WorkersBridgeConfig`` snapshot wholesale.

        Used by ``_apply_bridge_config`` at startup with the value
        resolved through ``ConfigResolver.get_workers_bridge_config``.
        Hot-reload paths must use :meth:`mutate_workers_bridge_config`.
        """
        self._swap_bridge_config(
            lock=self._workers_bridge_config_lock,
            attr="_workers_bridge_config",
            service="workers_bridge_config",
            config=config,
        )

    def mutate_workers_bridge_config(self, updates: dict[str, object]) -> None:
        """Apply ``updates`` to the workers snapshot under a lock.

        Re-validates via ``model_validate`` so an out-of-range operator
        value raises ``ValidationError`` and the prior snapshot is
        retained (mirrors :meth:`mutate_api_bridge_config`).
        """
        self._mutate_bridge_config(
            lock=self._workers_bridge_config_lock,
            attr="_workers_bridge_config",
            service="workers_bridge_config",
            updates=updates,
        )

    @property
    def memory_bridge_config(self) -> MemoryBridgeConfig:
        """Return the current ``MemoryBridgeConfig`` snapshot.

        Always non-None: ``__init__`` default-constructs a
        ``MemoryBridgeConfig()`` (Field defaults == the registered
        ``memory.*`` defaults) so a consumer built before
        ``_apply_bridge_config`` or under a resolver outage still
        observes the documented consolidation / fine-tune preflight
        defaults.

        Returns:
            ``MemoryBridgeConfig`` instance.
        """
        return self._memory_bridge_config

    def swap_memory_bridge_config(self, config: MemoryBridgeConfig) -> None:
        """Replace the ``MemoryBridgeConfig`` snapshot wholesale.

        Used by ``_apply_bridge_config`` at startup with the value
        resolved through ``ConfigResolver.get_memory_bridge_config``.
        Hot-reload paths must use :meth:`mutate_memory_bridge_config`.
        """
        self._swap_bridge_config(
            lock=self._memory_bridge_config_lock,
            attr="_memory_bridge_config",
            service="memory_bridge_config",
            config=config,
        )

    def mutate_memory_bridge_config(self, updates: dict[str, object]) -> None:
        """Apply ``updates`` to the memory snapshot under a lock.

        Re-validates via ``model_validate`` so an out-of-range operator
        value raises ``ValidationError`` and the prior snapshot is
        retained (mirrors :meth:`mutate_api_bridge_config`).
        """
        self._mutate_bridge_config(
            lock=self._memory_bridge_config_lock,
            attr="_memory_bridge_config",
            service="memory_bridge_config",
            updates=updates,
        )
