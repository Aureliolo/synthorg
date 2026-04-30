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

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type

_ARGS_CONFIG = ConfigDict(
    frozen=True,
    allow_inf_nan=False,
    extra="forbid",
)

# ASCII control-character bounds for HTTP header validation.  Anything
# below 0x20 (SP) or equal to 0x7F (DEL) is a control char that must
# not appear in HTTP header names or values per RFC 7230 § 3.2.6.
_ASCII_PRINTABLE_MIN = 0x20
_ASCII_DEL = 0x7F

# RFC 7230 § 3.2.6 ``token`` charset for header-field-name validation.
# A header name is one or more ``tchar``.  Names containing whitespace,
# colon, separators, or non-ASCII characters are rejected.  An empty
# string also fails.
_RFC_TOKEN_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


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
        description="Optional request headers (no CR/LF or other ASCII control chars)",
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

    @field_validator("headers", mode="after")
    @classmethod
    def _validate_headers(cls, value: dict[str, str]) -> dict[str, str]:
        r"""Validate header shape at the typed boundary.

        Two checks run here:

        1. **Names are RFC 7230 ``tchar`` tokens.**  Empty strings and
           names containing whitespace, ``:``, separators, or non-ASCII
           characters are rejected.  Without this guard, payloads like
           ``{"": "x"}`` or ``{"bad header": "x"}`` would only fail
           inside the HTTP client.
        2. **No ASCII control characters** in either names or values.
           Blocks header smuggling / response-splitting attempts
           (CR/LF/NUL/DEL).
        """
        for name, val in value.items():
            if not _RFC_TOKEN_RE.match(name):
                # Don't echo the rejected name -- this validation runs
                # at the typed boundary and the message body lands in
                # logs / ``invalid_argument`` envelopes.  Pydantic
                # already includes the offending field path in its
                # error chain; the message just needs to explain the
                # rule.
                msg = (
                    "header name is not a valid RFC 7230 ``token``; "
                    "names must be one or more ``tchar`` characters "
                    "and contain no whitespace, ``:``, separators, or "
                    "non-ASCII characters"
                )
                raise ValueError(msg)
            for label, candidate in (("name", name), ("value", val)):
                if any(
                    ord(ch) < _ASCII_PRINTABLE_MIN or ord(ch) == _ASCII_DEL
                    for ch in candidate
                ):
                    msg = (
                        f"header {label} contains an ASCII control "
                        f"character (CR/LF/NUL etc.); reject to prevent "
                        f"header smuggling"
                    )
                    raise ValueError(msg)
        return value


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
