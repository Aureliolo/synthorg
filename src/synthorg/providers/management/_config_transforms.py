# module-kind: code
"""Provider config build / update / serialise transforms.

Pure transforms between the request DTOs (``CreateProviderRequest`` /
``UpdateProviderRequest``) and the persisted :class:`ProviderConfig`,
plus the versioned-envelope serialiser. This module is the single owner
of request-to-config mapping, kept distinct from discovery-auth and
litellm parsing so those concerns evolve independently.
"""

from datetime import UTC, datetime
from enum import Enum, auto

from pydantic import SecretStr

from synthorg.config.provider_schema import (
    PROVIDERS_CONFIG_SCHEMA_VERSION,
    ProvidersConfigEnvelope,
)
from synthorg.config.schema import ProviderConfig
from synthorg.observability import get_logger
from synthorg.observability.events.provider import (
    PROVIDER_UPDATE_AUTH_TYPE_UNEXPECTED,
)
from synthorg.providers._auth_type_descriptor import AUTH_TYPE_DESCRIPTORS
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
        keep_alive=request.keep_alive,
        oauth_token_url=request.oauth_token_url,
        oauth_client_id=request.oauth_client_id,
        oauth_client_secret=_unwrap_secret(request.oauth_client_secret),
        oauth_scope=request.oauth_scope,
        custom_header_name=request.custom_header_name,
        custom_header_value=_unwrap_secret(request.custom_header_value),
        models=request.models,
        preset_name=request.preset_name,
        agent_eligible=request.agent_eligible,
    )


_UPDATE_FIELDS: tuple[str, ...] = (
    "driver",
    "litellm_provider",
    "base_url",
    "keep_alive",
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

    # ``agent_eligible`` is a non-nullable bool on the config, so it is handled
    # apart from the generic clear-on-None loop: apply it only when the request
    # carried a concrete True/False (``None`` means "not sent, no change").
    if request.agent_eligible is not None:
        updates["agent_eligible"] = request.agent_eligible

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


def serialize_provider_envelope(
    providers: dict[str, ProviderConfig],
) -> str:
    """Serialize providers into a versioned JSON envelope for persistence.

    Wraps the provider dict in a :class:`ProvidersConfigEnvelope` stamped
    with the current schema version and dumps it to a JSON string. The
    reader (``ConfigResolver.get_provider_configs``) validates the version
    and falls back to code defaults on a mismatch or a corrupt blob.

    Args:
        providers: Provider configurations keyed by name.

    Returns:
        The JSON-encoded versioned envelope.
    """
    envelope = ProvidersConfigEnvelope(
        schema_version=PROVIDERS_CONFIG_SCHEMA_VERSION,
        providers=providers,
    )
    return envelope.model_dump_json()
