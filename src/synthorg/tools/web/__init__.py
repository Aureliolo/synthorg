"""Built-in web tools for HTTP requests, search, page reading, and parsing."""

from synthorg.tools.web.base_web_tool import BaseWebTool
from synthorg.tools.web.config import WebToolsConfig
from synthorg.tools.web.extract import ExtractedDocument, extract_markdown
from synthorg.tools.web.html_parser import HtmlParserTool
from synthorg.tools.web.http_request import HttpRequestTool
from synthorg.tools.web.readiness import (
    WebResearchReadiness,
    WebSearchBlocker,
    resolve_web_research_readiness,
)
from synthorg.tools.web.web_fetch import (
    FetchBackend,
    FetchedPage,
    WebFetchProvider,
    WebFetchRungs,
    WebFetchTool,
    WebToolsWiring,
)
from synthorg.tools.web.web_search import (
    SearchFilters,
    SearchResult,
    WebSearchProvider,
    WebSearchTool,
)

__all__ = [
    "BaseWebTool",
    "ExtractedDocument",
    "FetchBackend",
    "FetchedPage",
    "HtmlParserTool",
    "HttpRequestTool",
    "SearchFilters",
    "SearchResult",
    "WebFetchProvider",
    "WebFetchRungs",
    "WebFetchTool",
    "WebResearchReadiness",
    "WebSearchBlocker",
    "WebSearchProvider",
    "WebSearchTool",
    "WebToolsConfig",
    "WebToolsWiring",
    "extract_markdown",
    "resolve_web_research_readiness",
]
