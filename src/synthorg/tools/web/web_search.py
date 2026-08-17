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


class SearchFilters(BaseModel):
    """Result restrictions requested by the caller, in neutral terms.

    Spelled once here rather than in each provider's vocabulary: a recency
    window means the same thing to every index and is named differently by
    all of them, so translation is the provider's job.

    Attributes:
        recency: Only results published within this window.
        include_domains: Only results from these hostnames.
        exclude_domains: No results from these hostnames.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    recency: str | None = None
    include_domains: tuple[str, ...] = ()
    exclude_domains: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Whether nothing was actually requested."""
        return (
            self.recency is None
            and not self.include_domains
            and not self.exclude_domains
        )


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
        filters: SearchFilters | None = None,
    ) -> list[SearchResult]:
        """Execute a web search query.

        Args:
            query: Search query string.
            max_results: Maximum number of results to return.
            filters: Recency / domain restrictions, or ``None`` for an
                unfiltered search. An implementation that cannot express a
                requested filter reports it rather than dropping it.

        Returns:
            List of search results.
        """
        ...

    def unsupported_filters(self, filters: SearchFilters | None) -> tuple[str, ...]:
        """Name the requested filters this implementation will not apply.

        Required rather than optional: an implementation that quietly ignores
        a recency filter returns unfiltered results the caller believes were
        filtered, and the caller cannot tell the difference from the results
        alone. Returning ``()`` is a claim that everything was applied.

        Returns:
            The filter names that were not applied.
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
                "Search the web and get back titles, URLs and snippets. Reach "
                "for this whenever the answer depends on the world outside "
                "this workspace and outside your training data: the current "
                "API of a library, whether an approach is still recommended, "
                "what a recent version changed, an error message you do not "
                "recognise, a standard or specification. Your priors on fast-"
                "moving libraries are older than the code you are asked to "
                "write, and confidently wrong about them is the expensive "
                "failure. Use `recency` and `include_domains` to pin results "
                "to current material and to official documentation, then read "
                "the page itself with web_fetch rather than trusting a snippet."
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
            arguments: Must contain ``query``; optionally ``max_results``,
                ``recency``, ``include_domains``, ``exclude_domains``.

        Returns:
            A ``ToolExecutionResult`` with formatted search results.
        """
        args = parse_typed("tool.execute", arguments, WebSearchArgs)
        query = args.query
        max_results = args.max_results
        filters = SearchFilters(
            recency=args.recency,
            include_domains=tuple(args.include_domains),
            exclude_domains=tuple(args.exclude_domains),
        )

        logger.info(WEB_SEARCH_START, query=query, max_results=max_results)

        try:
            results = await self._provider.search(query, max_results, filters)
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

        unsupported = tuple(self._provider.unsupported_filters(filters))
        notice = self._filter_notice(unsupported)
        validated = self._coerce_results(results, query, max_results)
        if not validated:
            logger.info(WEB_SEARCH_SUCCESS, query=query, result_count=0)
            return ToolExecutionResult(
                content=f"No results found.{notice}",
                metadata={
                    "query": query,
                    "result_count": 0,
                    "unsupported_filters": list(unsupported),
                },
            )

        logger.info(
            WEB_SEARCH_SUCCESS,
            query=query,
            result_count=len(validated),
            unsupported_filters=list(unsupported),
        )
        return ToolExecutionResult(
            content=f"{self._format_lines(validated)}{notice}",
            metadata={
                "query": query,
                "result_count": len(validated),
                "unsupported_filters": list(unsupported),
            },
        )

    @staticmethod
    def _filter_notice(unsupported: tuple[str, ...]) -> str:
        """Render the warning naming filters that were not applied.

        Returns:
            A trailing notice, or an empty string when everything applied.
        """
        if not unsupported:
            return ""
        joined = ", ".join(unsupported)
        return (
            f"\n\n[Not applied by the configured search provider: {joined}. "
            "These results are NOT filtered by it; check dates and sources "
            "yourself, or narrow the query text instead.]"
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
