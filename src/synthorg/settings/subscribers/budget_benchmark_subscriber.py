"""Budget benchmark-provider settings subscriber.

Rebuilds the cost-dial benchmark-score provider + Pareto analyzer when an
operator edits ``budget.benchmark_provider`` or ``budget.model_tier_overrides``,
then reloads runtime services so the engine routing strategy (which reads the
slice provider at engine-build time) picks up the new provider.
"""

from collections.abc import Sequence

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
)
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import describe_changes

logger = get_logger(__name__)

_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        ("budget", "benchmark_provider"),
        ("budget", "model_tier_overrides"),
    }
)


class BudgetBenchmarkProviderSettingsSubscriber:
    """Rebuild the cost-dial benchmark provider on a watched budget edit.

    Args:
        app_state: Application state owning the budget slice + resolver.
        settings_service: Held for symmetry with peer subscribers.
    """

    def __init__(
        self,
        app_state: AppState,
        settings_service: SettingsService,
    ) -> None:
        self._app_state = app_state
        self._settings_service = settings_service

    @property
    def watched_keys(self) -> frozenset[tuple[str, str]]:
        """Return the ``(namespace, key)`` pairs this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name for logs."""
        return "budget-benchmark-provider"

    async def on_settings_changed(self, changes: Sequence[tuple[str, str]]) -> None:
        """Rebuild the provider + analyzer and reload runtime services.

        One rebuild per batch: the provider is rebuilt from every watched key
        and the runtime reload behind it is the most expensive step here, so
        repeating either per key would redo identical work.

        Args:
            changes: The watched writes this rebuild carries.
        """
        from synthorg.api._benchmark_wiring import (  # noqa: PLC0415
            rebuild_cost_dial_benchmark_provider,
        )
        from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
        from synthorg.workers.runtime_builder import (  # noqa: PLC0415
            reload_runtime_services,
        )

        # Capture the pre-rebuild slice so the two-step hot-reload is atomic:
        # rebuild rewires the slice provider/analyser, but the engine runtime
        # only adopts it on the reload below. If the reload fails the slice is
        # restored, so no component observes a different budget-routing config
        # than the engine still running the old strategy.
        budget_slice = self._app_state.slice(BudgetStateSlice)
        prior_provider = budget_slice.benchmark_provider
        prior_analyzer = budget_slice.pareto_analyzer
        try:
            await rebuild_cost_dial_benchmark_provider(self._app_state)
            await reload_runtime_services(
                self._app_state, trigger=f"setting:{describe_changes(changes)}"
            )
        except Exception as exc:
            reraise_critical(exc)
            self._app_state.wire(
                BudgetStateSlice,
                benchmark_provider=prior_provider,
                pareto_analyzer=prior_analyzer,
            )
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="budget_benchmark_provider",
                trigger=describe_changes(changes),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
