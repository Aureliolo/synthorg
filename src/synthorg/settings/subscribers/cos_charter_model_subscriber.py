"""Chief-of-Staff + charter model subscriber: wire on a model-key change.

The Chief-of-Staff chat / narrator / propose features and the charter interview
wire only behind a resolvable per-feature model. Each model defaults to blank,
so a boot-empty start leaves them unwired; when an operator sets a model in
dashboard Settings this subscriber re-runs their wiring so the feature comes
online with no restart (the wiring is idempotent, so a feature already wired is
left in place). A change before the provider registry is wired is a no-op
(advisory log); the boot / post-setup wiring picks the value up later.

The model VALUE for chat / narrator / propose is read live per call, so a change
between models served by the same provider needs no rebuild -- this subscriber
covers the unwired -> wired transition that the live read cannot do by itself.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.settings.service import SettingsService

logger = get_logger(__name__)

_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        ("chief_of_staff", "chat_model"),
        ("chief_of_staff", "propose_model"),
        ("chief_of_staff", "routing_model"),
        ("chief_of_staff", "narrative_model"),
        ("charter", "interview_model"),
    }
)


class CosCharterModelSettingsSubscriber:
    """Wire the CoS trio + charter on a per-feature model-key change.

    Args:
        app_state: Application state holding the slices + wiring surface.
        settings_service: Settings service the wiring factories read from.
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
        """Return the per-feature model keys this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name for logging."""
        return "cos-charter-model-settings"

    async def on_settings_changed(
        self,
        namespace: str,
        key: str,
    ) -> None:
        """Re-run the CoS trio + charter wiring from current settings.

        Args:
            namespace: Changed setting namespace.
            key: Changed setting key.
        """
        from synthorg.api.lifecycle_helpers.charter_wiring import (  # noqa: PLC0415
            _wire_charter_engine,
        )
        from synthorg.api.lifecycle_helpers.conversational_wiring import (  # noqa: PLC0415
            wire_chief_of_staff_proposer,
            wire_conversational_actor,
        )
        from synthorg.api.lifecycle_helpers.feature_wiring import (  # noqa: PLC0415
            _wire_chief_of_staff_chat,
        )
        from synthorg.api.lifecycle_helpers.narrative_wiring import (  # noqa: PLC0415
            wire_run_narrator,
        )
        from synthorg.api.lifecycle_helpers.refinement_wiring import (  # noqa: PLC0415
            wire_refinement_router,
        )
        from synthorg.approval.state import ApprovalStateSlice  # noqa: PLC0415
        from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
        from synthorg.meta.config import (  # noqa: PLC0415
            load_self_improvement_config,
        )
        from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415
        from synthorg.providers.state import ProvidersStateSlice  # noqa: PLC0415

        registry = self._app_state.slice(ProvidersStateSlice).registry
        if registry is None:
            logger.info(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="no provider registry wired; wiring deferred to boot",
            )
            return
        try:
            si_config = await load_self_improvement_config(self._settings_service)
            cost_tracker = self._app_state.slice(BudgetStateSlice).cost_tracker
            persistence = self._app_state.slice(PersistenceStateSlice).backend
            approval_store = self._app_state.slice(ApprovalStateSlice).store
            await _wire_charter_engine(
                self._app_state,
                provider_registry=registry,
                persistence=persistence,
                cost_tracker=cost_tracker,
                si_config=si_config,
            )
            await _wire_chief_of_staff_chat(
                self._app_state,
                provider_registry=registry,
                cost_tracker=cost_tracker,
                si_config=si_config,
            )
            await wire_run_narrator(
                self._app_state,
                provider_registry=registry,
                cost_tracker=cost_tracker,
                si_config=si_config,
            )
            if approval_store is not None:
                await wire_chief_of_staff_proposer(
                    self._app_state,
                    provider_registry=registry,
                    persistence=persistence,
                    cost_tracker=cost_tracker,
                    effective_approval_store=approval_store,
                    si_config=si_config,
                )
                await wire_refinement_router(self._app_state)
                await wire_conversational_actor(self._app_state, si_config=si_config)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="chief_of_staff_charter",
                trigger_namespace=namespace,
                trigger_key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.info(
            SETTINGS_SUBSCRIBER_NOTIFIED,
            subscriber=self.subscriber_name,
            namespace=namespace,
            key=key,
            note="chief-of-staff + charter wiring re-run",
        )
