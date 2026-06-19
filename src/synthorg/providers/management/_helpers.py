"""Private helpers for ProviderManagementService."""

import re
from datetime import UTC, datetime
from enum import Enum, auto
from types import MappingProxyType
from typing import Final
from urllib.parse import urlparse

from pydantic import JsonValue, SecretStr

from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_DISCOVERY_FAILED,
    PROVIDER_LITELLM_LOOKUP_SKIPPED,
    PROVIDER_LITELLM_MODELS_EMPTY,
    PROVIDER_LITELLM_MODELS_LOADED,
    PROVIDER_UPDATE_AUTH_TYPE_UNEXPECTED,
)
from synthorg.providers._auth_type_descriptor import (
    AUTH_TYPE_DESCRIPTORS,
    DiscoveryAuthStyle,
)
from synthorg.providers.enums import AuthType
from synthorg.providers.management.dtos import (
    CreateProviderRequest,
    UpdateProviderRequest,
)

logger = get_logger(__name__)


class _Unset(Enum):
    """Sentinel for the ``connection_name`` override.

    Distinguishes "leave connection_name to the auth-switch logic" from
    "override it with this concrete value (including ``None``)".
    """

    UNSET = auto()


_UNSET = _Unset.UNSET

# Date suffix pattern for model names (e.g. "-YYYYMMDD" like "-20250514")
_DATE_SUFFIX_RE = re.compile(r"-\d{8}$")
_DEFAULT_MAX_CONTEXT: Final[int] = 200_000


def _unwrap_secret(value: SecretStr | str | None) -> str | None:
    """Return the raw string for a request-side credential field.

    Request DTOs use ``SecretStr`` so debug / log / repr cannot leak
    secrets at the API boundary; ``ProviderConfig`` (the persisted
    shape) stores the same fields as plain strings, so this helper
    unwraps when handing values across the boundary.  Plain ``str``
    inputs pass through unchanged so the helper is safe on non-Secret
    legacy values too.
    """
    if value is None:
        return None
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return value


def build_provider_config(
    request: CreateProviderRequest,
    *,
    connection_name: str | None = None,
) -> ProviderConfig:
    """Build a ProviderConfig from a create request.

    Args:
        request: Create provider request.
        connection_name: Catalog connection the service has already minted
            for an api-key credential. Threaded in (rather than attached
            afterwards) so an API-key config validates on construction --
            API_KEY auth mandates connection_name.

    Returns:
        Frozen ProviderConfig.
    """
    is_subscription = request.auth_type == AuthType.SUBSCRIPTION
    tos_accepted_at = (
        datetime.now(UTC) if is_subscription and request.tos_accepted else None
    )
    # api_key is NOT embedded here: the management service mints it into a
    # ConnectionCatalog connection and threads connection_name in (catalog-only
    # credentials). The secret never lands on the ProviderConfig.
    return ProviderConfig(
        driver=request.driver,
        litellm_provider=request.litellm_provider,
        auth_type=request.auth_type,
        connection_name=connection_name,
        subscription_token=(
            _unwrap_secret(request.subscription_token) if is_subscription else None
        ),
        tos_accepted_at=tos_accepted_at,
        base_url=request.base_url,
        oauth_token_url=request.oauth_token_url,
        oauth_client_id=request.oauth_client_id,
        oauth_client_secret=_unwrap_secret(request.oauth_client_secret),
        oauth_scope=request.oauth_scope,
        custom_header_name=request.custom_header_name,
        custom_header_value=_unwrap_secret(request.custom_header_value),
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

# Subset of ``_UPDATE_FIELDS`` that arrive on the request as
# ``SecretStr`` and must be unwrapped before being copied into
# ``ProviderConfig`` (which stores plain strings).
_UPDATE_SECRET_FIELDS: frozenset[str] = frozenset(
    {"oauth_client_secret", "custom_header_value"},
)


def apply_update(
    existing: ProviderConfig,
    request: UpdateProviderRequest,
    *,
    connection_name: str | None | _Unset = _UNSET,
) -> ProviderConfig:
    """Apply partial update fields to an existing config.

    When auth_type changes, orphaned credential fields from the
    old auth type are automatically cleared.

    Args:
        existing: Current provider configuration.
        request: Partial update request.
        connection_name: Override for the catalog ``connection_name``.
            Left unset, the field follows the auth-switch clearing logic
            (cleared when the new auth type does not own it). Pass a
            concrete value (string or ``None``) when the service has just
            minted or deleted the backing credential connection so the
            merged config validates with a complete credential reference.

    Returns:
        New ProviderConfig with updates applied.
    """
    # ``model_fields_set`` distinguishes "field omitted" (no change)
    # from "field explicitly set to ``None``" (clear).  Without this,
    # the previous ``value is not None`` gate made it impossible to
    # null out fields like ``litellm_provider`` / ``base_url`` /
    # ``oauth_*`` / ``custom_header_*`` / ``models`` via PATCH.  The
    # existing ``clear_api_key`` / ``clear_subscription_token`` flags
    # on the request DTO retain their original semantic for those two
    # SecretStr fields (handled in ``_apply_credential_updates``).
    sent_fields = request.model_fields_set
    updates: dict[str, object] = {}
    for field in _UPDATE_FIELDS:
        if field not in sent_fields:
            continue
        value = getattr(request, field)
        if value is None:
            updates[field] = None
        else:
            updates[field] = (
                _unwrap_secret(value) if field in _UPDATE_SECRET_FIELDS else value
            )

    # auth_type change: clear all fields NOT owned by the new auth type.
    # ``.get`` (not subscript) tolerates a non-AuthType value that slipped
    # past request validation: keep stays empty so every credential field
    # clears, and the defensive isinstance guard below logs + rejects it.
    if request.auth_type is not None:
        updates["auth_type"] = request.auth_type
        new_descriptor = AUTH_TYPE_DESCRIPTORS.get(request.auth_type)
        keep = set(new_descriptor.owned_fields) if new_descriptor is not None else set()
        for descriptor in AUTH_TYPE_DESCRIPTORS.values():
            for f in descriptor.owned_fields:
                if f not in keep:
                    updates[f] = None

    updated_auth_type = updates.get("auth_type", existing.auth_type)
    if isinstance(updated_auth_type, AuthType):
        final_auth_type = updated_auth_type
    else:
        # Defensive: ``auth_type`` is always an ``AuthType`` (from the
        # validated request or the existing config). Log before falling
        # back so a future deserialisation mismatch that silently keeps
        # the old auth type (and mis-gates credential clearing) is visible
        # rather than failing silently.
        logger.warning(
            PROVIDER_UPDATE_AUTH_TYPE_UNEXPECTED,
            value_type=type(updated_auth_type).__name__,
            kept_auth_type=existing.auth_type.value,
        )
        final_auth_type = existing.auth_type
    _apply_credential_updates(updates, request, final_auth_type)

    # The service mints/deletes the catalog connection out-of-band and
    # passes the resulting reference here so the merged config validates
    # with a complete credential (API-key auth requires connection_name).
    if not isinstance(connection_name, _Unset):
        updates["connection_name"] = connection_name

    # Use model_validate (not model_copy) to run validators on the merged result
    merged = {**existing.model_dump(mode="python"), **updates}
    return ProviderConfig.model_validate(merged)


def _apply_credential_updates(
    updates: dict[str, object],
    request: UpdateProviderRequest,
    final_auth_type: AuthType,
) -> None:
    """Apply set/clear logic for subscription_token and tos_accepted_at.

    ``subscription_token`` arrives as ``SecretStr`` on the request DTO;
    unwrap to the raw string before storing on ``ProviderConfig`` (which
    keeps it as a plain ``NotBlankStr``). The API-key credential is no
    longer embedded: it lives in the connection catalog and the service
    stamps ``connection_name`` via ``apply_update``'s override argument.
    """
    descriptor = AUTH_TYPE_DESCRIPTORS[final_auth_type]

    # subscription_token + tos_accepted_at: only the subscription-style
    # auth type (the one mandating ToS) owns these fields.
    if descriptor.requires_tos:
        if request.subscription_token is not None:
            updates["subscription_token"] = _unwrap_secret(request.subscription_token)
        elif request.clear_subscription_token:
            updates["subscription_token"] = None
        if request.tos_accepted:
            updates["tos_accepted_at"] = datetime.now(UTC)
    else:
        updates["subscription_token"] = None
        updates["tos_accepted_at"] = None


def serialize_providers(
    providers: dict[str, ProviderConfig],
) -> dict[str, JsonValue]:
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
    api_key: str | None,
) -> dict[str, str] | None:
    """Build auth headers for model discovery from provider config.

    Returns headers appropriate for the provider's auth type, or
    ``None`` for ``AuthType.NONE`` or when credentials are absent.
    OAuth-based discovery is not yet supported (token acquisition
    requires a separate flow); a log message is emitted when skipped.

    Args:
        config: Provider configuration.
        api_key: The catalog-resolved API key for the provider's
            ``connection_name`` (the credential is no longer embedded on
            the config); ``None`` when unresolved.

    Returns:
        Auth headers dict, or ``None``.
    """
    style = AUTH_TYPE_DESCRIPTORS[config.auth_type].discovery_style
    if style is DiscoveryAuthStyle.BEARER_API_KEY and api_key:
        return {"Authorization": f"Bearer {api_key}"}
    if (
        style is DiscoveryAuthStyle.CUSTOM_HEADER
        and config.custom_header_name
        and config.custom_header_value
    ):
        return {config.custom_header_name: config.custom_header_value}
    if style is DiscoveryAuthStyle.BEARER_SUBSCRIPTION and config.subscription_token:
        return {"Authorization": f"Bearer {config.subscription_token}"}
    if style is DiscoveryAuthStyle.OAUTH_UNSUPPORTED:
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


def _coerce_cost(value: JsonValue) -> float:
    """Coerce a litellm per-token cost to ``float``.

    Returns:
        The value as a ``float``.

    Raises:
        TypeError: If *value* is not a real number (``bool`` is rejected
            too). The caller's ``except (TypeError, ValueError)`` turns
            this into a skipped, logged ``malformed_model_entry``.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"non-numeric cost value: {type(value).__name__}"
        raise TypeError(msg)
    return float(value)


def _parse_litellm_entry(
    model_name: str,
    info: dict[str, JsonValue],
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
            cost_per_1k_input=round(_coerce_cost(input_cost) * 1000, 6),
            cost_per_1k_output=round(_coerce_cost(output_cost) * 1000, 6),
            max_context=(
                max_input if isinstance(max_input, int) else _DEFAULT_MAX_CONTEXT
            ),
        )
    except (TypeError, ValueError) as exc:
        # WARNING (not DEBUG) so an upstream LiteLLM data-shape change
        # that suddenly invalidates a provider's model surface is
        # visible to operators -- the alternative is a silent "no
        # models loaded" outcome at line ~413 that is indistinguishable
        # from a provider that genuinely has zero models.
        logger.warning(
            PROVIDER_LITELLM_LOOKUP_SKIPPED,
            reason="malformed_model_entry",
            model=model_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
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

    from synthorg.providers.drivers.litellm_model_info import (  # noqa: PLC0415
        extract_model_metadata,
    )
    from synthorg.providers.family_parser import get_family_parser  # noqa: PLC0415
    from synthorg.providers.presets import MODEL_VERSION_FILTERS  # noqa: PLC0415

    parser = get_family_parser()
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
        config = config.model_copy(
            update={
                "metadata": extract_model_metadata(
                    info,
                    litellm_provider=litellm_provider,
                    model_id=config.id,
                    parser=parser,
                ),
            },
        )
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
