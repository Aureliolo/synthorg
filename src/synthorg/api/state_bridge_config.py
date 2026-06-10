"""Bridge-config snapshot primitives.

Owns the cross-cutting mutable config snapshots a frozen feature slice
cannot hold: the ``Api`` / ``Workers`` / ``Memory`` bridge-config
snapshots (hot-swapped by the settings subscribers under their per-config
locks) and the one-shot "bridge config applied" flag. Composed onto
``AppState`` as ``app_state.bridge_config``.
"""

import threading

from pydantic import BaseModel

from synthorg.observability import get_logger
from synthorg.observability.events.settings import SETTINGS_SERVICE_SWAPPED
from synthorg.settings.bridge_configs import (
    ApiBridgeConfig,
    MemoryBridgeConfig,
    WorkersBridgeConfig,
)

logger = get_logger(__name__)


class BridgeConfigState:
    """``Api`` / ``Workers`` / ``Memory`` bridge-config snapshots.

    Each snapshot is default-constructed so consumers see valid defaults
    before ``_apply_bridge_config`` runs; each lock guards its
    ``mutate_*`` read-modify-write. The one-shot ``applied`` flag lets a
    re-entered Litestar lifespan (shared-app test fixtures) skip the
    apply exactly once per lifetime.
    """

    __slots__ = (
        "_api",
        "_api_lock",
        "_applied",
        "_memory",
        "_memory_lock",
        "_workers",
        "_workers_lock",
    )

    def __init__(self) -> None:
        """Default-construct each snapshot + its lock; applied flag unset."""
        self._api: ApiBridgeConfig = ApiBridgeConfig()
        self._api_lock: threading.Lock = threading.Lock()
        self._workers: WorkersBridgeConfig = WorkersBridgeConfig()
        self._workers_lock: threading.Lock = threading.Lock()
        self._memory: MemoryBridgeConfig = MemoryBridgeConfig()
        self._memory_lock: threading.Lock = threading.Lock()
        # One-shot flag: bridge config applied exactly once per lifetime
        # even across re-entered lifespans (shared-app test fixtures).
        self._applied: bool = False

    @property
    def applied(self) -> bool:
        """Whether the one-shot bridge-config apply has already run.

        Returns:
            ``True`` once :meth:`mark_applied` has been called.
        """
        return self._applied

    def mark_applied(self) -> None:
        """Flip :attr:`applied` to ``True`` (one-way)."""
        self._applied = True

    def _swap(
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

    def _mutate(
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

    @property
    def api(self) -> ApiBridgeConfig:
        """Return the current ``ApiBridgeConfig`` snapshot.

        Always non-None: ``__init__`` default-constructs an
        ``ApiBridgeConfig()`` so consumers see valid defaults even
        before ``_apply_bridge_config`` runs or when the resolver is
        unreachable.  Operator overrides land via :meth:`swap_api` from
        the startup snapshot path and :meth:`mutate_api` from the
        ``ApiBridgeSettingsSubscriber`` hot-reload path.

        Returns:
            ``ApiBridgeConfig`` instance.
        """
        return self._api

    def swap_api(self, config: ApiBridgeConfig) -> None:
        """Replace the ``ApiBridgeConfig`` snapshot wholesale.

        Used by ``_apply_bridge_config`` at startup with the value
        resolved through ``ConfigResolver.get_api_bridge_config`` (full
        snapshot, not a diff).  Hot-reload paths must use :meth:`mutate_api`
        instead so the read-modify-write is serialised against concurrent
        updates.
        """
        self._swap(
            lock=self._api_lock,
            attr="_api",
            service="api_bridge_config",
            config=config,
        )

    def mutate_api(self, updates: dict[str, object]) -> None:
        """Apply ``updates`` to the current API snapshot under a lock.

        Combines a re-validating partial update and the swap into a
        single critical section so two concurrent operator edits cannot
        both build a new snapshot from the same prior value and lose
        each other's update.  The watched-key check in
        :class:`~synthorg.settings.subscribers.api_bridge_subscriber.ApiBridgeSettingsSubscriber`
        already restricts ``updates`` to fields declared on
        ``ApiBridgeConfig``.
        """
        self._mutate(
            lock=self._api_lock,
            attr="_api",
            service="api_bridge_config",
            updates=updates,
        )

    @property
    def workers(self) -> WorkersBridgeConfig:
        """Return the current ``WorkersBridgeConfig`` snapshot.

        Always non-None: ``__init__`` default-constructs a
        ``WorkersBridgeConfig()`` (Field defaults == the registered
        ``workers.*`` defaults) so a dispatcher built before
        ``_apply_bridge_config`` or under a resolver outage still
        observes the documented retry budget.

        Returns:
            ``WorkersBridgeConfig`` instance.
        """
        return self._workers

    def swap_workers(self, config: WorkersBridgeConfig) -> None:
        """Replace the ``WorkersBridgeConfig`` snapshot wholesale.

        Used by ``_apply_bridge_config`` at startup with the value
        resolved through ``ConfigResolver.get_workers_bridge_config``.
        Hot-reload paths must use :meth:`mutate_workers`.
        """
        self._swap(
            lock=self._workers_lock,
            attr="_workers",
            service="workers_bridge_config",
            config=config,
        )

    def mutate_workers(self, updates: dict[str, object]) -> None:
        """Apply ``updates`` to the workers snapshot under a lock.

        Re-validates via ``model_validate`` so an out-of-range operator
        value raises ``ValidationError`` and the prior snapshot is
        retained (mirrors :meth:`mutate_api`).
        """
        self._mutate(
            lock=self._workers_lock,
            attr="_workers",
            service="workers_bridge_config",
            updates=updates,
        )

    @property
    def memory(self) -> MemoryBridgeConfig:
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
        return self._memory

    def swap_memory(self, config: MemoryBridgeConfig) -> None:
        """Replace the ``MemoryBridgeConfig`` snapshot wholesale.

        Used by ``_apply_bridge_config`` at startup with the value
        resolved through ``ConfigResolver.get_memory_bridge_config``.
        Hot-reload paths must use :meth:`mutate_memory`.
        """
        self._swap(
            lock=self._memory_lock,
            attr="_memory",
            service="memory_bridge_config",
            config=config,
        )

    def mutate_memory(self, updates: dict[str, object]) -> None:
        """Apply ``updates`` to the memory snapshot under a lock.

        Re-validates via ``model_validate`` so an out-of-range operator
        value raises ``ValidationError`` and the prior snapshot is
        retained (mirrors :meth:`mutate_api`).
        """
        self._mutate(
            lock=self._memory_lock,
            attr="_memory",
            service="memory_bridge_config",
            updates=updates,
        )
