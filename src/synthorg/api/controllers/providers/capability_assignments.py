# module-kind: controller
"""Model capability-assignment endpoints: effective map, overrides, LLM recommend."""

from litestar import Controller, get, post, put
from litestar.datastructures import State

from synthorg.api.dto import ApiResponse
from synthorg.api.dto_capability_assignment import (
    ApplyRecommendationRequest,
    CapabilityAssignmentsResponse,
    CapabilityOverrideRequest,
    CapabilityRecommendationsResponse,
    ClassifierModelDTO,
    to_capability_assignment_dto,
    to_capability_recommendation_dto,
)
from synthorg.api.guards import require_ceo_or_manager, require_read_access
from synthorg.api.path_params import PathId, PathName
from synthorg.api.state import AppState
from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.providers.capability_assignment.models import CapabilityRecommendation
from synthorg.providers.errors import (
    ProviderModelNotFoundError,
    ProviderNotFoundError,
)
from synthorg.settings.model_ref import ModelRef, parse_model_ref, serialize_model_ref
from synthorg.settings.state import config_resolver_of, settings_service_of
from synthorg.workers._capability_assignment_wiring import (
    build_capability_assignment_service,
    build_capability_recommender,
)

_NAMESPACE = "providers"
_CLASSIFIER_MODEL_KEY = "capability_classifier_model"
_CLASSIFIER_ENABLED_KEY = "capability_classifier_enabled"


async def _providers(app_state: AppState) -> dict[str, ProviderConfig]:
    """Return the live provider config map.

    Returns:
        The persisted (or boot-default) provider configurations keyed by name.
    """
    return dict(await config_resolver_of(app_state).get_provider_configs())


def _require_models(
    providers: dict[str, ProviderConfig],
    provider: str,
    model_id: str,
) -> list[ProviderModelConfig]:
    """Return the matching model config(s), raising 404 when absent.

    An override or recommendation for a model that is not configured would
    persist silently and never surface, so an unknown provider or model is
    rejected up front rather than accepted as a no-op.

    Returns:
        The one-element list of the matching model config.

    Raises:
        ProviderNotFoundError: When *provider* is not configured (404).
        ProviderModelNotFoundError: When *model_id* is not a model of
            *provider* (404).
    """
    config = providers.get(provider)
    if config is None:
        msg = f"Provider {provider!r} is not configured"
        raise ProviderNotFoundError(msg)
    matches = [m for m in config.models if m.id == model_id]
    if not matches:
        msg = f"Model {model_id!r} is not configured on provider {provider!r}"
        raise ProviderModelNotFoundError(msg)
    return matches


class ProviderCapabilityAssignmentsController(Controller):
    """Effective capability map, operator overrides, and LLM recommendations."""

    path = "/providers/capability-assignments"
    tags = ("providers",)

    @get("", guards=[require_read_access])
    async def list_assignments(
        self,
        state: State,
    ) -> ApiResponse[CapabilityAssignmentsResponse]:
        """Return the effective capability of every configured model.

        Returns:
            The heuristic classification overlaid by operator / LLM overrides.
        """
        app_state: AppState = state.app_state
        service = await build_capability_assignment_service(app_state)
        assignments = await service.effective_assignments(await _providers(app_state))
        return ApiResponse(
            data=CapabilityAssignmentsResponse(
                assignments=tuple(to_capability_assignment_dto(a) for a in assignments),
            ),
        )

    @put("/{provider:str}/{model_id:str}", guards=[require_ceo_or_manager])
    async def set_override(
        self,
        state: State,
        provider: PathName,
        model_id: PathId,
        data: CapabilityOverrideRequest,
    ) -> ApiResponse[CapabilityAssignmentsResponse]:
        """Set (or clear) an operator capability override for one model.

        Returns:
            The full effective capability map after the change.
        """
        app_state: AppState = state.app_state
        providers = await _providers(app_state)
        service = await build_capability_assignment_service(app_state)
        if data.capability is None:
            # Clearing is idempotent cleanup: allow it even for a model that has
            # since been removed, so a stale override can always be dropped.
            await service.clear_override(provider=provider, model_id=model_id)
        else:
            _require_models(providers, provider, model_id)
            await service.set_override(
                provider=provider,
                model_id=model_id,
                capability=data.capability,
                provenance="operator",
                reason=data.reason,
            )
        assignments = await service.effective_assignments(providers)
        return ApiResponse(
            data=CapabilityAssignmentsResponse(
                assignments=tuple(to_capability_assignment_dto(a) for a in assignments),
            ),
        )

    @post("/{provider:str}/{model_id:str}/recommend", guards=[require_ceo_or_manager])
    async def recommend_model(
        self,
        state: State,
        provider: PathName,
        model_id: PathId,
    ) -> ApiResponse[CapabilityRecommendationsResponse]:
        """Run the LLM recommender for one model.

        Returns:
            The offered capabilities; the offer is not applied.

        Raises:
            ProviderNotFoundError: When *provider* is not configured (404).
            ProviderModelNotFoundError: When *model_id* is not configured (404).
            CapabilityClassifierModelUnsetError: When no classifier model is set (409).
            CapabilityClassifierDisabledError: When the recommender opt-in is off (409).
            CapabilityClassifierProviderUnavailableError: When the classifier
                provider is not registered (409).
        """
        app_state: AppState = state.app_state
        models = _require_models(await _providers(app_state), provider, model_id)
        recommender = await build_capability_recommender(app_state)
        offers = await recommender.recommend(provider, models)
        return ApiResponse(
            data=CapabilityRecommendationsResponse(
                recommendations=tuple(
                    to_capability_recommendation_dto(o) for o in offers
                ),
            ),
        )

    @post("/recommend-all", guards=[require_ceo_or_manager])
    async def recommend_all(
        self,
        state: State,
    ) -> ApiResponse[CapabilityRecommendationsResponse]:
        """Run the LLM recommender fresh over every configured model.

        Returns:
            The offered capabilities across all providers.

        Raises:
            CapabilityClassifierModelUnsetError: When no classifier model is set (409).
            CapabilityClassifierDisabledError: When the recommender opt-in is off (409).
            CapabilityClassifierProviderUnavailableError: When the classifier
                provider is not registered (409).
        """
        app_state: AppState = state.app_state
        recommender = await build_capability_recommender(app_state)
        providers = await _providers(app_state)
        offers: list[CapabilityRecommendation] = []
        for name in sorted(providers):
            offers.extend(await recommender.recommend(name, providers[name].models))
        return ApiResponse(
            data=CapabilityRecommendationsResponse(
                recommendations=tuple(
                    to_capability_recommendation_dto(o) for o in offers
                ),
            ),
        )

    @post("/apply", guards=[require_ceo_or_manager])
    async def apply_recommendation(
        self,
        state: State,
        data: ApplyRecommendationRequest,
    ) -> ApiResponse[CapabilityAssignmentsResponse]:
        """Accept an LLM offer, writing it as an ``llm``-provenance override.

        Returns:
            The full effective capability map after the override.

        Raises:
            ProviderNotFoundError: When the provider is not configured (404).
            ProviderModelNotFoundError: When the model is not configured (404).
        """
        app_state: AppState = state.app_state
        providers = await _providers(app_state)
        _require_models(providers, data.provider, data.model_id)
        service = await build_capability_assignment_service(app_state)
        await service.set_override(
            provider=data.provider,
            model_id=data.model_id,
            capability=data.capability,
            provenance="llm",
            reason=data.rationale,
        )
        assignments = await service.effective_assignments(providers)
        return ApiResponse(
            data=CapabilityAssignmentsResponse(
                assignments=tuple(to_capability_assignment_dto(a) for a in assignments),
            ),
        )

    @get("/classifier-model", guards=[require_read_access])
    async def get_classifier_model(
        self,
        state: State,
    ) -> ApiResponse[ClassifierModelDTO]:
        """Return the provider + model the LLM recommender runs on.

        Returns:
            The configured classifier model (empty fields when unset) and
            whether the recommender opt-in is enabled.
        """
        app_state: AppState = state.app_state
        resolver = config_resolver_of(app_state)
        ref = parse_model_ref(await resolver.get_str(_NAMESPACE, _CLASSIFIER_MODEL_KEY))
        enabled = await resolver.get_bool(_NAMESPACE, _CLASSIFIER_ENABLED_KEY)
        return ApiResponse(
            data=ClassifierModelDTO(
                provider=ref.provider,
                model_id=ref.model_id,
                enabled=enabled,
            ),
        )

    @put("/classifier-model", guards=[require_ceo_or_manager])
    async def set_classifier_model(
        self,
        state: State,
        data: ClassifierModelDTO,
    ) -> ApiResponse[ClassifierModelDTO]:
        """Set the provider + model the LLM recommender runs on and its opt-in.

        Returns:
            The stored classifier model and enabled state.
        """
        app_state: AppState = state.app_state
        ref = ModelRef(provider=data.provider, model_id=data.model_id)
        settings = settings_service_of(app_state)
        await settings.set(_NAMESPACE, _CLASSIFIER_MODEL_KEY, serialize_model_ref(ref))
        await settings.set(
            _NAMESPACE,
            _CLASSIFIER_ENABLED_KEY,
            "true" if data.enabled else "false",
        )
        return ApiResponse(
            data=ClassifierModelDTO(
                provider=ref.provider,
                model_id=ref.model_id,
                enabled=data.enabled,
            ),
        )
