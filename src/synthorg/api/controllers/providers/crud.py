# module-kind: controller
"""Provider CRUD + read endpoints."""

import asyncio

from litestar import Controller, delete, get, post, put
from litestar.datastructures import State
from litestar.status_codes import HTTP_204_NO_CONTENT

from synthorg._core.features import require_service
from synthorg.api.controllers._provider_helpers import (
    apply_usage,
    fetch_provider_usage_24h,
)
from synthorg.api.dto import (
    DEFAULT_LIMIT,
    ApiResponse,
    PaginatedResponse,
)
from synthorg.api.dto_providers import (
    CreateFromPresetRequest,
    CreateProviderRequest,
    ProviderResponse,
    UpdateProviderRequest,
    to_provider_response,
)
from synthorg.api.guards import require_ceo_or_manager, require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import PathName
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.responses import require_resource_or_404
from synthorg.api.state import AppState
from synthorg.core.domain_errors import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_PROVIDER_HEALTH_QUERIED,
    API_RESOURCE_CONFLICT,
    API_RESOURCE_NOT_FOUND,
    API_VALIDATION_FAILED,
)
from synthorg.providers.errors import (
    ProviderAlreadyExistsError,
    ProviderNotFoundError,
    ProviderValidationError,
)
from synthorg.providers.health import ProviderHealthSummary
from synthorg.providers.presets import ProviderPreset, list_presets
from synthorg.providers.state import ProvidersStateSlice, provider_management_of
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)


class ProviderCrudController(Controller):
    """LLM provider CRUD, listing, and health reads."""

    path = "/providers"
    tags = ("providers",)

    @get(
        "/presets",
        guards=[require_read_access],
    )
    async def get_presets(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = DEFAULT_LIMIT,
    ) -> PaginatedResponse[ProviderPreset]:
        """List available provider presets, paginated by name.

        Returns the discriminated-union type alias so Litestar/Pydantic
        emit the ``kind`` discriminator on the wire and frontend
        consumers receive properly tagged objects. The list is bounded by
        cursor pagination so a growing preset catalogue cannot return an
        unbounded page.

        Returns:
            ``PaginatedResponse[ProviderPreset]`` instance.
        """
        app_state: AppState = state.app_state
        ordered = tuple(sorted(list_presets(), key=lambda preset: preset.name))
        page, meta = paginate_cursor(
            ordered,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )
        return PaginatedResponse[ProviderPreset](data=page, pagination=meta)

    @get(
        "/",
        guards=[require_read_access],
    )
    async def list_providers(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = DEFAULT_LIMIT,
    ) -> PaginatedResponse[ProviderResponse]:
        """List all configured providers (secrets stripped), paginated by name.

        Returns:
            ``PaginatedResponse[ProviderResponse]`` instance.
        """
        app_state: AppState = state.app_state
        providers = await config_resolver_of(app_state).get_provider_configs()
        # Paginate the sorted name list FIRST, then build DTOs only
        # for the page slice.  Constructing every ``ProviderResponse``
        # before slicing would defeat the cursor-pagination perf goal:
        # a small-page request still pays O(n) ``model_copy`` /
        # secret-stripping cost on every call.  Same shape as
        # ``list_models`` below.
        ordered_names = tuple(sorted(providers))
        page_names, meta = paginate_cursor(
            ordered_names,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )
        page = tuple(
            to_provider_response(providers[name], name=name) for name in page_names
        )
        return PaginatedResponse[ProviderResponse](data=page, pagination=meta)

    @get(
        "/{name:str}",
        guards=[require_read_access],
    )
    async def get_provider(
        self,
        state: State,
        name: PathName,
    ) -> ApiResponse[ProviderResponse]:
        """Get a provider by name (secrets stripped).

        Raises:
            NotFoundError: If the provider is not found.

        Returns:
            ``ApiResponse[ProviderResponse]`` instance.
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
        # Every provider response advertises its canonical ``name`` so
        # clients can confirm the stored identity on single-resource reads
        # as well as in list pages.
        return ApiResponse(data=to_provider_response(provider, name=name))

    @get(
        "/{name:str}/health",
        guards=[require_read_access],
    )
    async def get_provider_health(
        self,
        state: State,
        name: PathName,
    ) -> ApiResponse[ProviderHealthSummary]:
        """Get provider health summary (enriched with cost data).

        Raises:
            NotFoundError: If the provider is not found.

        Returns:
            ``ApiResponse[ProviderHealthSummary]`` instance.
        """
        app_state: AppState = state.app_state
        providers = await config_resolver_of(app_state).get_provider_configs()
        if name not in providers:
            msg = f"Provider {name!r} not found"
            logger.warning(API_RESOURCE_NOT_FOUND, resource="provider", name=name)
            raise NotFoundError(msg)
        health_tracker = require_service(
            app_state.slice(ProvidersStateSlice).health_tracker,
            "Provider Health Tracker",
        )
        # The summary fetch and the 24h usage fetch both depend only on
        # ``name``, so run them concurrently and merge.
        async with asyncio.TaskGroup() as tg:
            summary_task = tg.create_task(health_tracker.get_summary(name))
            usage_task = tg.create_task(fetch_provider_usage_24h(app_state, name))
        summary = apply_usage(summary_task.result(), usage_task.result())
        logger.debug(
            API_PROVIDER_HEALTH_QUERIED,
            provider=name,
            health_status=summary.health_status.value,
            calls_24h=summary.calls_last_24h,
        )
        return ApiResponse(data=summary)

    @post(
        "/",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("providers.create", key="user"),
        ],
    )
    async def create_provider(
        self,
        state: State,
        data: CreateProviderRequest,
    ) -> ApiResponse[ProviderResponse]:
        """Create a new provider.

        Args:
            state: Application state.
            data: Create provider request.

        Returns:
            Created provider response.

        Raises:
            ConflictError: If a provider with this name already exists.
            ValidationError: If the provider configuration fails
                validation.
        """
        app_state: AppState = state.app_state
        # Strip preset_name from external requests -- only
        # create_from_preset may set it (capability flag injection).
        safe_data = data.model_copy(update={"preset_name": None})
        try:
            config = await provider_management_of(app_state).create_provider(
                safe_data,
            )
        except ProviderAlreadyExistsError as exc:
            logger.warning(
                API_RESOURCE_CONFLICT,
                resource="provider",
                name=data.name,
            )
            raise ConflictError(safe_error_description(exc)) from exc
        except ProviderValidationError as exc:
            logger.warning(
                API_VALIDATION_FAILED,
                resource="provider",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ValidationError(safe_error_description(exc)) from exc
        return ApiResponse(data=to_provider_response(config, name=data.name))

    @post(
        "/from-preset",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy(
                "providers.create_from_preset",
                key="user",
            ),
        ],
    )
    async def create_from_preset(
        self,
        state: State,
        data: CreateFromPresetRequest,
    ) -> ApiResponse[ProviderResponse]:
        """Create a provider from a preset.

        Args:
            state: Application state.
            data: Preset-based creation request.

        Returns:
            Created provider response.

        Raises:
            ConflictError: If a provider with this name already exists.
            ValidationError: If the preset is unknown or config
                validation fails.
        """
        app_state: AppState = state.app_state
        try:
            config = await provider_management_of(app_state).create_from_preset(
                data,
            )
        except ProviderAlreadyExistsError as exc:
            logger.warning(
                API_RESOURCE_CONFLICT,
                resource="provider",
                name=data.name,
            )
            raise ConflictError(safe_error_description(exc)) from exc
        except ProviderValidationError as exc:
            logger.warning(
                API_VALIDATION_FAILED,
                resource="provider",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ValidationError(safe_error_description(exc)) from exc
        return ApiResponse(data=to_provider_response(config, name=data.name))

    @put(
        "/{name:str}",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("providers.update", key="user"),
        ],
    )
    async def update_provider(
        self,
        state: State,
        name: PathName,
        data: UpdateProviderRequest,
    ) -> ApiResponse[ProviderResponse]:
        """Update an existing provider.

        Args:
            state: Application state.
            name: Provider name.
            data: Partial update request.

        Returns:
            Updated provider response.

        Raises:
            NotFoundError: If the provider does not exist.
            ValidationError: If the update fails validation.
        """
        app_state: AppState = state.app_state
        try:
            config = await provider_management_of(app_state).update_provider(
                name,
                data,
            )
        except ProviderNotFoundError as exc:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="provider",
                name=name,
            )
            raise NotFoundError(safe_error_description(exc)) from exc
        except ProviderValidationError as exc:
            logger.warning(
                API_VALIDATION_FAILED,
                resource="provider",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ValidationError(safe_error_description(exc)) from exc
        return ApiResponse(data=to_provider_response(config, name=name))

    @delete(
        "/{name:str}",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("providers.delete", key="user"),
        ],
        status_code=HTTP_204_NO_CONTENT,
    )
    async def delete_provider(
        self,
        state: State,
        name: PathName,
    ) -> None:
        """Delete a provider.

        Args:
            state: Application state.
            name: Provider name.

        Raises:
            NotFoundError: If the provider does not exist.
        """
        app_state: AppState = state.app_state
        try:
            await provider_management_of(app_state).delete_provider(name)
        except ProviderNotFoundError as exc:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="provider",
                name=name,
            )
            raise NotFoundError(safe_error_description(exc)) from exc
