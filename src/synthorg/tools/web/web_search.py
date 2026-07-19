"""Web search tool -- search the web via an abstracted provider.

The ``WebSearchProvider`` protocol defines a vendor-agnostic interface for
web search. A native ``HttpWebSearchProvider`` ships and is boot-wired by
default (Brave/Tavily/Exa presets); the protocol also admits a custom or
MCP-bridged provider injected at construction time.
"""

from typing import ClassVar, Final, Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict

from synthorg.core.boundary import parse_typed
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.web import (
    WEB_SEARCH_FAILED,
    WEB_SEARCH_START,
    WEB_SEARCH_SUCCESS,
)
from synthorg.security.autonomy.enums import ActionType
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

    title: NotBlankStr
    url: NotBlankStr
    snippet: str


# Vendor-agnostic public extension surface threaded through tools/factory.py:
# the native HTTP provider satisfies it, and a custom / MCP-bridged provider
# can be substituted without touching the tool.
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
            action_type=ActionType.EXTERNAL_DATA_REQUEST,
            network_policy=network_policy,
        )
        self._provider = provider

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Execute a web search.

        Args:
            arguments: Must contain ``query``; optionally ``max_results``.

        Returns:
            A ``ToolExecutionResult`` with formatted search results.
        """
        args = parse_typed("tool.execute", arguments, WebSearchArgs)
        query = args.query
        max_results = args.max_results

        logger.info(WEB_SEARCH_START, query=query, max_results=max_results)

        try:
            results = await self._provider.search(query, max_results)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
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

        validated = self._coerce_results(results, query, max_results)
        if not validated:
            logger.info(WEB_SEARCH_SUCCESS, query=query, result_count=0)
            return ToolExecutionResult(
                content="No results found.",
                metadata={"query": query, "result_count": 0},
            )

        logger.info(WEB_SEARCH_SUCCESS, query=query, result_count=len(validated))
        return ToolExecutionResult(
            content=self._format_lines(validated),
            metadata={"query": query, "result_count": len(validated)},
        )

    def _coerce_results(
        self,
        results: list[SearchResult] | None,
        query: str,
        max_results: int,
    ) -> list[SearchResult]:
        """Cap to ``max_results`` and coerce items to ``SearchResult``.

        A malformed item is dropped (scrubbed WARNING) rather than failing the
        whole search, so one bad row cannot blank an otherwise good page.

        Returns:
            The validated results, capped to ``max_results``.
        """
        if not results:
            # A custom / MCP-bridged provider may return ``None`` instead of an
            # empty list; treat both as "no results" rather than crashing on
            # ``list(None)``.
            return []
        validated: list[SearchResult] = []
        for item in list(results)[:max_results]:
            try:
                validated.append(
                    item
                    if isinstance(item, SearchResult)
                    else SearchResult.model_validate(item, from_attributes=True)
                )
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                # Drop exc_info + scrub -- the malformed provider result might
                # still be readable in frame-locals on the traceback.
                logger.warning(
                    WEB_SEARCH_FAILED,
                    query=query,
                    reason="malformed_provider_result",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                continue
        return validated

    @staticmethod
    def _format_lines(validated: list[SearchResult]) -> str:
        """Render results as the numbered ``title / URL / snippet`` block.

        Returns:
            The formatted multi-line result block.
        """
        lines: list[str] = []
        for i, r in enumerate(validated, 1):
            lines.append(f"{i}. {r.title}")
            lines.append(f"   URL: {r.url}")
            lines.append(f"   {r.snippet}")
            lines.append("")
        return "\n".join(lines).rstrip()
