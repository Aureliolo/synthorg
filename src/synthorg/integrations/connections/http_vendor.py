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

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    HTTP_VENDOR_METADATA_UNRECOGNISED,
)

logger = get_logger(__name__)

METADATA_KEY_VENDOR: Final[str] = "vendor"
_KEY_PLACEHOLDER: Final[str] = "{key}"


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
        health_body: JSON body the health probe must POST. Set for a vendor
            whose endpoint answers only POST, where a GET (however well
            formed) returns 405 and every valid credential would read as
            unhealthy. Mutually exclusive with ``health_params``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr
    label: NotBlankStr
    base_url: NotBlankStr
    auth_header: NotBlankStr
    auth_template: NotBlankStr = "{key}"
    health_path: str = ""
    health_params: dict[str, str] = Field(default_factory=dict)
    health_body: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_probe_shape(self) -> HttpVendorPreset:
        """Reject a preset that describes two different probes.

        The probe issues one request, so params and a body cannot both
        apply; declaring both would silently drop one and leave the preset
        reading as though it had been honoured.

        Returns:
            The validated preset.

        Raises:
            ValueError: If both a query and a body are declared.
        """
        if self.health_params and self.health_body:
            msg = (
                f"vendor {self.id!r} declares both health_params and "
                f"health_body; the probe sends one request, not two"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _template_consumes_the_key(self) -> HttpVendorPreset:
        """Reject a template that would render without the credential.

        ``str.format`` ignores a keyword the template never names, so a
        template missing ``{key}`` produces a constant header and drops the
        secret silently: every request would leave authenticated-looking and
        come back 401, with nothing in the preset to point at.

        Returns:
            The validated preset.

        Raises:
            ValueError: If the template never substitutes the key.
        """
        if _KEY_PLACEHOLDER not in self.auth_template:
            msg = (
                f"auth_template for vendor {self.id!r} must contain "
                f"{_KEY_PLACEHOLDER!r} or the credential is never sent"
            )
            raise ValueError(msg)
        return self

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
    # The endpoint answers POST only, so a query-string probe returns 405
    # and would report every valid credential as unhealthy.
    health_body={"query": "ping"},
)

_EXA: Final = HttpVendorPreset(
    id=NotBlankStr(HttpVendor.EXA.value),
    label=NotBlankStr("Exa"),
    base_url=NotBlankStr("https://api.exa.ai/search"),
    auth_header=NotBlankStr("x-api-key"),
    health_body={"query": "ping"},
)


HTTP_VENDOR_PRESETS: Final[Mapping[HttpVendor, HttpVendorPreset]] = MappingProxyType(
    {
        HttpVendor.BRAVE: _BRAVE,
        HttpVendor.TAVILY: _TAVILY,
        HttpVendor.EXA: _EXA,
    }
)

# The enum and the registry are maintained separately, and a member added to
# one but not the other resolves to None: indistinguishable from the operator
# choosing a custom endpoint, so nothing would report it. Checked at import so
# it fails the build rather than the connection.
_UNREGISTERED = {v for v in HttpVendor if v is not HttpVendor.CUSTOM} - set(
    HTTP_VENDOR_PRESETS
)
if _UNREGISTERED:  # pragma: no cover -- import-time guard
    _NAMES = ", ".join(sorted(v.value for v in _UNREGISTERED))
    _MSG = f"HttpVendor members with no HTTP_VENDOR_PRESETS entry: {_NAMES}"
    raise RuntimeError(_MSG)

_MISKEYED = {
    vendor.value
    for vendor, preset in HTTP_VENDOR_PRESETS.items()
    if preset.id != vendor.value
}
if _MISKEYED:  # pragma: no cover -- import-time guard
    _MSG = f"HTTP_VENDOR_PRESETS entries whose id disagrees with their key: {_MISKEYED}"
    raise RuntimeError(_MSG)


def resolve_vendor(metadata: Mapping[str, str]) -> HttpVendorPreset | None:
    """Read the vendor preset from a connection's metadata.

    Args:
        metadata: The connection record's metadata mapping.

    Returns:
        The declared preset, or ``None`` when the connection is a custom
        endpoint, declares nothing, or names a vendor this build does not
        know. ``None`` means "use the operator's own base URL and generic
        auth", never a guessed vendor.
    """
    declared = metadata.get(METADATA_KEY_VENDOR, "")
    if not declared or declared == HttpVendor.CUSTOM.value:
        return None
    try:
        vendor = HttpVendor(declared)
    except ValueError:
        # Carry the value that failed: without it the operator is told only
        # that something was unrecognised, and a typo, a stale vendor and a
        # case mismatch all read identically.
        logger.warning(
            HTTP_VENDOR_METADATA_UNRECOGNISED,
            field=METADATA_KEY_VENDOR,
            declared=declared,
            resolved="none",
        )
        return None
    return HTTP_VENDOR_PRESETS[vendor]


__all__ = [
    "HTTP_VENDOR_PRESETS",
    "METADATA_KEY_VENDOR",
    "HttpVendor",
    "HttpVendorPreset",
    "resolve_vendor",
]
