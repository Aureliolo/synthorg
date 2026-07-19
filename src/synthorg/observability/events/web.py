"""Event constants for web tool operations."""

from typing import Final

WEB_REQUEST_START: Final[str] = "web.request.start"
WEB_REQUEST_SUCCESS: Final[str] = "web.request.success"
WEB_REQUEST_FAILED: Final[str] = "web.request.failed"
WEB_REQUEST_TIMEOUT: Final[str] = "web.request.timeout"
WEB_SSRF_BLOCKED: Final[str] = "web.ssrf.blocked"
WEB_SSRF_DISABLED: Final[str] = "web.ssrf.disabled"
WEB_DNS_FAILED: Final[str] = "web.dns.failed"
WEB_SEARCH_START: Final[str] = "web.search.start"
WEB_SEARCH_SUCCESS: Final[str] = "web.search.success"
WEB_SEARCH_FAILED: Final[str] = "web.search.failed"
WEB_SEARCH_RETRY: Final[str] = "web.search.retry"
WEB_SEARCH_PROVIDER_BOUND: Final[str] = "web.search.provider_bound"
WEB_PARSE_START: Final[str] = "web.parse.start"
WEB_PARSE_SUCCESS: Final[str] = "web.parse.success"
WEB_PARSE_FAILED: Final[str] = "web.parse.failed"
