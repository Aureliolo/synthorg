"""Per-auth-type descriptor table for provider credential dispatch.

Centralises the facts about each :class:`AuthType` that the credential
update, discovery-header, and rotation paths each need: the config
fields an auth type owns (cleared for other types when switching auth
types), whether it supports an ``api_key``, whether it mandates
terms-of-service acceptance, and how model-discovery auth headers are
built. A single ``MappingProxyType`` keyed by every auth type, plus an
import-time completeness guard, means adding a new auth type fails
loudly at import unless its descriptor is supplied -- instead of
silently defaulting to "no header" / "clear all credentials" at one of
the call sites.
"""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from synthorg.providers.enums import AuthType


class DiscoveryAuthStyle(StrEnum):
    """How model-discovery auth headers are built for an auth type.

    Members:
        BEARER_API_KEY: ``Authorization: Bearer <api_key>``.
        CUSTOM_HEADER: a caller-named header carrying the secret value.
        BEARER_SUBSCRIPTION: ``Authorization: Bearer <subscription_token>``.
        OAUTH_UNSUPPORTED: discovery needs a separate token flow; skip
            and emit a debug log.
        NONE: no discovery auth header.
    """

    BEARER_API_KEY = "bearer_api_key"
    CUSTOM_HEADER = "custom_header"
    BEARER_SUBSCRIPTION = "bearer_subscription"
    OAUTH_UNSUPPORTED = "oauth_unsupported"
    NONE = "none"


@dataclass(frozen=True)
class AuthTypeDescriptor:
    """Dispatch metadata for one :class:`AuthType`.

    Attributes:
        owned_fields: ``ProviderConfig`` fields this auth type owns;
            fields owned by other auth types are cleared on switch.
        supports_api_key: Whether an ``api_key`` credential is meaningful
            for this auth type (gates minting the secret into the
            connection catalog and clearing the backing connection).
        requires_tos: Whether terms-of-service acceptance is mandatory
            (true only for subscription-style auth).
        discovery_style: How a model-discovery auth header is built.
    """

    owned_fields: tuple[str, ...]
    supports_api_key: bool
    requires_tos: bool
    discovery_style: DiscoveryAuthStyle


AUTH_TYPE_DESCRIPTORS: MappingProxyType[AuthType, AuthTypeDescriptor] = (
    MappingProxyType(
        {
            AuthType.API_KEY: AuthTypeDescriptor(
                owned_fields=("connection_name",),
                supports_api_key=True,
                requires_tos=False,
                discovery_style=DiscoveryAuthStyle.BEARER_API_KEY,
            ),
            AuthType.OAUTH: AuthTypeDescriptor(
                owned_fields=(
                    "connection_name",
                    "oauth_client_secret",
                    "oauth_token_url",
                    "oauth_client_id",
                    "oauth_scope",
                ),
                supports_api_key=True,
                requires_tos=False,
                discovery_style=DiscoveryAuthStyle.OAUTH_UNSUPPORTED,
            ),
            AuthType.CUSTOM_HEADER: AuthTypeDescriptor(
                owned_fields=("custom_header_name", "custom_header_value"),
                supports_api_key=False,
                requires_tos=False,
                discovery_style=DiscoveryAuthStyle.CUSTOM_HEADER,
            ),
            AuthType.SUBSCRIPTION: AuthTypeDescriptor(
                owned_fields=("subscription_token", "tos_accepted_at"),
                supports_api_key=False,
                requires_tos=True,
                discovery_style=DiscoveryAuthStyle.BEARER_SUBSCRIPTION,
            ),
            AuthType.NONE: AuthTypeDescriptor(
                owned_fields=(),
                supports_api_key=False,
                requires_tos=False,
                discovery_style=DiscoveryAuthStyle.NONE,
            ),
        }
    )
)


_missing_auth_types = set(AuthType) - set(AUTH_TYPE_DESCRIPTORS)
if _missing_auth_types:
    _msg = (
        f"Missing AUTH_TYPE_DESCRIPTORS entries for: "
        f"{sorted(a.value for a in _missing_auth_types)}"
    )
    raise ValueError(_msg)

del _missing_auth_types
