# module-kind: declarative
"""Vendor presets for generic-HTTP connections.

``ConnectionType`` describes a protocol and credential *shape*, not a vendor:
a bespoke member is minted only where there is vendor-specific behaviour to
hang off it (an authenticator, a webhook verifier, a tool family). An API
served over HTTPS behind an API key has none of that, so it lands on
``GENERIC_HTTP`` and its identity rides in the connection's ``metadata``,
exactly as a deploy platform or a container registry provider does
(:mod:`~synthorg.integrations.connections.deploy_target`,
:mod:`~synthorg.integrations.connections.registry_target`).

Without that identity the operator is asked for a base URL nothing reads,
the health probe guesses an auth header the vendor does not accept, and a
bound MCP server is handed a credential field it never looks up. The preset
supplies all three from one declarative record.

Real vendor names live here by the same exemption the LLM provider presets
take: a declarative registry is the one place they are allowed.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    HTTP_VENDOR_METADATA_UNRECOGNISED,
)

logger = get_logger(__name__)

METADATA_KEY_VENDOR: Final[str] = "vendor"


class HttpVendor(StrEnum):
    """A generic-HTTP service with a code-defined request contract."""

    BRAVE = "brave"
    TAVILY = "tavily"
    EXA = "exa"
    CUSTOM = "custom"


class HttpVendorPreset(BaseModel):
    """Everything the platform needs to talk to one HTTP vendor.

    Attributes:
        id: The vendor discriminator persisted in connection metadata. A
            plain string rather than :class:`HttpVendor` so a test can bind
            a fictional vendor without minting an enum member for it; the
            registry below is what the enum keys.
        label: Operator-facing name.
        base_url: Absolute HTTPS origin the connection defaults to, so the
            operator is never asked for a URL the platform already knows.
        auth_header: Header the API key travels in.
        auth_template: Value template for that header; ``{key}`` is replaced
            with the resolved key (e.g. ``"Bearer {key}"``).
        health_path: Path probed to prove the credential works. Empty probes
            ``base_url`` itself.
        health_params: Query parameters the health probe must send. A search
            API rejects a bare request as malformed, which would read as an
            unhealthy connection even with a valid key.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr
    label: NotBlankStr
    base_url: NotBlankStr
    auth_header: NotBlankStr
    auth_template: NotBlankStr = "{key}"
    health_path: str = ""
    health_params: dict[str, str] = Field(default_factory=dict)

    def auth_headers(self, key: str) -> dict[str, str]:
        """Render this vendor's auth header for *key*.

        Returns:
            A single-entry header mapping. Carries a secret: never log it.
        """
        return {self.auth_header: self.auth_template.format(key=key)}


_BRAVE: Final = HttpVendorPreset(
    id=NotBlankStr(HttpVendor.BRAVE.value),
    label=NotBlankStr("Brave Search"),
    base_url=NotBlankStr("https://api.search.brave.com/res/v1/web/search"),
    auth_header=NotBlankStr("X-Subscription-Token"),
    # A search endpoint with no query is a 4xx, so the probe sends the
    # smallest well-formed search rather than reporting a working key
    # as unhealthy.
    health_params={"q": "ping", "count": "1"},
)

_TAVILY: Final = HttpVendorPreset(
    id=NotBlankStr(HttpVendor.TAVILY.value),
    label=NotBlankStr("Tavily"),
    base_url=NotBlankStr("https://api.tavily.com/search"),
    auth_header=NotBlankStr("Authorization"),
    auth_template=NotBlankStr("Bearer {key}"),
)

_EXA: Final = HttpVendorPreset(
    id=NotBlankStr(HttpVendor.EXA.value),
    label=NotBlankStr("Exa"),
    base_url=NotBlankStr("https://api.exa.ai/search"),
    auth_header=NotBlankStr("x-api-key"),
)


HTTP_VENDOR_PRESETS: Final[Mapping[HttpVendor, HttpVendorPreset]] = MappingProxyType(
    {
        HttpVendor.BRAVE: _BRAVE,
        HttpVendor.TAVILY: _TAVILY,
        HttpVendor.EXA: _EXA,
    }
)


def resolve_vendor(metadata: Mapping[str, str]) -> HttpVendorPreset | None:
    """Read the vendor preset from a connection's metadata.

    Args:
        metadata: The connection record's metadata mapping.

    Returns:
        The declared preset, or ``None`` when the connection is a custom
        endpoint, declares nothing, or names a vendor this build has no
        preset for. ``None`` means "use the operator's own base URL and
        generic auth", never a guessed vendor.
    """
    declared = metadata.get(METADATA_KEY_VENDOR, "")
    if not declared or declared == HttpVendor.CUSTOM.value:
        return None
    try:
        vendor = HttpVendor(declared)
    except ValueError:
        logger.warning(
            HTTP_VENDOR_METADATA_UNRECOGNISED,
            field=METADATA_KEY_VENDOR,
            resolved="none",
        )
        return None
    return HTTP_VENDOR_PRESETS.get(vendor)


def preset_for(vendor: HttpVendor) -> HttpVendorPreset | None:
    """Return the preset for *vendor*, or ``None`` for a custom endpoint.

    Returns:
        The registered :class:`HttpVendorPreset`, or ``None``.
    """
    return HTTP_VENDOR_PRESETS.get(vendor)


__all__ = [
    "HTTP_VENDOR_PRESETS",
    "METADATA_KEY_VENDOR",
    "HttpVendor",
    "HttpVendorPreset",
    "preset_for",
    "resolve_vendor",
]
