# module-kind: code
"""Bridge-config snapshot appliers for the startup config-apply step.

Extracted from :mod:`synthorg.api.lifecycle_helpers.config_apply` to keep that
orchestrator under its size budget. Each applier resolves one frozen
``*BridgeConfig`` snapshot once via the wired ``ConfigResolver`` and atomically
swaps it onto ``AppState`` (DB > env > default), retaining the construction-time
default on any non-fatal resolve failure (the fail-safe rule: a settings-backend
hiccup must never perturb the live config).
"""

import asyncio
from collections.abc import Awaitable, Callable

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_BRIDGE_CONFIG_RESOLVE_FAILED
from synthorg.settings.state import SettingsStateSlice, config_resolver_of

logger = get_logger(__name__)


async def _apply_bridge_snapshot[T](
    app_state: AppState,
    *,
    bridge: str,
    getter: Callable[[], Awaitable[T]],
    setter: Callable[[T], None],
) -> None:
    """Resolve a bridge-config snapshot once and atomically swap it in.

    Shared body for the ``api`` / ``workers`` / ``memory`` / ``observability``
    snapshot appliers. On any non-fatal resolve failure the default snapshot
    installed by ``AppState.__init__`` is retained and a single structured
    warning is emitted.

    No-op when no resolver is wired (dev/test rigs that bypass ``create_app``);
    ``getter`` is only invoked after that guard so binding it to
    ``config_resolver_of(app_state)`` stays safe.

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    if app_state.slice(SettingsStateSlice).config_resolver is None:
        return
    try:
        snapshot = await getter()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_BRIDGE_CONFIG_RESOLVE_FAILED,
            bridge=bridge,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback="module_defaults",
        )
        return
    setter(snapshot)


async def _apply_api_bridge_config_snapshot(app_state: AppState) -> None:
    """Snapshot ``ApiBridgeConfig`` onto ``AppState`` at startup.

    Resolves the full bridge once via
    :meth:`ConfigResolver.get_api_bridge_config` and atomically swaps it onto
    ``app_state``. On any non-fatal resolve failure the default
    ``ApiBridgeConfig()`` snapshot installed by ``AppState.__init__`` is
    retained and a single structured warning is emitted.

    No-op when no resolver is wired (dev/test rigs that bypass ``create_app``);
    the default snapshot remains in place.
    """
    await _apply_bridge_snapshot(
        app_state,
        bridge="api",
        # Lambda is required: ``config_resolver`` raises until wired, so
        # access must defer past the helper's has_config_resolver guard.
        getter=lambda: config_resolver_of(app_state).get_api_bridge_config(),
        setter=app_state.bridge_config.swap_api,
    )


async def _apply_workers_bridge_config_snapshot(app_state: AppState) -> None:
    """Snapshot ``WorkersBridgeConfig`` onto ``AppState`` at startup.

    Resolves the dispatcher retry budget once via
    :meth:`ConfigResolver.get_workers_bridge_config` and atomically swaps it
    onto ``app_state`` so ``DistributedDispatcher`` observes operator-tuned
    values. On any non-fatal resolve failure the default
    ``WorkersBridgeConfig()`` snapshot (Field defaults == registered
    ``workers.*`` defaults) is retained.

    No-op when no resolver is wired.
    """
    await _apply_bridge_snapshot(
        app_state,
        bridge="workers",
        # Lambda is required: ``config_resolver`` raises until wired, so
        # access must defer past the helper's has_config_resolver guard.
        getter=lambda: config_resolver_of(app_state).get_workers_bridge_config(),
        setter=app_state.bridge_config.swap_workers,
    )


async def _apply_memory_bridge_config_snapshot(app_state: AppState) -> None:
    """Snapshot ``MemoryBridgeConfig`` onto ``AppState`` at startup.

    Resolves the consolidation enforce-batch + fine-tune preflight knobs once
    via :meth:`ConfigResolver.get_memory_bridge_config` and atomically swaps the
    result onto ``app_state`` so memory consumers observe operator-tuned values.
    On any non-fatal resolve failure the default ``MemoryBridgeConfig()``
    snapshot (Field defaults == registered ``memory.*`` defaults) is retained.

    No-op when no resolver is wired.
    """
    await _apply_bridge_snapshot(
        app_state,
        bridge="memory",
        # Lambda is required: ``config_resolver`` raises until wired, so
        # access must defer past the helper's has_config_resolver guard.
        getter=lambda: config_resolver_of(app_state).get_memory_bridge_config(),
        setter=app_state.bridge_config.swap_memory,
    )


async def _apply_observability_bridge_config_snapshot(app_state: AppState) -> None:
    """Snapshot ``ObservabilityBridgeConfig`` onto ``AppState`` at startup.

    Resolves the HTTP-handler / audit-chain / TSA-endpoint knobs once via
    :meth:`ConfigResolver.get_observability_bridge_config` and atomically swaps
    the result onto ``app_state`` so observability consumers read the
    operator-tuned snapshot (DB > env > default). On any non-fatal resolve
    failure the default ``ObservabilityBridgeConfig()`` snapshot is retained.

    The HTTP-batch and TSA-endpoint fields are baked into their handlers at
    ``configure_logging`` time (pre-resolver), so their DB values apply on the
    next restart; ``audit_chain_signing_timeout_seconds`` is the one
    live-settable field and is pushed onto the sink separately.

    No-op when no resolver is wired.
    """
    await _apply_bridge_snapshot(
        app_state,
        bridge="observability",
        # Lambda is required: ``config_resolver`` raises until wired, so
        # access must defer past the helper's has_config_resolver guard.
        getter=lambda: config_resolver_of(app_state).get_observability_bridge_config(),
        setter=app_state.bridge_config.swap_observability,
    )


__all__ = [
    "_apply_api_bridge_config_snapshot",
    "_apply_memory_bridge_config_snapshot",
    "_apply_observability_bridge_config_snapshot",
    "_apply_workers_bridge_config_snapshot",
]
