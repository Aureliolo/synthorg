# module-kind: declarative
"""Reader-request presets for the ``proxy`` fetch rung.

Same split as the search presets: the endpoint and the auth header belong to
the vendor (:mod:`synthorg.integrations.connections.http_vendor`), and only
the request and response *shape* lives here.

Every preset here reads its key from the SAME connection the search preset of
that vendor reads, so an operator who configured search has already configured
the reader; a vendor whose ``reader_url`` is empty simply has no entry.

Request and response shape per vendor:

* ollama: ``POST`` ``{url}``; response is the document itself, with
  ``title`` / ``content`` / ``links``.
* tavily: ``POST`` ``{urls: [...]}``; documents at ``results[]`` with
  ``raw_content`` and no title.
* exa: ``POST`` ``{urls: [...]}``; documents at ``results[]`` with
  ``title`` / ``text``.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, JsonValue, computed_field

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.http_vendor import (
    HTTP_VENDOR_PRESETS,
    HttpVendor,
    HttpVendorPreset,
)


class FetchProviderPreset(BaseModel):
    """Declarative description of one vendor's page-reader contract.

    Attributes:
        vendor: The shared vendor identity owning the endpoint and auth.
        url_key: Body key carrying the target URL.
        url_as_list: Whether that key takes a one-element array rather than a
            bare string, which the batch-shaped readers require.
        extra: Constant extra body fields merged into the request.
        results_path: JSON key path to the document, empty when the response
            root is the document itself.
        result_is_list: Whether ``results_path`` lands on an array whose first
            element is the document.
        title_key: Document key for the title, or ``None`` when the vendor
            returns none.
        content_key: Document key for the page content.
        links_key: Document key for outbound links, or ``None``.
        content_is_markdown: Whether the vendor already returns markdown. When
            false the content is HTML and goes through the same extractor the
            local rung uses, so every rung yields comparable output.
        capabilities: What this reader offers beyond plain content, surfaced
            so the caller can tell when the paid rung buys something.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    vendor: HttpVendorPreset
    url_key: NotBlankStr
    url_as_list: bool = False
    extra: dict[str, JsonValue] = Field(default_factory=dict)
    results_path: tuple[str, ...] = ()
    result_is_list: bool = False
    title_key: NotBlankStr | None = "title"
    content_key: NotBlankStr
    links_key: NotBlankStr | None = None
    content_is_markdown: bool = True
    capabilities: tuple[str, ...] = ()

    @computed_field
    @property
    def id(self) -> str:
        """Settings discriminator; the vendor id, never a second spelling."""
        return self.vendor.id

    @computed_field
    @property
    def endpoint(self) -> str:
        """Absolute HTTPS reader endpoint, owned by the vendor preset."""
        return self.vendor.reader_url

    def auth_headers(self, key: str) -> dict[str, str]:
        """Render the vendor's auth header for *key*.

        Returns:
            A single-entry header mapping. Carries a secret: never log it.
        """
        return self.vendor.auth_headers(key)


_OLLAMA: Final = FetchProviderPreset(
    vendor=HTTP_VENDOR_PRESETS[HttpVendor.OLLAMA],
    url_key="url",
    content_key="content",
    links_key="links",
    capabilities=("links",),
)

_TAVILY: Final = FetchProviderPreset(
    vendor=HTTP_VENDOR_PRESETS[HttpVendor.TAVILY],
    url_key="urls",
    url_as_list=True,
    results_path=("results",),
    result_is_list=True,
    title_key=None,
    content_key="raw_content",
    capabilities=("anti-bot fetching",),
)

_EXA: Final = FetchProviderPreset(
    vendor=HTTP_VENDOR_PRESETS[HttpVendor.EXA],
    url_key="urls",
    url_as_list=True,
    results_path=("results",),
    result_is_list=True,
    content_key="text",
    capabilities=("anti-bot fetching",),
)


FETCH_PROVIDER_PRESETS: Final[Mapping[str, FetchProviderPreset]] = MappingProxyType(
    {p.id: p for p in (_OLLAMA, _TAVILY, _EXA)}
)

FETCH_PROVIDER_IDS: Final[tuple[str, ...]] = tuple(FETCH_PROVIDER_PRESETS)

_READERLESS = {
    preset.id for preset in FETCH_PROVIDER_PRESETS.values() if not preset.endpoint
}
if _READERLESS:  # pragma: no cover -- import-time guard
    _NAMES = ", ".join(sorted(_READERLESS))
    _MSG = f"fetch presets whose vendor declares no reader_url: {_NAMES}"
    raise RuntimeError(_MSG)


def get_fetch_preset(provider_id: str) -> FetchProviderPreset | None:
    """Return the reader preset for ``provider_id``, or ``None`` when unknown.

    Args:
        provider_id: The vendor discriminator to resolve.

    Returns:
        A deep copy of the matching :class:`FetchProviderPreset`, or ``None``
        when that vendor ships no reader. Copying isolates the shared
        singleton, whose ``extra`` dict stays mutable under ``frozen=True``.
    """
    preset = FETCH_PROVIDER_PRESETS.get(provider_id)
    return preset.model_copy(deep=True) if preset is not None else None


__all__ = [
    "FETCH_PROVIDER_IDS",
    "FETCH_PROVIDER_PRESETS",
    "FetchProviderPreset",
    "get_fetch_preset",
]
