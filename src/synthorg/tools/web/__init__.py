"""Built-in web tools for HTTP requests, search, page reading, and parsing.

``extract`` is deliberately absent: it pulls the extractor and its XML stack,
which every importer of this package would then pay for at cold import whether
or not it ever reads a page. The three fetch rungs that need it import it
directly.
"""

from synthorg.tools.web.base_web_tool import BaseWebTool
from synthorg.tools.web.config import WebToolsConfig
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
    "resolve_web_research_readiness",
]
