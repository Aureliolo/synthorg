# module-kind: code
"""Discovery-auth headers and preset hints for provider management.

Builds the per-auth-type HTTP headers used by model discovery and the
port-based preset heuristic for local providers. This module owns the
discovery-auth concern alone, kept distinct from config transforms and
litellm parsing so header construction never risks touching them.
"""

from types import MappingProxyType
from typing import Final
from urllib.parse import urlparse

from synthorg.config.schema import ProviderConfig
from synthorg.observability import get_logger
from synthorg.observability.events.provider import PROVIDER_DISCOVERY_FAILED
from synthorg.providers._auth_type_descriptor import (
    AUTH_TYPE_DESCRIPTORS,
    DiscoveryAuthStyle,
)

logger = get_logger(__name__)


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
