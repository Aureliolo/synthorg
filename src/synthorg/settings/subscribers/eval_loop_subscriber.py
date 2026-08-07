"""Eval-loop settings subscriber: hot-swaps the IDENTIFY/PROPOSE strategies.

The closed-loop evaluation coordinator builds its provider-backed pattern
identifier + fix proposer from the ``hr.eval_loop_llm_model`` (a provider +
model reference) / ``eval_loop_pattern_identifier_mode`` /
``eval_loop_fix_proposer_mode`` keys at boot. A change to any of those re-resolves
those choices from the live settings DB, rebuilds the strategies (degrading
to deterministic when a model / provider is unavailable), and swaps them onto the
wired coordinator so the next cycle uses them, with no restart. A no-op when the
coordinator is not wired (the eval loop is off / unavailable). The cycle
enable / interval / window knobs are re-read live per tick by the scheduler and
need no subscriber.
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
        ("hr", "eval_loop_llm_model"),
        ("hr", "eval_loop_pattern_identifier_mode"),
        ("hr", "eval_loop_fix_proposer_mode"),
    }
)


class EvalLoopSettingsSubscriber:
    """Rebuild + swap the eval-loop pattern strategies on a model/mode change.

    Args:
        app_state: Application state holding the coordinator, provider registry,
            and config resolver.
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
        """Return the ``hr.eval_loop_*`` model/mode keys this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name for logging."""
        return "eval-loop-settings"

    async def on_settings_changed(self, changes: Sequence[tuple[str, str]]) -> None:
        """Re-resolve + swap the eval-loop pattern strategies.

        One swap per batch: the reload re-reads every watched key, so
        repeating it per key would swap in the same strategies several times.

        Args:
            changes: The watched writes this swap carries.
        """
        from synthorg.api.lifecycle_helpers.eval_loop_wiring import (  # noqa: PLC0415
            reload_eval_loop_pattern_strategies,
        )
        from synthorg.providers.state import ProvidersStateSlice  # noqa: PLC0415

        registry = self._app_state.slice(ProvidersStateSlice).registry
        try:
            await reload_eval_loop_pattern_strategies(
                self._app_state, provider_registry=registry
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="eval_loop_pattern_strategies",
                trigger=describe_changes(changes),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
