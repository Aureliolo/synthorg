# module-kind: declarative
"""Search-request presets for the native HTTP web-search provider.

:class:`~synthorg.tools.web.providers.http_search_provider.HttpWebSearchProvider`
is vendor-agnostic; every provider-specific detail (request shape, response
field names, result cap) lives here as data.

The endpoint and auth header are NOT restated here: they are the same facts a
connection needs, and they live once in
:mod:`synthorg.integrations.connections.http_vendor`. Two copies would let a
search call and its connection's health probe disagree about where the service
is or how it authenticates, which is the drift that makes a working key read
as unhealthy.

Response shape per provider (the only thing that differs beyond auth):

* brave: ``GET`` ``?q=&count=``; results at ``web.results[]`` with ``title`` /
  ``url`` / ``description``.
* tavily: ``POST`` ``{query, max_results}``; results at ``results[]`` with
  ``title`` / ``url`` / ``content``.
* exa: ``POST`` ``{query, numResults, contents}``; results at ``results[]``
  with ``title`` / ``url`` / ``text``.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, computed_field

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.http_vendor import (
    HTTP_VENDOR_PRESETS,
    HttpVendor,
    HttpVendorPreset,
)

SearchMethod = Literal["GET", "POST"]


class SearchProviderPreset(BaseModel):
    """Declarative description of one search provider's REST contract.

    Attributes:
        vendor: The shared vendor identity, which owns the endpoint and the
            auth header. The settings enum value is this vendor's id.
        method: HTTP verb; ``GET`` carries params in the query string,
            ``POST`` carries them in a JSON body.
        query_key: Param/body key carrying the search query string.
        count_key: Param/body key carrying the result count, or ``None`` if
            the provider takes no count parameter.
        extra: Constant extra body fields merged into a ``POST`` request
            (ignored for ``GET``).
        max_results_cap: Provider-enforced ceiling; the requested count is
            clamped to this to avoid an upstream 4xx.
        results_path: JSON key path from the response root to the results
            array (empty means the root itself is the array).
        title_key: Result-object key for the title.
        url_key: Result-object key for the URL.
        snippet_key: Result-object key for the text snippet.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    vendor: HttpVendorPreset
    method: SearchMethod
    query_key: NotBlankStr
    count_key: NotBlankStr | None = None
    extra: dict[str, JsonValue] = Field(default_factory=dict)
    max_results_cap: int = Field(gt=0, le=100)
    results_path: tuple[str, ...] = ()
    title_key: NotBlankStr = "title"
    url_key: NotBlankStr = "url"
    snippet_key: NotBlankStr

    @computed_field
    @property
    def id(self) -> str:
        """Settings discriminator; the vendor id, never a second spelling."""
        return self.vendor.id

    @computed_field
    @property
    def endpoint(self) -> str:
        """Absolute HTTPS search endpoint, owned by the vendor preset."""
        return self.vendor.base_url

    def auth_headers(self, key: str) -> dict[str, str]:
        """Render the vendor's auth header for *key*.

        Returns:
            A single-entry header mapping. Carries a secret: never log it.
        """
        return self.vendor.auth_headers(key)


_BRAVE: Final = SearchProviderPreset(
    vendor=HTTP_VENDOR_PRESETS[HttpVendor.BRAVE],
    method="GET",
    query_key="q",
    count_key="count",
    max_results_cap=20,
    results_path=("web", "results"),
    snippet_key="description",
)

_TAVILY: Final = SearchProviderPreset(
    vendor=HTTP_VENDOR_PRESETS[HttpVendor.TAVILY],
    method="POST",
    query_key="query",
    count_key="max_results",
    max_results_cap=20,
    results_path=("results",),
    snippet_key="content",
)

_EXA: Final = SearchProviderPreset(
    vendor=HTTP_VENDOR_PRESETS[HttpVendor.EXA],
    method="POST",
    query_key="query",
    count_key="numResults",
    extra={"type": "auto", "contents": {"text": {"maxCharacters": 800}}},
    max_results_cap=100,
    results_path=("results",),
    snippet_key="text",
)


SEARCH_PROVIDER_PRESETS: Final[Mapping[str, SearchProviderPreset]] = MappingProxyType(
    {p.id: p for p in (_BRAVE, _TAVILY, _EXA)}
)

# Ordered provider ids; first entry is the recommended default.
SEARCH_PROVIDER_IDS: Final[tuple[str, ...]] = tuple(SEARCH_PROVIDER_PRESETS)
DEFAULT_SEARCH_PROVIDER_ID: Final[str] = _BRAVE.id


def get_search_preset(provider_id: str) -> SearchProviderPreset | None:
    """Return the preset for ``provider_id``, or ``None`` when unknown.

    Args:
        provider_id: The provider discriminator to resolve.

    Returns:
        A deep copy of the matching :class:`SearchProviderPreset`, or ``None``
        if no preset is registered under ``provider_id``. Copying isolates the
        shared singleton: ``frozen=True`` blocks field reassignment but not
        in-place mutation of the ``extra`` dict, so returning the registry
        object directly would let one caller corrupt it for every other.
    """
    preset = SEARCH_PROVIDER_PRESETS.get(provider_id)
    return preset.model_copy(deep=True) if preset is not None else None


__all__ = [
    "DEFAULT_SEARCH_PROVIDER_ID",
    "SEARCH_PROVIDER_IDS",
    "SEARCH_PROVIDER_PRESETS",
    "SearchMethod",
    "SearchProviderPreset",
    "get_search_preset",
]
