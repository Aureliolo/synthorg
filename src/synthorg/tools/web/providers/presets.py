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
* ollama: ``POST`` ``{query, max_results}``; results at ``results[]`` with
  ``title`` / ``url`` / ``content``.

Result filters are declared, never assumed. A provider names the keys it
implements and the tool reports any filter the selected provider cannot
express, because a recency filter that is silently dropped returns stale
results that look filtered.
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
FreshnessStyle = Literal["keyword", "iso_date"]

RECENCY_WINDOW_DAYS: Final[Mapping[str, int]] = MappingProxyType(
    {"day": 1, "week": 7, "month": 30, "year": 365}
)


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
        freshness_key: Param/body key carrying a recency window, or ``None``
            when this provider offers no date filter.
        freshness_style: How that key is spelled. ``keyword`` sends this
            provider's own token from ``freshness_values``; ``iso_date`` sends
            an absolute earliest-publication date, which the provider derives
            from the window because days-per-window is arithmetic rather than
            anything a vendor gets to define.
        freshness_values: This provider's token for each recency window. Only
            read under ``keyword`` style, since every keyword provider spells
            the same four windows differently.
        include_domains_key: Param/body key restricting results to a set of
            hostnames, or ``None`` when unsupported.
        exclude_domains_key: Param/body key dropping a set of hostnames, or
            ``None`` when unsupported.
        domains_as_csv: Whether the domain keys take a comma-joined string
            rather than a JSON array.
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
    freshness_key: NotBlankStr | None = None
    freshness_style: FreshnessStyle = "keyword"
    freshness_values: Mapping[str, str] = Field(default_factory=dict)
    include_domains_key: NotBlankStr | None = None
    exclude_domains_key: NotBlankStr | None = None
    domains_as_csv: bool = False

    @property
    def supports_recency(self) -> bool:
        """Whether this provider can filter by publication date."""
        if self.freshness_key is None:
            return False
        return self.freshness_style == "iso_date" or bool(self.freshness_values)

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
    freshness_key="freshness",
    freshness_values={"day": "pd", "week": "pw", "month": "pm", "year": "py"},
)

_TAVILY: Final = SearchProviderPreset(
    vendor=HTTP_VENDOR_PRESETS[HttpVendor.TAVILY],
    method="POST",
    query_key="query",
    count_key="max_results",
    max_results_cap=20,
    results_path=("results",),
    snippet_key="content",
    freshness_key="time_range",
    freshness_values={"day": "day", "week": "week", "month": "month", "year": "year"},
    include_domains_key="include_domains",
    exclude_domains_key="exclude_domains",
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
    freshness_key="startPublishedDate",
    freshness_style="iso_date",
    include_domains_key="includeDomains",
    exclude_domains_key="excludeDomains",
)

_OLLAMA: Final = SearchProviderPreset(
    vendor=HTTP_VENDOR_PRESETS[HttpVendor.OLLAMA],
    method="POST",
    query_key="query",
    count_key="max_results",
    # Ten, against twenty and a hundred for the others: this endpoint rejects
    # a larger count rather than clamping it, so the ceiling is load-bearing.
    max_results_cap=10,
    results_path=("results",),
    snippet_key="content",
)


SEARCH_PROVIDER_PRESETS: Final[Mapping[str, SearchProviderPreset]] = MappingProxyType(
    {p.id: p for p in (_BRAVE, _TAVILY, _EXA, _OLLAMA)}
)

SEARCH_PROVIDER_IDS: Final[tuple[str, ...]] = tuple(SEARCH_PROVIDER_PRESETS)


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
    "RECENCY_WINDOW_DAYS",
    "SEARCH_PROVIDER_IDS",
    "SEARCH_PROVIDER_PRESETS",
    "FreshnessStyle",
    "SearchMethod",
    "SearchProviderPreset",
    "get_search_preset",
]
