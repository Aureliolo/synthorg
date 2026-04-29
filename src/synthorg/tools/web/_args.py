"""Typed argument models for web tools.

One frozen Pydantic model per web tool.  SSRF / network policy checks
stay inside the tool body because they depend on per-instance
``NetworkPolicy`` configuration; the args models enforce only the
static shape (URLs are non-blank, HTTP methods are from a closed set,
timeout bounds match the wire schema).

Tools wired to consume these models:

* :class:`~synthorg.tools.web.web_search.WebSearchTool` -> :class:`WebSearchArgs`
* :class:`~synthorg.tools.web.http_request.HttpRequestTool` -> :class:`HttpRequestArgs`
* :class:`~synthorg.tools.web.html_parser.HtmlParserTool` -> :class:`HtmlParserArgs`
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type

_ARGS_CONFIG = ConfigDict(
    frozen=True,
    allow_inf_nan=False,
    extra="forbid",
)


# ``HttpMethod`` is closed: the existing tool body rejects anything
# outside ``_ALLOWED_METHODS`` after consuming the arg, so promoting
# the gate to a typed Literal eliminates the runtime check entirely.
HttpMethod = Literal["GET", "POST", "PUT", "DELETE"]


# ``HtmlExtractMode`` mirrors the closed set the parser dispatches on.
HtmlExtractMode = Literal["text", "links", "metadata"]


class WebSearchArgs(BaseModel):
    """Args for ``web_search``."""

    model_config = _ARGS_CONFIG

    query: NotBlankStr = Field(description="Search query string")
    max_results: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum results to return",
    )


class HttpRequestArgs(BaseModel):
    """Args for ``http_request``.

    The HTTP method is restricted to the closed set the tool dispatches
    on; the previous ``_ALLOWED_METHODS`` runtime check becomes a
    Pydantic ``Literal`` validation.  ``timeout`` is left optional so
    callers can fall back to the per-tool default; the bounds match the
    JSON-Schema cap (``0-300`` seconds).
    """

    model_config = _ARGS_CONFIG

    url: NotBlankStr = Field(description="The URL to request")
    method: HttpMethod = Field(default="GET", description="HTTP method")
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Optional request headers",
    )
    body: str | None = Field(
        default=None,
        description="Optional request body (for POST/PUT)",
    )
    timeout: float | None = Field(
        default=None,
        ge=0,
        le=300,
        description="Request timeout in seconds (defaults to per-tool config)",
    )


class HtmlParserArgs(BaseModel):
    """Args for ``html_parser``.

    Operates on a pre-fetched HTML string -- no network policy check
    needed.  ``extract_mode`` is a closed Literal mirroring the
    parser's dispatch.
    """

    model_config = _ARGS_CONFIG

    html_content: str = Field(description="HTML content to parse")
    extract_mode: HtmlExtractMode = Field(
        default="text",
        description="What to extract: text, links, or metadata",
    )


__all__ = [
    "HtmlExtractMode",
    "HtmlParserArgs",
    "HttpMethod",
    "HttpRequestArgs",
    "WebSearchArgs",
]
