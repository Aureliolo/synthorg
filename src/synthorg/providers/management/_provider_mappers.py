# module-kind: code
"""Domain-to-response mappers for the provider management surface.

Extracted from ``dtos.py``. Convert ``ProviderConfig`` /
``ProviderModelConfig`` into the secret-free response DTOs.
"""

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.management._provider_responses import (
    ProviderModelResponse,
    ProviderResponse,
)


def to_provider_response(
    config: ProviderConfig,
    *,
    name: str | None,
) -> ProviderResponse:
    """Convert a ProviderConfig to a safe ProviderResponse.

    Strips all secrets and provides boolean credential indicators.
    Resolves local model management capabilities from the preset
    when ``preset_name`` is set.

    Args:
        config: Provider configuration (may contain secrets).
        name: Provider identifier. Pass the provider name for paginated
            list responses (so each item carries its own name
            independently of collection ordering). Pass ``None`` on
            single-provider GET-by-path responses where the URL
            already carries the identifier. The argument is required
            (no default) so a future list endpoint cannot silently
            omit it and break the dict-by-name reconstruction
            contract on the frontend with ``name=None`` items.

    Returns:
        Safe response DTO with secrets stripped.
    """
    from synthorg.providers.presets import (  # noqa: PLC0415
        LocalPreset,
        get_preset,
    )

    tos_str = (
        config.tos_accepted_at.isoformat()
        if config.tos_accepted_at is not None
        else None
    )
    preset = get_preset(config.preset_name) if config.preset_name else None
    # Local-management capability flags (pull/delete/config) live only
    # on LocalPreset and are exposed back to the dashboard through this
    # ProviderResponse DTO.  Cloud providers default them to False.
    local_preset = preset if isinstance(preset, LocalPreset) else None
    return ProviderResponse(
        name=name,
        driver=config.driver,
        litellm_provider=config.litellm_provider,
        auth_type=config.auth_type,
        base_url=config.base_url,
        models=config.models,
        has_api_key=config.api_key is not None,
        has_oauth_credentials=(
            config.oauth_client_id is not None
            and config.oauth_client_secret is not None
            and config.oauth_token_url is not None
        ),
        has_custom_header=(
            config.custom_header_name is not None
            and config.custom_header_value is not None
        ),
        has_subscription_token=config.subscription_token is not None,
        tos_accepted_at=tos_str,
        oauth_token_url=config.oauth_token_url,
        oauth_client_id=config.oauth_client_id,
        oauth_scope=config.oauth_scope,
        custom_header_name=config.custom_header_name,
        preset_name=config.preset_name,
        supports_model_pull=local_preset.supports_model_pull if local_preset else False,
        supports_model_delete=local_preset.supports_model_delete
        if local_preset
        else False,
        supports_model_config=local_preset.supports_model_config
        if local_preset
        else False,
    )


def to_provider_model_response(
    config: ProviderModelConfig,
    capabilities: ModelCapabilities | None = None,
) -> ProviderModelResponse:
    """Convert a ProviderModelConfig to an enriched response.

    When *capabilities* is provided, capability booleans are overlaid.
    Otherwise, defaults are used.

    Args:
        config: Model configuration from provider config.
        capabilities: Runtime capabilities from the driver layer.

    Returns:
        Enriched model response DTO.
    """
    return ProviderModelResponse(
        id=config.id,
        alias=config.alias,
        cost_per_1k_input=config.cost_per_1k_input,
        cost_per_1k_output=config.cost_per_1k_output,
        # ``ProviderModelConfig`` does not yet carry a per-row
        # currency; the project-wide default reflects the operator's
        # ``budget.currency`` setting and aggregation sites enforce
        # same-currency at sum time.  When per-model overrides land,
        # plumb that value through here.
        currency=DEFAULT_CURRENCY,
        max_context=config.max_context,
        estimated_latency_ms=config.estimated_latency_ms,
        local_params=config.local_params,
        supports_tools=(
            capabilities.supports_tools if capabilities is not None else False
        ),
        supports_vision=(
            capabilities.supports_vision if capabilities is not None else False
        ),
        supports_streaming=(
            capabilities.supports_streaming if capabilities is not None else True
        ),
    )
