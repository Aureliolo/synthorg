"""Web search tool -- search the web via an abstracted provider.

The ``WebSearchProvider`` protocol defines a vendor-agnostic interface
for web search.  No concrete implementation is shipped -- users inject
a provider at construction time (e.g. via MCP bridge or a custom
implementation).
"""

from typing import ClassVar, Final, Protocol, cast, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, JsonValue

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import ActionType
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.web import (
    WEB_SEARCH_FAILED,
    WEB_SEARCH_START,
    WEB_SEARCH_SUCCESS,
)
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.network_validator import NetworkPolicy
from synthorg.tools.web._args import WebSearchArgs
from synthorg.tools.web.base_web_tool import BaseWebTool

logger = get_logger(__name__)

_DEFAULT_MAX_RESULTS: Final[int] = 10


class SearchResult(BaseModel):
    """A single web search result.

    Attributes:
        title: Result title.
        url: Result URL.
        snippet: Text snippet from the result page.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    title: str
    url: str
    snippet: str


# Vendor-agnostic public extension surface; tools/factory.py threads it
# through 4 callsites; intentional no-built-in-impl design
# (MCP / user-supplied).
@runtime_checkable
class WebSearchProvider(Protocol):
    """Abstracted web search provider protocol.

    Implementations must be async and return a list of
    ``SearchResult`` objects.
    """

    async def search(
        self,
        query: str,
        max_results: int = _DEFAULT_MAX_RESULTS,
    ) -> list[SearchResult]:
        """Execute a web search query.

        Args:
            query: Search query string.
            max_results: Maximum number of results to return.

        Returns:
            List of search results.
        """
        ...


class WebSearchTool(BaseWebTool):
    """Search the web using an injected provider.

    The search provider is vendor-agnostic -- any implementation
    satisfying the ``WebSearchProvider`` protocol can be used.

    Examples:
        Search with a custom provider::

            tool = WebSearchTool(provider=my_search_provider)
            result = await tool.execute(arguments={"query": "Python async patterns"})
    """

    args_model: ClassVar[type[BaseModel] | None] = WebSearchArgs

    def __init__(
        self,
        *,
        provider: WebSearchProvider,
        network_policy: NetworkPolicy | None = None,
    ) -> None:
        """Initialize the web search tool.

        Args:
            provider: Web search backend.
            network_policy: SSRF + scheme allowlist applied to the
                provider's outgoing requests. ``None`` uses the
                default conservative policy.
        """
        super().__init__(
            name="web_search",
            description=(
                "Search the web for information. Returns titles, "
                "URLs, and snippets for matching results."
            ),
            parameters_schema=WebSearchArgs.model_json_schema(),
            action_type=ActionType.COMMS_EXTERNAL,
            network_policy=network_policy,
        )
        self._provider = provider

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, JsonValue],
    ) -> ToolExecutionResult:
        """Execute a web search.

        Args:
            arguments: Must contain ``query``; optionally ``max_results``.

        Returns:
            A ``ToolExecutionResult`` with formatted search results.
        """
        query = cast("str", arguments["query"])
        max_results = cast("int", arguments.get("max_results", 10))

        logger.info(WEB_SEARCH_START, query=query, max_results=max_results)

        try:
            results = await self._provider.search(query, max_results)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                WEB_SEARCH_FAILED,
                query=query,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            # Stable, generic message: ``ToolExecutionResult.content``
            # is forwarded to the LLM, so interpolating ``exc`` would
            # leak provider internals / API keys past the log scrub.
            return ToolExecutionResult(
                content="Web search failed. Please try again.",
                is_error=True,
            )

        if not results:
            logger.info(WEB_SEARCH_SUCCESS, query=query, result_count=0)
            return ToolExecutionResult(
                content="No results found.",
                metadata={"query": query, "result_count": 0},
            )

        # Cap to requested max and coerce to SearchResult.
        validated: list[SearchResult] = []
        for item in list(results)[:max_results]:
            try:
                validated.append(
                    item
                    if isinstance(item, SearchResult)
                    else SearchResult.model_validate(item, from_attributes=True)
                )
            except Exception as exc:
                reraise_critical(exc)
                # Drop exc_info + scrub -- the malformed provider
                # result might still be readable in frame-locals on
                # the traceback.
                logger.warning(
                    WEB_SEARCH_FAILED,
                    query=query,
                    reason="malformed_provider_result",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                continue

        if not validated:
            logger.info(WEB_SEARCH_SUCCESS, query=query, result_count=0)
            return ToolExecutionResult(
                content="No results found.",
                metadata={"query": query, "result_count": 0},
            )

        lines: list[str] = []
        for i, r in enumerate(validated, 1):
            lines.append(f"{i}. {r.title}")
            lines.append(f"   URL: {r.url}")
            lines.append(f"   {r.snippet}")
            lines.append("")

        logger.info(
            WEB_SEARCH_SUCCESS,
            query=query,
            result_count=len(validated),
        )

        return ToolExecutionResult(
            content="\n".join(lines).rstrip(),
            metadata={"query": query, "result_count": len(validated)},
        )
