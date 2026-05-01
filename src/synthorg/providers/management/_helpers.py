"""Private helpers for ProviderManagementService."""

import re
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Final
from urllib.parse import urlparse

from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.observability import get_logger
from synthorg.observability.events.provider import (
    PROVIDER_DISCOVERY_FAILED,
    PROVIDER_LITELLM_LOOKUP_SKIPPED,
    PROVIDER_LITELLM_MODELS_EMPTY,
    PROVIDER_LITELLM_MODELS_LOADED,
)
from synthorg.providers.enums import AuthType
from synthorg.providers.management.dtos import (  # noqa: TC001
    CreateProviderRequest,
    UpdateProviderRequest,
)

logger = get_logger(__name__)

# Date suffix pattern for model names (e.g. "-YYYYMMDD" like "-20250514")
_DATE_SUFFIX_RE = re.compile(r"-\d{8}$")
_DEFAULT_MAX_CONTEXT: Final[int] = 200_000


def build_provider_config(
    request: CreateProviderRequest,
) -> ProviderConfig:
    """Build a ProviderConfig from a create request.

    Args:
        request: Create provider request.

    Returns:
        Frozen ProviderConfig.
    """
    is_subscription = request.auth_type == AuthType.SUBSCRIPTION
    tos_accepted_at = (
        datetime.now(UTC) if is_subscription and request.tos_accepted else None
    )
    return ProviderConfig(
        driver=request.driver,
        litellm_provider=request.litellm_provider,
        auth_type=request.auth_type,
        api_key=request.api_key,
        subscription_token=request.subscription_token if is_subscription else None,
        tos_accepted_at=tos_accepted_at,
        base_url=request.base_url,
        oauth_token_url=request.oauth_token_url,
        oauth_client_id=request.oauth_client_id,
        oauth_client_secret=request.oauth_client_secret,
        oauth_scope=request.oauth_scope,
        custom_header_name=request.custom_header_name,
        custom_header_value=request.custom_header_value,
        models=request.models,
        preset_name=request.preset_name,
    )


_UPDATE_FIELDS: tuple[str, ...] = (
    "driver",
    "litellm_provider",
    "base_url",
    "oauth_token_url",
    "oauth_client_id",
    "oauth_client_secret",
    "oauth_scope",
    "custom_header_name",
    "custom_header_value",
    "models",
)


# Fields owned by each auth type.  When switching auth types, fields
# not owned by the new type are cleared.
_AUTH_OWNED_FIELDS: Final[MappingProxyType[AuthType, tuple[str, ...]]] = (
    MappingProxyType(
        {
            AuthType.API_KEY: ("api_key",),
            AuthType.OAUTH: (
                "api_key",
                "oauth_client_secret",
                "oauth_token_url",
                "oauth_client_id",
                "oauth_scope",
            ),
            AuthType.CUSTOM_HEADER: ("custom_header_name", "custom_header_value"),
            AuthType.SUBSCRIPTION: ("subscription_token", "tos_accepted_at"),
            AuthType.NONE: (),
        }
    )
)


def apply_update(
    existing: ProviderConfig,
    request: UpdateProviderRequest,
) -> ProviderConfig:
    """Apply partial update fields to an existing config.

    When auth_type changes, orphaned credential fields from the
    old auth type are automatically cleared.

    Args:
        existing: Current provider configuration.
        request: Partial update request.

    Returns:
        New ProviderConfig with updates applied.
    """
    updates: dict[str, Any] = {}
    for field in _UPDATE_FIELDS:
        value = getattr(request, field)
        if value is not None:
            updates[field] = value

    # auth_type change: clear all fields NOT owned by the new auth type
    if request.auth_type is not None:
        updates["auth_type"] = request.auth_type
        keep = set(_AUTH_OWNED_FIELDS.get(request.auth_type, ()))
        for fields in _AUTH_OWNED_FIELDS.values():
            for f in fields:
                if f not in keep:
                    updates[f] = None

    final_auth_type = updates.get("auth_type", existing.auth_type)
    _apply_credential_updates(updates, request, final_auth_type)

    # Use model_validate (not model_copy) to run validators on the merged result
    merged = {**existing.model_dump(mode="python"), **updates}
    return ProviderConfig.model_validate(merged)


def _apply_credential_updates(
    updates: dict[str, Any],
    request: UpdateProviderRequest,
    final_auth_type: AuthType,
) -> None:
    """Apply set/clear logic for api_key, subscription_token, and tos_accepted_at."""
    # api_key: only set/clear when the resulting auth type supports it
    if final_auth_type in (AuthType.API_KEY, AuthType.OAUTH):
        if request.api_key is not None:
            updates["api_key"] = request.api_key
        elif request.clear_api_key:
            updates["api_key"] = None
    else:
        updates["api_key"] = None

    # subscription_token: only set/clear when auth type is SUBSCRIPTION
    if final_auth_type == AuthType.SUBSCRIPTION:
        if request.subscription_token is not None:
            updates["subscription_token"] = request.subscription_token
        elif request.clear_subscription_token:
            updates["subscription_token"] = None
        if request.tos_accepted:
            updates["tos_accepted_at"] = datetime.now(UTC)
    else:
        updates["subscription_token"] = None
        updates["tos_accepted_at"] = None


def serialize_providers(
    providers: dict[str, ProviderConfig],
) -> dict[str, Any]:
    """Serialize provider dict for JSON persistence.

    Args:
        providers: Provider configurations.

    Returns:
        JSON-safe dict of serialized provider configs.
    """
    return {name: config.model_dump(mode="json") for name, config in providers.items()}


PORT_TO_PRESET: Final[MappingProxyType[int, str]] = MappingProxyType(
    {
        11434: "ollama",
        1234: "lm-studio",
    }
)


def build_discovery_headers(
    config: ProviderConfig,
) -> dict[str, str] | None:
    """Build auth headers for model discovery from provider config.

    Returns headers appropriate for the provider's auth type, or
    ``None`` for ``AuthType.NONE`` or when credentials are absent.
    OAuth-based discovery is not yet supported (token acquisition
    requires a separate flow); a log message is emitted when skipped.

    Args:
        config: Provider configuration.

    Returns:
        Auth headers dict, or ``None``.
    """
    if config.auth_type == AuthType.API_KEY and config.api_key:
        return {"Authorization": f"Bearer {config.api_key}"}
    if (
        config.auth_type == AuthType.CUSTOM_HEADER
        and config.custom_header_name
        and config.custom_header_value
    ):
        return {config.custom_header_name: config.custom_header_value}
    if config.auth_type == AuthType.SUBSCRIPTION and config.subscription_token:
        return {"Authorization": f"Bearer {config.subscription_token}"}
    if config.auth_type == AuthType.OAUTH:
        logger.debug(
            PROVIDER_DISCOVERY_FAILED,
            reason="oauth_discovery_unsupported",
            auth_type=config.auth_type.value,
        )
    return None


def infer_preset_hint(base_url: str) -> str | None:
    """Infer the preset name from a provider base URL.

    Uses port-based heuristics for common local providers.
    Recognized ports: 11434 (ollama), 1234 (lm-studio).

    Args:
        base_url: Provider base URL.

    Returns:
        Preset name hint, or ``None`` if unrecognized.
    """
    try:
        port = urlparse(base_url).port
    except ValueError:
        logger.debug(
            PROVIDER_DISCOVERY_FAILED,
            reason="invalid_port_in_url",
            base_url=base_url,
        )
        return None
    if port is None:
        return None
    return PORT_TO_PRESET.get(port)


def _parse_litellm_entry(
    model_name: str,
    info: dict[str, Any],
    litellm_provider: str,
    version_filter: re.Pattern[str] | None,
) -> tuple[str, ProviderModelConfig] | None:
    """Parse a single litellm.model_cost entry into a model config.

    Args:
        model_name: Raw model key from litellm.model_cost.
        info: Model metadata dict.
        litellm_provider: Provider identifier for prefix stripping.
        version_filter: Optional regex; entry is skipped when it
            does not match.

    Returns:
        ``(base_name, config)`` tuple, or ``None`` if the entry
        should be skipped (wrong provider, filtered, malformed).
    """
    if info.get("litellm_provider") != litellm_provider:
        return None

    # Strip provider prefix if present (e.g. "provider/model-name")
    model_id = model_name.removeprefix(f"{litellm_provider}/")

    if version_filter and not version_filter.search(model_id):
        return None

    base_name = _DATE_SUFFIX_RE.sub("", model_id)
    input_cost = info.get("input_cost_per_token") or 0
    output_cost = info.get("output_cost_per_token") or 0
    max_input = info.get("max_input_tokens", _DEFAULT_MAX_CONTEXT)

    try:
        config = ProviderModelConfig(
            id=model_id,
            cost_per_1k_input=round(input_cost * 1000, 6),
            cost_per_1k_output=round(output_cost * 1000, 6),
            max_context=(
                max_input if isinstance(max_input, int) else _DEFAULT_MAX_CONTEXT
            ),
        )
    except (TypeError, ValueError) as exc:
        logger.debug(
            PROVIDER_LITELLM_LOOKUP_SKIPPED,
            reason="malformed_model_entry",
            model=model_name,
            error=str(exc),
        )
        return None

    return (base_name, config)


def models_from_litellm(
    litellm_provider: str,
) -> tuple[ProviderModelConfig, ...]:
    """Query litellm.model_cost for all models of a given provider.

    Returns model configs populated with pricing and context data
    from LiteLLM's built-in model database. Prefers shorter model
    identifiers over dated variants (e.g. ``example-large-001``
    over ``example-large-001-20260205``).

    Provider-specific model generation filters (defined in
    ``presets.MODEL_VERSION_FILTERS``) exclude older models.

    Args:
        litellm_provider: LiteLLM provider identifier
            (e.g. ``"example-provider"``).

    Returns:
        Tuple of model configs, or empty tuple if litellm is not
        installed or no models match.
    """
    try:
        import litellm  # noqa: PLC0415
    except ImportError:
        logger.warning(
            PROVIDER_LITELLM_LOOKUP_SKIPPED,
            reason="litellm_not_installed",
            provider=litellm_provider,
        )
        return ()

    from synthorg.providers.presets import MODEL_VERSION_FILTERS  # noqa: PLC0415

    version_filter = MODEL_VERSION_FILTERS.get(litellm_provider)
    seen: dict[str, ProviderModelConfig] = {}

    for model_name, info in litellm.model_cost.items():
        if not isinstance(info, dict):
            continue
        parsed = _parse_litellm_entry(
            model_name,
            info,
            litellm_provider,
            version_filter,
        )
        if parsed is None:
            continue
        base_name, config = parsed
        existing = seen.get(base_name)
        if existing is not None and len(existing.id) <= len(config.id):
            continue
        seen[base_name] = config

    result = tuple(sorted(seen.values(), key=lambda m: m.id))
    if result:
        logger.info(
            PROVIDER_LITELLM_MODELS_LOADED,
            provider=litellm_provider,
            count=len(result),
        )
    else:
        logger.info(
            PROVIDER_LITELLM_MODELS_EMPTY,
            provider=litellm_provider,
            version_filter_applied=version_filter is not None,
        )
    return result
