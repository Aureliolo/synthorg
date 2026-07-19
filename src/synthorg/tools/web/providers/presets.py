# module-kind: declarative
"""Vendor presets for the native HTTP web-search provider.

:class:`~synthorg.tools.web.providers.http_search_provider.HttpWebSearchProvider`
is vendor-agnostic; every provider-specific detail (endpoint, auth header,
request shape, response field names, result cap) lives here as data. Real
vendor names are confined to this declarative preset registry, the same
exemption the LLM provider presets take (:mod:`synthorg.providers.presets`).

Response shape per provider (the only thing that differs beyond auth):

* brave: ``GET`` ``?q=&count=``, key in ``X-Subscription-Token``; results at
  ``web.results[]`` with ``title`` / ``url`` / ``description``.
* tavily: ``POST`` ``{query, max_results}``, ``Authorization: Bearer``; results
  at ``results[]`` with ``title`` / ``url`` / ``content``.
* exa: ``POST`` ``{query, numResults, contents}``, key in ``x-api-key``;
  results at ``results[]`` with ``title`` / ``url`` / ``text``.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from synthorg.core.types import NotBlankStr

SearchMethod = Literal["GET", "POST"]


class SearchProviderPreset(BaseModel):
    """Declarative description of one search provider's REST contract.

    Attributes:
        id: Stable provider discriminator (the settings enum value).
        endpoint: Absolute HTTPS search endpoint.
        method: HTTP verb; ``GET`` carries params in the query string,
            ``POST`` carries them in a JSON body.
        auth_header: Header name the API key is sent under.
        auth_template: Value template for the auth header; ``{key}`` is
            substituted with the resolved API key (e.g. ``"Bearer {key}"``).
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

    id: NotBlankStr
    endpoint: NotBlankStr
    method: SearchMethod
    auth_header: NotBlankStr
    auth_template: NotBlankStr = "{key}"
    query_key: NotBlankStr
    count_key: NotBlankStr | None = None
    extra: dict[str, JsonValue] = Field(default_factory=dict)
    max_results_cap: int = Field(gt=0, le=100)
    results_path: tuple[str, ...] = ()
    title_key: NotBlankStr = "title"
    url_key: NotBlankStr = "url"
    snippet_key: NotBlankStr


_BRAVE: Final = SearchProviderPreset(
    id="brave",
    endpoint="https://api.search.brave.com/res/v1/web/search",
    method="GET",
    auth_header="X-Subscription-Token",
    query_key="q",
    count_key="count",
    max_results_cap=20,
    results_path=("web", "results"),
    snippet_key="description",
)

_TAVILY: Final = SearchProviderPreset(
    id="tavily",
    endpoint="https://api.tavily.com/search",
    method="POST",
    auth_header="Authorization",
    auth_template="Bearer {key}",
    query_key="query",
    count_key="max_results",
    max_results_cap=20,
    results_path=("results",),
    snippet_key="content",
)

_EXA: Final = SearchProviderPreset(
    id="exa",
    endpoint="https://api.exa.ai/search",
    method="POST",
    auth_header="x-api-key",
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
