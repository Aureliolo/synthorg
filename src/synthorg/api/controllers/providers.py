"""Provider controller -- CRUD, connection testing, and presets."""

import asyncio
import json as _json
from collections.abc import (
    Mapping,  # noqa: TC003  # Litestar inspects runtime return-type annotation
)
from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from litestar import Controller, delete, get, patch, post, put
from litestar.datastructures import State  # noqa: TC002
from litestar.params import Parameter
from litestar.response import ServerSentEvent
from litestar.status_codes import HTTP_204_NO_CONTENT

from synthorg.api.controllers._provider_helpers import enrich_with_usage, sse_error
from synthorg.api.controllers._workflow_helpers import audit_actor_from_context
from synthorg.api.cursor import InvalidCursorError, decode_keyset_cursor
from synthorg.api.dto import (
    DEFAULT_LIMIT,
    ApiResponse,
    CreateFromPresetRequest,
    CreateProviderRequest,
    DiscoverModelsResponse,
    PaginatedResponse,
    ProbeLocalResponse,
    ProbePresetResponse,
    ProviderResponse,
    TestConnectionResponse,
    UpdateProviderRequest,
    to_provider_response,
)
from synthorg.api.dto import (
    TestConnectionRequest as ConnTestRequest,
)
from synthorg.api.dto_discovery import (
    AddAllowlistEntryRequest,
    DiscoveryPolicyResponse,
    RemoveAllowlistEntryRequest,
)
from synthorg.api.dto_provider_capabilities import (
    AddModelRequest,  # noqa: TC001 -- runtime litestar request body
    CredentialsRotateRequest,  # noqa: TC001 -- runtime litestar request body
    PresetOverride,  # noqa: TC001 -- runtime litestar response model
    PresetOverrideUpdateRequest,  # noqa: TC001 -- runtime litestar request body
    ProviderAuditEvent,  # noqa: TC001 -- runtime litestar response model
    RateLimitsResponse,  # noqa: TC001 -- runtime litestar response model
    RateLimitsUpdateRequest,  # noqa: TC001 -- runtime litestar request body
    SyncModelsRequest,  # noqa: TC001 -- runtime litestar request body
    SyncModelsResponse,  # noqa: TC001 -- runtime litestar response model
)
from synthorg.api.dto_providers import (
    ProviderModelResponse,
    PullModelRequest,
    UpdateModelConfigRequest,
    to_provider_model_response,
)
from synthorg.api.guards import require_ceo_or_manager, require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    encode_keyset_meta,
    paginate_cursor,
)
from synthorg.api.path_params import PathName  # noqa: TC001
from synthorg.api.rate_limits import (
    per_op_concurrency_from_policy,
    per_op_rate_limit_from_policy,
)
from synthorg.api.responses import require_resource_or_404
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.domain_errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    ValidationError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_MODEL_OPERATION_FAILED,
    API_PROVIDER_HEALTH_QUERIED,
    API_PROVIDER_USAGE_ENRICHMENT_FAILED,
    API_RESOURCE_CONFLICT,
    API_RESOURCE_NOT_FOUND,
    API_SSE_PULL_MODEL_FAILED,
    API_VALIDATION_FAILED,
)
from synthorg.observability.events.provider import (
    PROVIDER_PROBE_LOCAL_BATCH_COMPLETED,
    PROVIDER_PROBE_LOCAL_BATCH_STARTED,
    PROVIDER_PROBE_LOCAL_PRESET_FAILED,
)
from synthorg.providers.capabilities import ModelCapabilities  # noqa: TC001
from synthorg.providers.errors import (
    ProviderAlreadyExistsError,
    ProviderModelNotFoundError,
    ProviderNotFoundError,
    ProviderValidationError,
    RateLimitError,
)
from synthorg.providers.health import ProviderHealthSummary  # noqa: TC001
from synthorg.providers.presets import (
    LocalPreset,
    ProviderPreset,
    get_preset,
    list_presets,
    list_probable_presets,
)
from synthorg.providers.probing import probe_preset_urls
from synthorg.providers.resilience.errors import RetryExhaustedError

logger = get_logger(__name__)


class ProviderController(Controller):
    """LLM provider management: CRUD, test, and presets."""

    path = "/providers"
    tags = ("providers",)

    # ── Read endpoints (read access) ─────────────────────────

    @get(
        "/presets",
        guards=[require_read_access],
    )
    async def get_presets(
        self,
        state: State,  # noqa: ARG002
    ) -> ApiResponse[tuple[ProviderPreset, ...]]:
        """List all available provider presets.

        Returns the discriminated-union type alias so Litestar/Pydantic
        emit the ``kind`` discriminator on the wire and frontend
        consumers receive properly tagged objects.
        """
        return ApiResponse(data=list_presets())

    @post(
        "/probe-local",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("providers.probe_local", key="user"),
        ],
    )
    async def probe_local(
        self,
        state: State,  # noqa: ARG002
    ) -> ApiResponse[ProbeLocalResponse]:
        """Probe every local preset's candidate URLs in parallel.

        Returns a batch envelope with one entry per probed preset under
        ``results`` (success) or ``errors`` (probe raised).  Cloud
        presets and local presets without candidate URLs (vLLM) are
        excluded from the probe surface and absent from both maps.

        Per-preset failures do not abort the batch: each probe runs in
        an ``asyncio.TaskGroup`` body that catches ``Exception`` and
        records the error message, so one slow / unreachable preset
        cannot starve another.
        """
        probable = list_probable_presets()
        results: dict[str, ProbePresetResponse] = {}
        errors: dict[str, str] = {}

        logger.info(
            PROVIDER_PROBE_LOCAL_BATCH_STARTED,
            preset_count=len(probable),
        )

        async def _probe_one(preset: LocalPreset) -> None:
            """Run one preset probe, recording success or failure in-place."""
            try:
                result = await probe_preset_urls(preset.name)
                results[preset.name] = ProbePresetResponse(
                    url=result.url,
                    model_count=result.model_count,
                    candidates_tried=result.candidates_tried,
                )
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                errors[preset.name] = safe_error_description(exc)
                logger.warning(
                    PROVIDER_PROBE_LOCAL_PRESET_FAILED,
                    preset=preset.name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

        async with asyncio.TaskGroup() as tg:
            for preset in probable:
                tg.create_task(_probe_one(preset))

        logger.info(
            PROVIDER_PROBE_LOCAL_BATCH_COMPLETED,
            preset_count=len(probable),
            success_count=len(results),
            failure_count=len(errors),
        )

        return ApiResponse(
            data=ProbeLocalResponse(results=results, errors=errors),
        )

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
        """List all configured providers (secrets stripped), paginated by name."""
        app_state: AppState = state.app_state
        providers = await app_state.config_resolver.get_provider_configs()
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
            secret=app_state.cursor_secret,
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
        """
        app_state: AppState = state.app_state
        providers = await app_state.config_resolver.get_provider_configs()
        provider = require_resource_or_404(
            providers.get(name),
            resource_type="Provider",
            identifier=name,
            log_event=API_RESOURCE_NOT_FOUND,
            operation="read",
            extra_log_kwargs={"name": name},
        )
        # ``name=None`` keeps the list-only ``ProviderResponse.name``
        # field out of the wire envelope on single-resource responses
        # (the URL already identifies the provider).  Setting ``name``
        # here would silently expand the non-paginated schema to carry
        # the field, breaking the dict-by-name contract on the
        # frontend that distinguishes list vs single-resource shape.
        return ApiResponse(data=to_provider_response(provider, name=None))

    @get(
        "/{name:str}/models",
        guards=[require_read_access],
    )
    async def list_models(
        self,
        state: State,
        name: PathName,
        cursor: CursorParam = None,
        limit: CursorLimit = DEFAULT_LIMIT,
    ) -> PaginatedResponse[ProviderModelResponse]:
        """List models for a provider with runtime capabilities, paginated by id.

        Raises:
            NotFoundError: If the provider is not found.
        """
        app_state: AppState = state.app_state
        providers = await app_state.config_resolver.get_provider_configs()
        provider = require_resource_or_404(
            providers.get(name),
            resource_type="Provider",
            identifier=name,
            log_event=API_RESOURCE_NOT_FOUND,
            operation="read",
            extra_log_kwargs={"name": name},
        )

        driver = None
        if app_state.has_provider_registry and name in app_state.provider_registry:
            driver = app_state.provider_registry.get(name)

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
            secret=app_state.cursor_secret,
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
        """
        app_state: AppState = state.app_state
        providers = await app_state.config_resolver.get_provider_configs()
        if name not in providers:
            msg = f"Provider {name!r} not found"
            logger.warning(API_RESOURCE_NOT_FOUND, resource="provider", name=name)
            raise NotFoundError(msg)
        summary = await app_state.provider_health_tracker.get_summary(name)
        summary = await enrich_with_usage(summary, app_state, name)
        logger.debug(
            API_PROVIDER_HEALTH_QUERIED,
            provider=name,
            health_status=summary.health_status.value,
            calls_24h=summary.calls_last_24h,
        )
        return ApiResponse(data=summary)

    # ── Write endpoints (write access) ───────────────────────

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
        actor = audit_actor_from_context()
        try:
            config = await app_state.provider_management.create_provider(
                safe_data,
                actor=actor,
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
        return ApiResponse(data=to_provider_response(config, name=None))

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
        actor = audit_actor_from_context()
        try:
            config = await app_state.provider_management.create_from_preset(
                data,
                actor=actor,
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
        return ApiResponse(data=to_provider_response(config, name=None))

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
        actor = audit_actor_from_context()
        try:
            config = await app_state.provider_management.update_provider(
                name,
                data,
                actor=actor,
            )
        except ProviderNotFoundError as exc:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="provider",
                name=name,
            )
            raise NotFoundError(str(exc)) from exc
        except ProviderValidationError as exc:
            logger.warning(
                API_VALIDATION_FAILED,
                resource="provider",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ValidationError(safe_error_description(exc)) from exc
        return ApiResponse(data=to_provider_response(config, name=None))

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
        actor = audit_actor_from_context()
        try:
            await app_state.provider_management.delete_provider(name, actor=actor)
        except ProviderNotFoundError as exc:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="provider",
                name=name,
            )
            raise NotFoundError(str(exc)) from exc

    @post(
        "/{name:str}/discover-models",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy(
                "providers.discover_models",
                key="user",
            ),
        ],
        opt=per_op_concurrency_from_policy(
            "providers.discover_models",
            key="user",
        ),
    )
    async def discover_models(
        self,
        state: State,
        name: PathName,
        preset_hint: Annotated[
            str,
            Parameter(
                max_length=64,
                description="Canonical preset hint (e.g. anthropic, ollama).",
            ),
        ]
        | None = None,
    ) -> ApiResponse[DiscoverModelsResponse]:
        """Discover available models from a provider endpoint.

        Queries the provider's API for available models and updates
        the provider configuration with any discovered models.  When
        ``base_url`` is not configured, returns an empty result.

        Args:
            state: Application state.
            name: Provider name.
            preset_hint: Optional preset name to guide endpoint
                selection (e.g. ``"ollama"``).

        Returns:
            Discovery result with found models.

        Raises:
            NotFoundError: If the provider does not exist.
        """
        app_state: AppState = state.app_state
        mgmt = app_state.provider_management
        try:
            discovered = await mgmt.discover_models_for_provider(
                name,
                preset_hint=preset_hint,
            )
        except ProviderNotFoundError as exc:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="provider",
                name=name,
            )
            raise NotFoundError(str(exc)) from exc
        return ApiResponse(
            data=DiscoverModelsResponse(
                discovered_models=discovered,
                provider_name=name,
            ),
        )

    @post(
        "/{name:str}/test",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("providers.test", key="user"),
        ],
    )
    async def test_connection(
        self,
        state: State,
        name: PathName,
        data: ConnTestRequest,
    ) -> ApiResponse[TestConnectionResponse]:
        """Test connectivity to a provider.

        Args:
            state: Application state.
            name: Provider name.
            data: Test connection request (includes optional model selection).

        Returns:
            Connection test result.

        Raises:
            NotFoundError: If the provider does not exist.
        """
        app_state: AppState = state.app_state
        try:
            result = await app_state.provider_management.test_connection(
                name,
                data,
            )
        except ProviderNotFoundError as exc:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="provider",
                name=name,
            )
            raise NotFoundError(str(exc)) from exc
        return ApiResponse(data=result)

    # ── Discovery allowlist (read + write access) ──────────────

    @get(
        "/discovery-policy",
        guards=[require_read_access],
    )
    async def get_discovery_policy(
        self,
        state: State,
    ) -> ApiResponse[DiscoveryPolicyResponse]:
        """Return the current provider discovery SSRF allowlist.

        Args:
            state: Application state.

        Returns:
            Current discovery policy envelope.
        """
        app_state: AppState = state.app_state
        policy = await app_state.provider_management.get_discovery_policy()
        return ApiResponse(
            data=DiscoveryPolicyResponse.model_validate(
                policy,
                from_attributes=True,
            ),
        )

    @post(
        "/discovery-policy/entries",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy(
                "providers.allowlist_add",
                key="user",
            ),
        ],
    )
    async def add_allowlist_entry(
        self,
        state: State,
        data: AddAllowlistEntryRequest,
    ) -> ApiResponse[DiscoveryPolicyResponse]:
        """Add a custom host:port entry to the discovery allowlist.

        Args:
            state: Application state.
            data: Request with the host:port entry to add.

        Returns:
            Updated discovery policy envelope.
        """
        app_state: AppState = state.app_state
        policy = await app_state.provider_management.add_custom_allowlist_entry(
            data.host_port,
        )
        return ApiResponse(
            data=DiscoveryPolicyResponse.model_validate(
                policy,
                from_attributes=True,
            ),
        )

    @post(
        "/discovery-policy/remove-entry",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy(
                "providers.allowlist_remove",
                key="user",
            ),
        ],
    )
    async def remove_allowlist_entry(
        self,
        state: State,
        data: RemoveAllowlistEntryRequest,
    ) -> ApiResponse[DiscoveryPolicyResponse]:
        """Remove a host:port entry from the discovery allowlist.

        Args:
            state: Application state.
            data: Request with the host:port entry to remove.

        Returns:
            Updated discovery policy envelope.
        """
        app_state: AppState = state.app_state
        policy = await app_state.provider_management.remove_custom_allowlist_entry(
            data.host_port,
        )
        return ApiResponse(
            data=DiscoveryPolicyResponse.model_validate(
                policy,
                from_attributes=True,
            ),
        )

    # ── Local model management ───────────────────────────────

    @post(
        "/{name:str}/models/pull",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("providers.pull_model", key="user"),
        ],
        opt=per_op_concurrency_from_policy(
            "providers.pull_model",
            key="user",
        ),
        media_type="text/event-stream",
    )
    async def pull_model(
        self,
        state: State,
        name: PathName,
        data: PullModelRequest,
    ) -> ServerSentEvent:
        """Pull a model on a local provider (SSE streaming).

        Args:
            state: Application state.
            name: Provider name.
            data: Pull request with model name.

        Returns:
            SSE stream of pull progress events.
        """
        app_state: AppState = state.app_state
        svc = app_state.provider_management

        async def _event_stream() -> AsyncIterator[dict[str, str]]:
            # Carve-out: SSE responses cannot raise domain exceptions
            # to the central RFC 9457 handler because the response
            # headers (``Content-Type: text/event-stream``, etc.) are
            # already on the wire by the time the first event yields.
            # Errors emitted after stream start MUST use this in-stream
            # ``event: error`` schema; ``sse_error()`` produces the
            # documented payload shape so clients can discriminate
            # in-stream errors from connection failures.  The same
            # applies to ``except ProviderValidationError`` below.
            try:
                async for event in svc.pull_model(name, data.model_name):
                    if event.done and event.error:
                        event_type = "error"
                    elif event.done:
                        event_type = "complete"
                    else:
                        event_type = "progress"
                    yield {
                        "event": event_type,
                        "data": _json.dumps(event.model_dump()),
                    }
            except ProviderNotFoundError:
                yield {
                    "event": "error",
                    "data": _json.dumps(
                        sse_error(f"Provider {name!r} not found"),
                    ),
                }
            except ProviderValidationError as exc:
                yield {
                    "event": "error",
                    "data": _json.dumps(sse_error(safe_error_description(exc))),
                }
            except asyncio.CancelledError:
                raise
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    API_SSE_PULL_MODEL_FAILED,
                    provider=name,
                    model=data.model_name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                yield {
                    "event": "error",
                    "data": _json.dumps(
                        sse_error(
                            f"Internal error: {type(exc).__name__}",
                        ),
                    ),
                }

        return ServerSentEvent(content=_event_stream())

    @delete(
        "/{name:str}/models/{model_id:path}",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("providers.delete_model", key="user"),
        ],
        status_code=HTTP_204_NO_CONTENT,
    )
    async def delete_model(
        self,
        state: State,
        name: PathName,
        model_id: Annotated[
            str,
            Parameter(
                max_length=256,
                min_length=1,
                description="Local provider model id (may contain colons).",
            ),
        ],
    ) -> None:
        """Delete a model from a local provider.

        Args:
            state: Application state.
            name: Provider name.
            model_id: Model identifier (may contain colons).
        """
        app_state: AppState = state.app_state
        actor = audit_actor_from_context()
        try:
            await app_state.provider_management.delete_model(
                name,
                model_id,
                actor=actor,
            )
        except ProviderNotFoundError as exc:
            msg = f"Provider {name!r} not found"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="provider",
                name=name,
            )
            raise NotFoundError(msg) from exc
        except ProviderValidationError as exc:
            logger.warning(
                API_VALIDATION_FAILED,
                resource="provider",
                name=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ValidationError(safe_error_description(exc)) from exc
        except ValueError as exc:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="model",
                name=model_id,
                provider=name,
            )
            raise NotFoundError(str(exc)) from exc
        except RuntimeError as exc:
            logger.warning(
                API_MODEL_OPERATION_FAILED,
                resource="model",
                operation="delete",
                name=model_id,
                provider=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise DomainError(safe_error_description(exc)) from exc

    @put(
        "/{name:str}/models/{model_id:path}/config",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy(
                "providers.update_model_config",
                key="user",
            ),
        ],
    )
    async def update_model_config(
        self,
        state: State,
        name: PathName,
        model_id: Annotated[
            str,
            Parameter(
                max_length=256,
                min_length=1,
                description="Local provider model whose launch parameters to update.",
            ),
        ],
        data: UpdateModelConfigRequest,
    ) -> ApiResponse[ProviderModelResponse]:
        """Update per-model launch parameters for a local provider.

        Args:
            state: Application state.
            name: Provider name.
            model_id: Model identifier.
            data: New launch parameters.

        Returns:
            Updated model response.

        Raises:
            NotFoundError: If the provider or model does not exist.
            ValidationError: If the launch parameters are unsupported.
        """
        app_state: AppState = state.app_state
        actor = audit_actor_from_context()
        try:
            updated = await app_state.provider_management.update_model_config(
                name,
                model_id,
                data.local_params,
                actor=actor,
            )
        except ProviderNotFoundError as exc:
            msg = f"Provider {name!r} not found"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="provider",
                name=name,
            )
            raise NotFoundError(msg) from exc
        except ProviderModelNotFoundError as exc:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="model",
                name=model_id,
                provider=name,
            )
            raise NotFoundError(safe_error_description(exc)) from exc
        except ProviderValidationError as exc:
            logger.warning(
                API_VALIDATION_FAILED,
                resource="provider",
                name=name,
                model=model_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ValidationError(safe_error_description(exc)) from exc
        model = next(
            (m for m in updated.models if m.id == model_id),
            None,
        )
        if model is None:
            msg = f"Model {model_id!r} missing from updated config"
            logger.error(
                API_MODEL_OPERATION_FAILED,
                resource="model",
                operation="config_update",
                name=model_id,
                provider=name,
                error=msg,
            )
            raise DomainError(msg)
        return ApiResponse(data=to_provider_model_response(model))

    # ── Manual model add + bulk sync ────────────────────────────

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
            NotFoundError: If the provider does not exist.
            ConflictError: If a model with the same id is already
                persisted on the provider.
        """
        app_state: AppState = state.app_state
        actor = audit_actor_from_context()
        try:
            updated = await app_state.provider_management.add_model(
                name,
                data,
                actor=actor,
            )
        except ProviderNotFoundError as exc:
            msg = f"Provider {name!r} not found"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="provider",
                name=name,
            )
            raise NotFoundError(msg) from exc
        except ProviderAlreadyExistsError as exc:
            logger.warning(
                API_RESOURCE_CONFLICT,
                resource="model",
                name=data.model.id,
                provider=name,
            )
            raise ConflictError(safe_error_description(exc)) from exc
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
            NotFoundError: If the provider does not exist.
        """
        app_state: AppState = state.app_state
        actor = audit_actor_from_context()
        try:
            result = await app_state.provider_management.sync_models(
                name,
                data,
                actor=actor,
            )
        except ProviderNotFoundError as exc:
            msg = f"Provider {name!r} not found"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="provider",
                name=name,
            )
            raise NotFoundError(msg) from exc
        except ProviderValidationError as exc:
            # Validation errors (e.g. provider configuration changed
            # mid-discovery) are operator-actionable; surface as 422
            # rather than letting them escape to a generic 500.
            logger.warning(
                API_VALIDATION_FAILED,
                resource="provider",
                name=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ValidationError(safe_error_description(exc)) from exc
        return ApiResponse(data=result)

    # ── Credentials rotation ────────────────────────────────────

    @post(
        "/{name:str}/credentials/rotate",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy(
                "providers.rotate_credentials",
                key="user",
            ),
        ],
    )
    async def rotate_credentials(
        self,
        state: State,
        name: PathName,
        data: CredentialsRotateRequest,
    ) -> ApiResponse[ProviderResponse]:
        """Rotate the secret credentials on an existing provider.

        Args:
            state: Application state.
            name: Provider name.
            data: Discriminated-union rotation payload keyed by
                ``auth_type``.

        Returns:
            Updated provider response (secrets stripped).

        Raises:
            NotFoundError: If the provider does not exist.
            ValidationError: If the rotation payload's ``auth_type``
                does not match the provider's persisted ``auth_type``.
        """
        app_state: AppState = state.app_state
        actor = audit_actor_from_context()
        try:
            updated = await app_state.provider_management.rotate_credentials(
                name,
                data,
                actor=actor,
            )
        except ProviderNotFoundError as exc:
            msg = f"Provider {name!r} not found"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="provider",
                name=name,
            )
            raise NotFoundError(msg) from exc
        except ProviderValidationError as exc:
            logger.warning(
                API_VALIDATION_FAILED,
                resource="provider",
                name=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ValidationError(safe_error_description(exc)) from exc
        return ApiResponse(data=to_provider_response(updated, name=None))

    # ── Preset overrides ────────────────────────────────────────

    @get(
        "/presets/{preset_name:str}/override",
        guards=[require_read_access],
    )
    async def get_preset_override(
        self,
        state: State,
        preset_name: PathName,
    ) -> ApiResponse[PresetOverride | None]:
        """Read the operator override for ``preset_name`` (or null).

        Args:
            state: Application state.
            preset_name: Preset whose override to read.  Must match an
                in-code preset; unknown names return 404.

        Returns:
            ``PresetOverride`` if one is persisted, otherwise ``None``.

        Raises:
            NotFoundError: If the preset name is unknown.
        """
        if get_preset(preset_name) is None:
            msg = f"Unknown preset {preset_name!r}"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="preset",
                name=preset_name,
            )
            raise NotFoundError(msg)

        app_state: AppState = state.app_state
        override = await app_state.preset_override_service.get_override(preset_name)
        return ApiResponse(data=override)

    @patch(
        "/presets/{preset_name:str}/override",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy(
                "providers.update_preset_override",
                key="user",
            ),
        ],
    )
    async def update_preset_override(
        self,
        state: State,
        preset_name: PathName,
        data: PresetOverrideUpdateRequest,
    ) -> ApiResponse[PresetOverride]:
        """Apply a partial override on top of ``preset_name``.

        Args:
            state: Application state.
            preset_name: Preset whose override to write.
            data: Partial override payload.

        Returns:
            The persisted override.

        Raises:
            NotFoundError: If the preset name is unknown.
            ValidationError: If the override shape conflicts with
                the preset's kind (cloud vs local).
        """
        app_state: AppState = state.app_state
        actor = audit_actor_from_context()
        # Preflight existence check: classifying "preset not found"
        # vs "override invalid for this preset's kind" via substring
        # match on the error message is brittle and silently misroutes
        # any unrelated validation error that happens to contain the
        # phrase "Unknown preset".  Fail-fast with 404 here when the
        # name is unknown; everything that survives this check is a
        # genuine validation failure on the override shape.
        if get_preset(preset_name) is None:
            msg = f"Unknown preset {preset_name!r}"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="preset",
                name=preset_name,
            )
            raise NotFoundError(msg)
        try:
            saved = await app_state.preset_override_service.upsert_override(
                preset_name,
                data,
                actor=actor,
            )
        except ProviderValidationError as exc:
            logger.warning(
                API_VALIDATION_FAILED,
                resource="preset",
                name=preset_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ValidationError(safe_error_description(exc)) from exc
        return ApiResponse(data=saved)

    @delete(
        "/presets/{preset_name:str}/override",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy(
                "providers.delete_preset_override",
                key="user",
            ),
        ],
        status_code=HTTP_204_NO_CONTENT,
    )
    async def delete_preset_override(
        self,
        state: State,
        preset_name: PathName,
    ) -> None:
        """Drop the override for ``preset_name``.

        Idempotent: returns 204 whether or not a row existed for a
        VALID preset name; an unknown preset name returns 404 to
        match the upsert path.

        Args:
            state: Application state.
            preset_name: Preset whose override to delete.
        """
        app_state: AppState = state.app_state
        actor = audit_actor_from_context()
        # Match the upsert path's preflight: an unknown preset name
        # is a 404, not a silent no-op.  Without this, callers can
        # DELETE arbitrary strings with no signal -- defeats the
        # accountability intent of the audit row.
        if get_preset(preset_name) is None:
            msg = f"Unknown preset {preset_name!r}"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="preset",
                name=preset_name,
            )
            raise NotFoundError(msg)
        await app_state.preset_override_service.delete_override(
            preset_name,
            actor=actor,
        )

    # ── Rate-limit overrides ────────────────────────────────────

    @get(
        "/{name:str}/rate-limits",
        guards=[require_read_access],
    )
    async def get_rate_limits(
        self,
        state: State,
        name: PathName,
    ) -> ApiResponse[RateLimitsResponse]:
        """Read the persisted rate-limit configuration for one provider.

        Args:
            state: Application state.
            name: Provider name.

        Returns:
            ``RateLimitsResponse`` with ``0`` meaning unlimited.

        Raises:
            NotFoundError: If the provider does not exist.
        """
        app_state: AppState = state.app_state
        try:
            data = await app_state.provider_management.get_rate_limits(name)
        except ProviderNotFoundError as exc:
            msg = f"Provider {name!r} not found"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="provider",
                name=name,
            )
            raise NotFoundError(msg) from exc
        return ApiResponse(data=data)

    @patch(
        "/{name:str}/rate-limits",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy(
                "providers.update_rate_limits",
                key="user",
            ),
        ],
    )
    async def update_rate_limits(
        self,
        state: State,
        name: PathName,
        data: RateLimitsUpdateRequest,
    ) -> ApiResponse[RateLimitsResponse]:
        """Apply a partial update to the provider's rate-limit config.

        Args:
            state: Application state.
            name: Provider name.
            data: Partial-update payload; at least one field required.

        Returns:
            ``RateLimitsResponse`` reflecting the new effective config.

        Raises:
            NotFoundError: If the provider does not exist.
            ValidationError: If the merged config fails validation.
        """
        app_state: AppState = state.app_state
        actor = audit_actor_from_context()
        try:
            updated = await app_state.provider_management.update_rate_limits(
                name,
                data,
                actor=actor,
            )
        except ProviderNotFoundError as exc:
            msg = f"Provider {name!r} not found"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="provider",
                name=name,
            )
            raise NotFoundError(msg) from exc
        except ProviderValidationError as exc:
            logger.warning(
                API_VALIDATION_FAILED,
                resource="provider",
                name=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ValidationError(safe_error_description(exc)) from exc
        return ApiResponse(data=updated)

    # ── Audit log (read access) ─────────────────────────────────

    @get(
        "/{name:str}/audit",
        guards=[require_read_access],
    )
    async def list_audit(
        self,
        state: State,
        name: PathName,
        cursor: CursorParam = None,
        limit: CursorLimit = DEFAULT_LIMIT,
    ) -> PaginatedResponse[ProviderAuditEvent]:
        """List the mutation audit log for one provider, newest first.

        Keyset-paginated on the integer ``id`` column.  ``cursor`` is
        an opaque keyset cursor returned by the previous page; pass
        ``None`` (omit the param) for the first page.

        The endpoint accepts any provider name and returns whatever
        rows exist for it -- including for providers that have since
        been deleted.  The most important audit row a user ever
        queries is ``provider_deleted``; gating the endpoint on
        live-provider existence would make that row undiscoverable.
        A name with no rows simply yields an empty page.

        Args:
            state: Application state.
            name: Provider name (any value accepted; missing
                providers yield an empty page rather than 404).
            cursor: Opaque keyset cursor from a previous page.
            limit: Page size (default ``DEFAULT_LIMIT``, max ``MAX_LIMIT``).

        Returns:
            Paginated response of ``ProviderAuditEvent`` rows.

        Raises:
            InvalidCursorError: HTTP 400 -- malformed, tampered, or
                signed by a different secret.
        """
        app_state: AppState = state.app_state
        # Audit history is queryable by name forever -- including
        # for providers that have been deleted, since the most
        # important row a user ever queries is the
        # ``provider_deleted`` event itself.  A name with no rows
        # simply yields an empty page.

        after_id_str = (
            decode_keyset_cursor(cursor, secret=app_state.cursor_secret)
            if cursor is not None
            else None
        )
        # The keyset cursor encodes the last id as a string for
        # cross-domain consistency; the provider audit log carries
        # integer ids, so coerce here.  A validly-signed but malformed
        # cursor (e.g. tampered payload that survived signature check
        # but no longer parses as int) maps to a 400.
        after_id: int | None = None
        if after_id_str is not None:
            try:
                after_id = int(after_id_str)
            except ValueError as exc:
                msg = "cursor payload is not an integer"
                raise InvalidCursorError(msg) from exc

        events, has_more = await app_state.provider_audit_service.list_for_provider(
            provider_name=name,
            after_id=after_id,
            limit=limit,
        )
        next_after_key = (
            str(events[-1].id)
            if has_more and events and events[-1].id is not None
            else None
        )
        meta = encode_keyset_meta(
            next_after_key=next_after_key,
            has_more=has_more,
            limit=limit,
            secret=app_state.cursor_secret,
        )
        return PaginatedResponse(data=events, pagination=meta)
