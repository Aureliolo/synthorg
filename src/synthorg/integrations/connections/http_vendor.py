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


class ProbeVerdict(StrEnum):
    """What a health probe established about the credential.

    Kept separate from the connection's health status because a probe that
    could not determine anything is not the same as one that found a fault,
    and folding the two would report every vendor whose error contract is
    unverified as broken.
    """

    AUTH_OK = "auth_ok"
    AUTH_FAILED = "auth_failed"
    INDETERMINATE = "indeterminate"


class HttpVendor(StrEnum):
    """A generic-HTTP service with a code-defined request contract."""

    BRAVE = "brave"
    TAVILY = "tavily"
    EXA = "exa"
    OLLAMA = "ollama"
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
        health_url: Absolute URL to probe instead of ``base_url``. Set only
            for a vendor with a genuinely free metadata endpoint, where a
            ``2xx`` proves the credential without buying anything.
        reader_url: Absolute URL of this vendor's page-reader endpoint, which
            takes a target URL and returns that page's content. Empty when the
            vendor sells search but no reader, which is what makes it absent
            from the fetch ladder rather than present and always failing.
        auth_cleared_statuses: Error statuses this vendor is *known* to
            return once the credential has been accepted and only the
            request shape was rejected. Empty unless verified against the
            live API, because treating a guessed status as proof would
            report a revoked key as healthy.
        auth_failure_markers: Case-insensitive substrings that appear in
            this vendor's error body when the CREDENTIAL was rejected
            rather than the request. Checked before
            ``auth_cleared_statuses``, since both arrive as the same status.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr
    label: NotBlankStr
    base_url: NotBlankStr
    auth_header: NotBlankStr
    auth_template: NotBlankStr = "{key}"
    health_url: str = ""
    reader_url: str = ""
    auth_cleared_statuses: frozenset[int] = Field(default_factory=frozenset)
    auth_failure_markers: tuple[str, ...] = ()

    def probe_verdict(self, status: int, body: str) -> ProbeVerdict:
        """Judge a probe response without having bought anything.

        A metered API bills successful calls, so the probe deliberately sends
        a request the endpoint must reject. The rejection is the evidence: it
        proves the request reached the handler with the credential accepted,
        and the vendor does not charge for an error.

        Returns:
            ``AUTH_OK`` when the credential demonstrably cleared,
            ``AUTH_FAILED`` when it was demonstrably rejected, and
            ``INDETERMINATE`` when this vendor's contract does not say --
            which is reported as unknown rather than guessed either way.
        """
        lowered = body.lower()
        if any(marker.lower() in lowered for marker in self.auth_failure_markers):
            return ProbeVerdict.AUTH_FAILED
        if status in self.auth_cleared_statuses:
            return ProbeVerdict.AUTH_OK
        return ProbeVerdict.INDETERMINATE

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
    # The probe sends NO query. Brave documents that "only successful
    # requests (non-error responses) are counted against your quota and
    # billed", and a search with no ``q`` is a 422, so this costs nothing.
    # Verified against the live API: a valid token yields
    # ``VALIDATION`` naming ``["query", "q"]`` (the request was rejected,
    # the credential was not), a wrong token yields
    # ``SUBSCRIPTION_TOKEN_INVALID``, and a missing one names the header.
    # The two failures are the markers below; the 422 that remains is proof
    # the credential cleared.
    auth_cleared_statuses=frozenset({422}),
    auth_failure_markers=("SUBSCRIPTION_TOKEN_INVALID", "x-subscription-token"),
)

_TAVILY: Final = HttpVendorPreset(
    id=NotBlankStr(HttpVendor.TAVILY.value),
    label=NotBlankStr("Tavily"),
    base_url=NotBlankStr("https://api.tavily.com/search"),
    auth_header=NotBlankStr("Authorization"),
    auth_template=NotBlankStr("Bearer {key}"),
    # Tavily publishes a metadata endpoint reporting key and plan usage, so
    # the credential can be proven by reading rather than by searching. A
    # 2xx here is the whole verdict, which is why no error contract is
    # declared below.
    health_url="https://api.tavily.com/usage",
    reader_url="https://api.tavily.com/extract",
)

_EXA: Final = HttpVendorPreset(
    id=NotBlankStr(HttpVendor.EXA.value),
    label=NotBlankStr("Exa"),
    base_url=NotBlankStr("https://api.exa.ai/search"),
    auth_header=NotBlankStr("x-api-key"),
    # The probe sends no query, which this API rejects as a malformed body
    # rather than running a search, so it buys nothing. Observed against the
    # live endpoint: a rejected key answers 401 tagged ``INVALID_API_KEY``,
    # and a request carrying no key at all answers 402 tagged
    # ``X402_PAYMENT_REQUIRED``, not the 401 the published table describes.
    # Both are credential failures and neither is a 400, which is what makes
    # the documented request-shape rejection safe to read as proof the
    # credential cleared. The other 402 tags (exhausted credits, exceeded
    # budget) are deliberately absent: they name a key that WAS accepted, and
    # calling them auth failures would blame the credential for an empty
    # wallet.
    auth_cleared_statuses=frozenset({400}),
    auth_failure_markers=("INVALID_API_KEY", "X402_PAYMENT_REQUIRED"),
    reader_url="https://api.exa.ai/contents",
)

_OLLAMA: Final = HttpVendorPreset(
    id=NotBlankStr(HttpVendor.OLLAMA.value),
    label=NotBlankStr("Ollama"),
    base_url=NotBlankStr("https://ollama.com/api/web_search"),
    auth_header=NotBlankStr("Authorization"),
    auth_template=NotBlankStr("Bearer {key}"),
    # Observed against the live endpoint: both a rejected key and no key at
    # all answer 401 ``{"error":"Unauthorized"}``, which is the marker below.
    # ``auth_cleared_statuses`` stays empty on purpose. This vendor publishes
    # no error contract, and what a request WITH a valid key but no query
    # answers cannot be observed without holding one; naming a status anyway
    # is the guess that reports a revoked key as healthy. Empty costs a
    # working key an indeterminate badge, which is the survivable half.
    auth_failure_markers=("Unauthorized",),
    reader_url="https://ollama.com/api/web_fetch",
)


HTTP_VENDOR_PRESETS: Final[Mapping[HttpVendor, HttpVendorPreset]] = MappingProxyType(
    {
        HttpVendor.BRAVE: _BRAVE,
        HttpVendor.TAVILY: _TAVILY,
        HttpVendor.EXA: _EXA,
        HttpVendor.OLLAMA: _OLLAMA,
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
