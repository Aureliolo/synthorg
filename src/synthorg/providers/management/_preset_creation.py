# module-kind: orchestrator
"""Preset-driven provider creation.

Owns ``create_from_preset``: resolving a preset template, choosing the
model seed (live-discovery gateways and local providers skip the static
LiteLLM catalogue), and delegating to the management service's CRUD path.
Kept out of the management service so the service stays focused on CRUD
and the hot-reload contract.
"""

from typing import TYPE_CHECKING

from synthorg.config.schema import ProviderConfig
from synthorg.observability import get_logger
from synthorg.observability.events.provider import PROVIDER_VALIDATION_FAILED
from synthorg.providers.enums import AuthType
from synthorg.providers.errors import ProviderValidationError
from synthorg.providers.management._helpers import models_from_litellm
from synthorg.providers.management.dtos import (
    CreateFromPresetRequest,
    CreateProviderRequest,
)
from synthorg.providers.preset_models import CloudPreset
from synthorg.providers.presets import default_models_for, get_preset

if TYPE_CHECKING:
    from synthorg.providers.management.service import ProviderManagementService

logger = get_logger(__name__)


async def create_provider_from_preset(
    service: ProviderManagementService,
    request: CreateFromPresetRequest,
) -> ProviderConfig:
    """Create a provider from a preset template.

    Args:
        service: The management service whose discovery + CRUD paths build
            and persist the provider.
        request: The preset name plus per-create overrides.

    Returns:
        The newly created and persisted ``ProviderConfig`` built from the
        preset template and request overrides.

    Raises:
        ProviderValidationError: If the preset is unknown.
        ProviderAlreadyExistsError: If the name is taken.
    """
    preset = get_preset(request.preset_name)
    if preset is None:
        msg = f"Unknown preset: {request.preset_name!r}"
        logger.warning(
            PROVIDER_VALIDATION_FAILED,
            preset=request.preset_name,
            error=msg,
        )
        raise ProviderValidationError(msg)

    prefer_live = isinstance(preset, CloudPreset) and preset.prefer_live_discovery
    if request.models is not None:
        models = request.models
    elif preset.auth_type == AuthType.NONE or prefer_live:
        # Local providers AND live-discovery gateways skip the static
        # LiteLLM model_cost table (which would surface the wrong
        # catalogue for an OpenAI-compatible gateway), seeding from the
        # curated list and relying on live discovery in
        # _maybe_discover_preset_models below.
        models = default_models_for(preset)
    else:
        litellm_models = models_from_litellm(preset.litellm_provider)
        models = litellm_models or default_models_for(preset)
    base_url = request.base_url or preset.default_base_url
    if preset.requires_base_url and not base_url:
        msg = f"Preset {preset.name!r} requires a base URL -- provide one via base_url"
        logger.warning(
            PROVIDER_VALIDATION_FAILED,
            preset=request.preset_name,
            error=msg,
        )
        raise ProviderValidationError(msg)
    auth_type = request.auth_type or preset.auth_type
    models = await service._maybe_discover_preset_models(  # noqa: SLF001 -- intra-package preset-creation helper drives the service's discovery gateway
        preset,
        base_url,
        models,
        auth_type=auth_type,
        api_key=(
            request.api_key.get_secret_value() if request.api_key is not None else None
        ),
    )
    create_request = CreateProviderRequest(
        name=request.name,
        driver=preset.driver,
        litellm_provider=preset.litellm_provider,
        auth_type=auth_type,
        api_key=request.api_key,
        subscription_token=request.subscription_token,
        tos_accepted=request.tos_accepted,
        base_url=base_url,
        models=models,
        preset_name=preset.name,
    )
    return await service.create_provider(create_request)
