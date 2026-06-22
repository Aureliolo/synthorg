# module-kind: code
"""Periodic driver for the model-refresh reconcile cycle.

The delicate loop-bound lifecycle (primitives rebound to the running loop,
bounded stop-drain) lives once in
:class:`~synthorg.core.scheduler.AsyncCycleScheduler`; this subclass keeps
its mode discriminator and ``reset_primitives_on_stop=False`` rebind-race
guard. Every tick re-reads the live ``providers.model_refresh_mode``
discriminator (fail-safe to ``OFF``) so an operator can pause or change mode
without a restart; the cadence work also reads the in-family auto-apply flag
and threads the api-layer apply hook.
"""

from typing import Final, override

from synthorg.core.scheduler import AsyncCycleScheduler
from synthorg.observability import get_logger
from synthorg.observability.events.provider import (
    PROVIDER_MODEL_REFRESH_CYCLE_FAILED,
    PROVIDER_MODEL_REFRESH_CYCLE_RAN,
    PROVIDER_MODEL_REFRESH_STARTED,
    PROVIDER_MODEL_REFRESH_STOPPED,
)
from synthorg.providers.management.model_refresh_service import (
    ApplyHook,
    ModelRefreshService,
)
from synthorg.providers.management.refresh_config import (
    RefreshMode,
    resolve_refresh_mode,
)
from synthorg.settings.kill_switch import resolve_bool_with_fallback
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_AUTO_APPLY_NS: Final[str] = "providers"
_AUTO_APPLY_KEY: Final[str] = "model_refresh_auto_apply_within_family"

_SCHEDULED_MODES: Final[frozenset[RefreshMode]] = frozenset(
    {RefreshMode.DETECT_ONLY, RefreshMode.RECONCILE_RECOMMEND},
)


class ModelRefreshScheduler(AsyncCycleScheduler):
    """Periodic background driver that runs the model-refresh cycle."""

    def __init__(
        self,
        service: ModelRefreshService,
        *,
        interval_seconds: float,
        config_resolver: ConfigResolver,
        apply_recommendation: ApplyHook | None = None,
    ) -> None:
        """Initialise the scheduler.

        Args:
            service: The model-refresh service whose ``run_cycle`` is driven.
            interval_seconds: Cadence between cycles; must be >= 60 seconds.
            config_resolver: Resolver for the live mode + auto-apply flag,
                re-read every tick so an operator can retune at runtime.
            apply_recommendation: Optional api-layer hook used when the
                in-family auto-apply flag is set.

        Raises:
            ValueError: If ``interval_seconds`` is below the minimum.
        """
        super().__init__(
            interval_seconds=interval_seconds,
            task_name="model-refresh-scheduler",
            started_event=PROVIDER_MODEL_REFRESH_STARTED,
            stopped_event=PROVIDER_MODEL_REFRESH_STOPPED,
            failed_event=PROVIDER_MODEL_REFRESH_CYCLE_FAILED,
            reset_primitives_on_stop=False,
        )
        self._service = service
        self._config_resolver = config_resolver
        self._apply_recommendation = apply_recommendation

    @override
    async def _run_cycle_once(self) -> None:
        """Run one reconcile cycle under the live mode (or skip when off).

        The mode discriminator replaces the bool kill-switch: a tick reads
        ``providers.model_refresh_mode`` and only reconciles under a
        scheduled mode, logging a skip otherwise. A systemic failure is
        logged and survived by the base loop.
        """
        mode = await resolve_refresh_mode(self._config_resolver)
        if mode not in _SCHEDULED_MODES:
            logger.debug(
                PROVIDER_MODEL_REFRESH_CYCLE_RAN,
                note="skipped_by_mode",
                mode=mode.value,
            )
            return
        auto_apply = await resolve_bool_with_fallback(
            resolver=self._config_resolver,
            namespace=_AUTO_APPLY_NS,
            key=_AUTO_APPLY_KEY,
            fallback=False,
        )
        await self._service.run_cycle(
            mode=mode,
            auto_apply=auto_apply,
            apply_recommendation=self._apply_recommendation,
        )


__all__ = ["ModelRefreshScheduler"]
