# module-kind: service
"""Settings-backed wiring for the model tier-assignment service.

The per-model tier overrides live in the ``providers.tier_assignment_overrides``
setting (DB > env > code). The heuristic layer is recomputed from live
capability metadata, so only overrides are persisted.
"""

from typing import TYPE_CHECKING

from synthorg.budget.state import BudgetStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import PROVIDER_TIER_LLM_RECOMMENDED
from synthorg.observability.events.settings import (
    SETTINGS_FETCH_FAILED,
    SETTINGS_SET_FAILED,
)
from synthorg.providers.model_binding import resolve_ref_provider
from synthorg.providers.tier_assignment.errors import (
    TierClassifierDisabledError,
    TierClassifierModelUnsetError,
    TierClassifierProviderUnavailableError,
    TierOverrideStoreReadOnlyError,
)
from synthorg.providers.tier_assignment.llm_recommender import LlmTierRecommender
from synthorg.providers.tier_assignment.models import (
    TIER_ASSIGNMENT_SCHEMA_VERSION,
    TierAssignmentMap,
)
from synthorg.providers.tier_assignment.service import TierAssignmentService
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.model_ref import parse_model_ref
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.state import SettingsStateSlice

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_NAMESPACE = SettingNamespace.PROVIDERS.value
_KEY = "tier_assignment_overrides"
_CLASSIFIER_MODEL_KEY = "tier_classifier_model"
_CLASSIFIER_ENABLED_KEY = "tier_classifier_enabled"


class SettingsTierOverrideStore:
    """Persists the tier-override envelope in the settings system.

    ``load`` reads the ``providers.tier_assignment_overrides`` JSON through the
    config resolver and falls back to an empty map on any read / validation
    failure (a corrupt blob must not crash boot). ``save`` writes it through the
    settings service; a store built without a settings service is read-only and
    ``save`` raises.

    Args:
        resolver: Config resolver for reads (``None`` yields an empty map).
        settings_service: Settings service for writes (``None`` is read-only).
    """

    __slots__ = ("_resolver", "_settings_service")

    def __init__(
        self,
        *,
        resolver: ConfigResolver | None,
        settings_service: SettingsService | None,
    ) -> None:
        self._resolver = resolver
        self._settings_service = settings_service

    async def load(self) -> TierAssignmentMap:
        """Return the persisted override map (empty on any read failure)."""
        if self._resolver is None:
            return TierAssignmentMap()
        try:
            raw = await self._resolver.get_json(_NAMESPACE, _KEY)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- a corrupt/unreadable overrides blob must
            # not crash boot; fall back to an empty map (heuristic-only routing).
            reraise_critical(exc)
            logger.warning(
                SETTINGS_FETCH_FAILED,
                namespace=_NAMESPACE,
                key=_KEY,
                reason="tier_overrides_read_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return TierAssignmentMap()
        if raw is None:
            return TierAssignmentMap()
        try:
            envelope = TierAssignmentMap.model_validate(raw)
        except ValueError as exc:
            logger.warning(
                SETTINGS_FETCH_FAILED,
                namespace=_NAMESPACE,
                key=_KEY,
                reason="tier_overrides_invalid_schema",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return TierAssignmentMap()
        if envelope.schema_version != TIER_ASSIGNMENT_SCHEMA_VERSION:
            logger.warning(
                SETTINGS_FETCH_FAILED,
                namespace=_NAMESPACE,
                key=_KEY,
                reason="tier_overrides_unknown_version",
                found_version=envelope.schema_version,
                expected_version=TIER_ASSIGNMENT_SCHEMA_VERSION,
            )
            return TierAssignmentMap()
        return envelope

    async def save(self, overrides: TierAssignmentMap) -> None:
        """Persist *overrides* through the settings service.

        Raises:
            TierOverrideStoreReadOnlyError: When the store was built without a
                settings service (read-only), so an override cannot be
                persisted.
        """
        if self._settings_service is None:
            logger.warning(
                SETTINGS_SET_FAILED,
                namespace=_NAMESPACE,
                key=_KEY,
                reason="tier_override_store_read_only",
            )
            raise TierOverrideStoreReadOnlyError
        await self._settings_service.set(
            _NAMESPACE,
            _KEY,
            overrides.model_dump_json(),
        )


def build_tier_assignment_service(app_state: AppState) -> TierAssignmentService:
    """Build a :class:`TierAssignmentService` from live application state.

    Returns:
        A service backed by the settings-persisted override store, the
        heuristic classifier, and the application clock.
    """
    slice_ = app_state.slice(SettingsStateSlice)
    store = SettingsTierOverrideStore(
        resolver=slice_.config_resolver,
        settings_service=slice_.settings_service,
    )
    return TierAssignmentService(store=store, clock=app_state.clock)


async def build_tier_recommender(app_state: AppState) -> LlmTierRecommender:
    """Build the LLM tier recommender from the classifier settings.

    The recommender is opt-in: it runs only when
    ``providers.tier_classifier_enabled`` is on and
    ``providers.tier_classifier_model`` names a registered provider. Each
    precondition raises a distinct error so the caller can tell the operator
    exactly what to fix (enable the feature, pick a model, or restore the
    provider) rather than conflating them.

    Returns:
        An :class:`LlmTierRecommender` bound to the configured classifier
        provider + model.

    Raises:
        TierClassifierModelUnsetError: When no settings backend is wired or no
            classifier model is configured.
        TierClassifierDisabledError: When the recommender opt-in is off.
        TierClassifierProviderUnavailableError: When the configured model names
            a provider that is not registered.
    """
    resolver = app_state.slice(SettingsStateSlice).config_resolver
    if resolver is None:
        logger.warning(
            PROVIDER_TIER_LLM_RECOMMENDED,
            namespace=_NAMESPACE,
            reason="classifier_no_settings_backend",
        )
        raise TierClassifierModelUnsetError
    if not await resolver.get_bool(_NAMESPACE, _CLASSIFIER_ENABLED_KEY):
        logger.warning(
            PROVIDER_TIER_LLM_RECOMMENDED,
            namespace=_NAMESPACE,
            key=_CLASSIFIER_ENABLED_KEY,
            reason="classifier_disabled",
        )
        raise TierClassifierDisabledError
    ref = parse_model_ref(await resolver.get_str(_NAMESPACE, _CLASSIFIER_MODEL_KEY))
    if not ref.model_id.strip():
        logger.warning(
            PROVIDER_TIER_LLM_RECOMMENDED,
            namespace=_NAMESPACE,
            key=_CLASSIFIER_MODEL_KEY,
            reason="classifier_model_unset",
        )
        raise TierClassifierModelUnsetError
    provider = resolve_ref_provider(
        app_state,
        ref,
        event=PROVIDER_TIER_LLM_RECOMMENDED,
        subject="tier classifier",
    )
    if provider is None:
        logger.warning(
            PROVIDER_TIER_LLM_RECOMMENDED,
            namespace=_NAMESPACE,
            key=_CLASSIFIER_MODEL_KEY,
            provider=ref.provider,
            reason="classifier_provider_unavailable",
        )
        raise TierClassifierProviderUnavailableError
    cost_tracker = app_state.slice(BudgetStateSlice).cost_tracker
    return LlmTierRecommender(
        provider=provider,
        model_id=ref.model_id,
        cost_tracker=cost_tracker,
    )


__all__ = [
    "SettingsTierOverrideStore",
    "build_tier_assignment_service",
    "build_tier_recommender",
]
