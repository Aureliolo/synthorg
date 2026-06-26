# module-kind: controller
"""Provider model catalogue: list, manual add, and bulk sync."""

from collections.abc import (
    Mapping,  # Litestar inspects runtime return-type annotation
)

from litestar import Controller, get, post
from litestar.datastructures import State

from synthorg.api.dto import (
    DEFAULT_LIMIT,
    ApiResponse,
    PaginatedResponse,
)
from synthorg.api.dto_provider_capabilities import (
    AddModelRequest,
    SyncModelsRequest,
    SyncModelsResponse,
)
from synthorg.api.dto_providers import (
    ProviderModelResponse,
    ProviderResponse,
    to_provider_model_response,
    to_provider_response,
)
from synthorg.api.guards import require_ceo_or_manager, require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import PathId, PathName
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.responses import require_resource_or_404
from synthorg.api.state import AppState
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_PROVIDER_USAGE_ENRICHMENT_FAILED,
    API_RESOURCE_NOT_FOUND,
)
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.errors import RateLimitError
from synthorg.providers.resilience.errors import RetryExhaustedError
from synthorg.providers.state import ProvidersStateSlice, provider_management_of
from synthorg.providers.tool_call_feedback.state import (
    tool_call_feedback_tracker_of,
)
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)


class ProviderModelsController(Controller):
    """Provider model catalogue reads + manual add + bulk sync."""

    path = "/providers"
    tags = ("providers",)

    @get(
        "/{name:str}/models",
        guards=[
            require_read_access,
            per_op_rate_limit_from_policy("providers.list_models", key="user"),
        ],
    )
    async def list_models(
        self,
        state: State,
        name: PathName,
        cursor: CursorParam = None,
        limit: CursorLimit = DEFAULT_LIMIT,
    ) -> PaginatedResponse[ProviderModelResponse]:
        """List models for a provider with runtime capabilities, paginated by id.

        Args:
            state: Application state.
            name: Provider name.
            cursor: Opaque cursor for the current page position.
            limit: Maximum number of models to return.

        Raises:
            NotFoundError: If the provider is not found.

        Returns:
            ``PaginatedResponse[ProviderModelResponse]`` instance.
        """
        app_state: AppState = state.app_state
        providers = await config_resolver_of(app_state).get_provider_configs()
        provider = require_resource_or_404(
            providers.get(name),
            resource_type="Provider",
            identifier=name,
            log_event=API_RESOURCE_NOT_FOUND,
            operation="read",
            extra_log_kwargs={"name": name},
        )

        driver = None
        registry = app_state.slice(ProvidersStateSlice).registry
        if registry is not None and name in registry:
            driver = registry.get(name)

        # Paginate the model list FIRST, then enrich only the page.
        # Running ``batch_get_capabilities`` over ``provider.models``
        # before slicing would defeat the cursor-pagination perf goal:
        # a small-page client would still pay the full upstream
        # capability-probe cost on every request.  ``paginate_cursor``
        # consumes the sorted ``ModelConfig`` objects directly; the
        # response shape is built per-page below.
        ordered_models = tuple(sorted(provider.models, key=lambda m: m.id))
        page_models, meta = paginate_cursor(
            ordered_models,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )

        caps_by_id: Mapping[str, ModelCapabilities | None] = {}
        if driver is not None and page_models:
            try:
                caps_by_id = await driver.batch_get_capabilities(
                    tuple(m.id for m in page_models),
                )
            except* (RetryExhaustedError, RateLimitError) as exc_group:
                # ``BaseCompletionProvider.batch_get_capabilities``
                # fans out via ``asyncio.TaskGroup``, which wraps any
                # raised exception in an ``ExceptionGroup``.  ``except*``
                # unpacks that wrapper so we still catch retry
                # exhaustion regardless of how many sub-exceptions the
                # group carries.  Retry exhaustion AND rate-limit
                # exhaustion both signal provider-level unhealthiness
                # rather than a per-model classification issue, so
                # they should fall through to the static-model
                # fallback ("no capability data") rather than a 500.
                exc = exc_group.exceptions[0]
                logger.warning(
                    API_PROVIDER_USAGE_ENRICHMENT_FAILED,
                    provider=name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        page = tuple(
            to_provider_model_response(
                model_config,
                caps_by_id.get(model_config.id),
            )
            for model_config in page_models
        )
        return PaginatedResponse[ProviderModelResponse](data=page, pagination=meta)

    @post(
        "/{name:str}/models",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("providers.add_model", key="user"),
        ],
    )
    async def add_model(
        self,
        state: State,
        name: PathName,
        data: AddModelRequest,
    ) -> ApiResponse[ProviderResponse]:
        """Add a single ``ProviderModelConfig`` to the persisted list.

        Args:
            state: Application state.
            name: Provider name.
            data: Payload carrying the new model spec.

        Returns:
            Updated provider response (secrets stripped).

        Raises:
            ProviderNotFoundError: If the provider does not exist (404,
                mapped by the domain handler from class metadata).
            ProviderAlreadyExistsError: If a model with the same id is
                already persisted on the provider (409).
        """
        app_state: AppState = state.app_state
        updated = await provider_management_of(app_state).add_model(name, data)
        return ApiResponse(data=to_provider_response(updated, name=None))

    @post(
        "/{name:str}/models/sync",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("providers.sync_models", key="user"),
        ],
    )
    async def sync_models(
        self,
        state: State,
        name: PathName,
        data: SyncModelsRequest,
    ) -> ApiResponse[SyncModelsResponse]:
        """Re-run discovery + pricing enrichment for the provider.

        Args:
            state: Application state.
            name: Provider name.
            data: Sync request (``replace_existing`` flag, optional
                ``preset_hint``).

        Returns:
            ``SyncModelsResponse`` with the diff and the new model
            list.

        Raises:
            ProviderNotFoundError: If the provider does not exist (404,
                mapped by the domain handler from class metadata).
            ProviderValidationError: If the provider configuration changed
                mid-discovery (422, mapped by the domain handler).
        """
        app_state: AppState = state.app_state
        result = await provider_management_of(app_state).sync_models(name, data)
        return ApiResponse(data=result)

    @post(
        "/{name:str}/models/{model_id:str}/reenable-tool-calling",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy(
                "providers.reenable_tool_calling", key="user"
            ),
        ],
    )
    async def reenable_tool_calling(
        self,
        state: State,
        name: PathName,
        model_id: PathId,
    ) -> ApiResponse[ProviderResponse]:
        """Clear a model's runtime tool-call downgrade and reset to untested.

        The manual operator escape hatch for a model the runtime feedback
        loop downgraded (``tool_calls_verified=False``): drops the decay
        accumulator and resets the flag to ``None`` so the matcher's
        optimistic path resumes. Naturally idempotent -- re-enabling an
        already-enabled model is a no-op.

        Args:
            state: Application state.
            name: Provider name.
            model_id: Model id within the provider.

        Returns:
            Updated provider response (secrets stripped).

        Raises:
            ProviderNotFoundError: If the provider does not exist (404).
            ProviderModelNotFoundError: If the model does not exist on the
                provider (404, ``MODEL_NOT_FOUND``; mapped by the domain
                handler from class metadata).
        """
        app_state: AppState = state.app_state
        tracker = tool_call_feedback_tracker_of(app_state)
        await tracker.clear(provider=name, model=model_id)
        providers = await config_resolver_of(app_state).get_provider_configs()
        updated = require_resource_or_404(
            providers.get(name),
            resource_type="Provider",
            identifier=name,
            log_event=API_RESOURCE_NOT_FOUND,
            operation="read",
            extra_log_kwargs={"name": name},
        )
        return ApiResponse(data=to_provider_response(updated, name=None))
