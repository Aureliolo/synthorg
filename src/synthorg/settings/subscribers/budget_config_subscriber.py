"""Budget-config settings subscriber.

The ``BudgetConfig`` the enforcer holds is built once, at boot, through
the bootstrap resolver, which reads env and code defaults and cannot see
the settings store. Every limit on it therefore froze at boot: an
operator raising the monthly total, tightening a per-agent daily limit,
or raising a per-run ceiling to unpark a halted run saw the write
persist and render, and bind nothing at all until the process was
restarted.

This re-resolves the whole config through the live resolver on any
watched write and hands it to the enforcer, which is the one component
that decides whether a run may continue.
"""

from collections.abc import Sequence

from synthorg.api.state import AppState
from synthorg.budget.config import BudgetConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
)
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import describe_changes

logger = get_logger(__name__)

#: Every key ``ConfigResolver.get_budget_config`` resolves. Kept in step
#: with that method rather than with ``BudgetConfig``'s field list: a
#: field the resolver does not read cannot change on a rebuild, so
#: watching its key would schedule work that changes nothing.
_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        ("budget", "total_monthly"),
        ("budget", "per_task_limit"),
        ("budget", "per_agent_daily_limit"),
        ("budget", "reset_day"),
        ("budget", "alert_warn_at"),
        ("budget", "alert_critical_at"),
        ("budget", "alert_hard_stop_at"),
        ("budget", "currency"),
        ("budget", "run_hard_ceiling"),
        ("budget", "run_hard_token_ceiling"),
        ("budget", "session_token_ceiling"),
    }
)


class BudgetConfigSettingsSubscriber:
    """Push a re-resolved ``BudgetConfig`` into the running enforcer.

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
        return "budget-config"

    async def on_settings_changed(self, changes: Sequence[tuple[str, str]]) -> None:
        """Re-resolve the config and adopt it on the slice + enforcer.

        One re-resolve per batch: the resolver assembles every field in a
        single pass, so a per-key rebuild would repeat identical work.

        Args:
            changes: The watched writes this rebuild carries.
        """
        from synthorg.budget.enforcer import BudgetEnforcer  # noqa: PLC0415
        from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
        from synthorg.settings.state import config_resolver_of  # noqa: PLC0415

        budget_slice = self._app_state.slice(BudgetStateSlice)
        enforcer = budget_slice.budget_enforcer
        prior_config = budget_slice.budget_config
        try:
            resolved = await config_resolver_of(self._app_state).get_budget_config()
            self._app_state.wire(BudgetStateSlice, budget_config=resolved)
            if isinstance(enforcer, BudgetEnforcer):
                enforcer.set_budget_config(resolved)
            # The enforcer is not the only holder. The tracker's copy decides
            # which currency it accepts records in and is what the summaries
            # and budget gauges are computed from; the optimizer scores every
            # recommendation against its own. A write adopted by one of the
            # three leaves the other two enforcing the boot config.
            self._adopt_on_budget_readers(resolved)
        except Exception as exc:
            reraise_critical(exc)
            # Restore the slice so no reader observes a config the
            # enforcer never adopted.
            if prior_config is not None:
                self._app_state.wire(BudgetStateSlice, budget_config=prior_config)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="budget_config",
                trigger=describe_changes(changes),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

    def _adopt_on_budget_readers(self, resolved: BudgetConfig) -> None:
        """Hand *resolved* to the tracker and optimizer, when wired.

        Both are optional on the slice (a harness runs without them), and
        both are protocol-typed there, so each is offered the setter and
        skipped when its implementation does not carry one.

        Args:
            resolved: The freshly resolved configuration.
        """
        from synthorg.budget.optimizer import CostOptimizer  # noqa: PLC0415
        from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
        from synthorg.budget.tracker import CostTracker  # noqa: PLC0415

        budget_slice = self._app_state.slice(BudgetStateSlice)
        tracker = budget_slice.cost_tracker
        if isinstance(tracker, CostTracker):
            tracker.set_budget_config(resolved)
        optimizer = budget_slice.cost_optimizer
        if isinstance(optimizer, CostOptimizer):
            optimizer.set_budget_config(resolved)
