# module-kind: controller
"""Provider connection testing, model discovery, and local-preset probing."""

import asyncio
from typing import Annotated

from litestar import Controller, post
from litestar.datastructures import State
from litestar.params import QueryParameter

from synthorg.api.dto import ApiResponse
from synthorg.api.dto_providers import (
    DiscoverModelsResponse,
    ProbeLocalResponse,
    ProbePresetResponse,
    TestConnectionResponse,
)
from synthorg.api.dto_providers import (
    TestConnectionRequest as ConnTestRequest,
)
from synthorg.api.guards import require_ceo_or_manager
from synthorg.api.path_params import PathName
from synthorg.api.rate_limits import (
    per_op_concurrency_from_policy,
    per_op_rate_limit_from_policy,
)
from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import NotFoundError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_RESOURCE_NOT_FOUND
from synthorg.observability.events.provider import (
    PROVIDER_PROBE_LOCAL_BATCH_COMPLETED,
    PROVIDER_PROBE_LOCAL_BATCH_STARTED,
    PROVIDER_PROBE_LOCAL_PRESET_FAILED,
)
from synthorg.providers.errors import ProviderNotFoundError
from synthorg.providers.presets import LocalPreset, list_probable_presets
from synthorg.providers.probing import probe_preset_urls
from synthorg.providers.state import provider_management_of

logger = get_logger(__name__)


class ProviderConnectionController(Controller):
    """Provider connectivity tests, model discovery, and local probing."""

    path = "/providers"
    tags = ("providers",)

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

        Returns:
            ``ApiResponse[ProbeLocalResponse]`` instance.
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
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                errors[preset.name] = safe_error_description(exc)
                logger.warning(
                    PROVIDER_PROBE_LOCAL_PRESET_FAILED,
                    preset=preset.name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

        async with asyncio.TaskGroup() as tg:
            for preset in probable:
                _ = tg.create_task(_probe_one(preset))

        logger.info(
            PROVIDER_PROBE_LOCAL_BATCH_COMPLETED,
            preset_count=len(probable),
            success_count=len(results),
            failure_count=len(errors),
        )

        return ApiResponse(
            data=ProbeLocalResponse(results=results, errors=errors),
        )

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
            str | None,
            QueryParameter(
                max_length=64,
                description=(
                    'Canonical preset hint (e.g. "example-provider", "test-provider").'
                ),
            ),
        ] = None,
    ) -> ApiResponse[DiscoverModelsResponse]:
        """Discover available models from a provider endpoint.

        Queries the provider's API for available models and updates
        the provider configuration with any discovered models.  When
        ``base_url`` is not configured, returns an empty result.

        Args:
            state: Application state.
            name: Provider name.
            preset_hint: Optional preset name to guide endpoint
                selection (e.g. ``"example-provider"``).

        Returns:
            Discovery result with found models.

        Raises:
            NotFoundError: If the provider does not exist.
        """
        app_state: AppState = state.app_state
        mgmt = provider_management_of(app_state)
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
            result = await provider_management_of(app_state).test_connection(
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
