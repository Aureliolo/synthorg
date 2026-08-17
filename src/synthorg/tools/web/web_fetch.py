# module-kind: code
"""Read one web page as markdown, over an operator-configured set of rungs.

``web_fetch`` exists because reading a documentation page through
``http_request`` + ``html_parser`` returns the whole DOM as flat text: the
navigation and cookie chrome survive and the fenced code blocks do not, which
inverts the value of every token spent.

Three rungs answer the same question at different cost, and the caller names
the one it wants:

* ``local`` -- fetch here, under the same SSRF and byte ceiling
  ``http_request`` uses, then extract. Needs nothing configured.
* ``proxy`` -- hand the URL to the bound search vendor's own reader.
* ``render`` -- drive the headless browser first, for pages that build their
  body in JavaScript.

Which rungs EXIST is the operator's decision (a rung is offered only once its
backing is configured); which available rung serves THIS call is the agent's.
Nothing escalates by itself: a rung that comes back empty says so, names
itself, and lists the rungs left, so the next attempt is a call the agent
chose and the transcript records which backend produced which bytes.
"""

from enum import StrEnum
from typing import ClassVar, Final, Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.boundary import parse_typed
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.web import (
    WEB_FETCH_BACKEND_UNAVAILABLE,
    WEB_FETCH_EMPTY,
    WEB_FETCH_FAILED,
    WEB_FETCH_START,
    WEB_FETCH_SUCCESS,
)
from synthorg.providers.url_utils import redact_url
from synthorg.security.autonomy.enums import ActionType
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.network_validator import NetworkPolicy
from synthorg.tools.web._args import WebFetchArgs
from synthorg.tools.web.base_web_tool import BaseWebTool
from synthorg.tools.web.llms_txt import (
    INDEX_PROBE_TTL_SECONDS,
    IndexProbeCache,
    discover_llms_txt,
    discovery_notice,
)
from synthorg.tools.web.web_search import WebSearchProvider

logger = get_logger(__name__)

_DEFAULT_PROBE_TIMEOUT: Final[float] = 5.0


class FetchBackend(StrEnum):
    """Which rung served, or is being asked to serve, a fetch."""

    LOCAL = "local"
    PROXY = "proxy"
    RENDER = "render"


class FetchedPage(BaseModel):
    """One page read as markdown.

    Attributes:
        url: The URL as requested.
        final_url: Where the read actually landed, when the backend reports it.
        title: Page title, empty when the page declares none.
        markdown: Extracted content; empty when nothing readable survived.
        backend: The rung that produced this.
        truncated: Whether the content was cut to fit the character budget.
        links: Outbound links, only when the backend returns them.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    url: NotBlankStr
    final_url: str = ""
    title: str = ""
    markdown: str
    backend: FetchBackend
    truncated: bool = False
    links: tuple[str, ...] = ()


@runtime_checkable
class WebFetchProvider(Protocol):
    """One rung of the fetch ladder."""

    @property
    def backend(self) -> FetchBackend:
        """Which rung this provider is."""
        ...

    @property
    def capabilities(self) -> tuple[str, ...]:
        """What this rung offers beyond markdown, for the agent to weigh."""
        ...

    async def fetch(self, url: str) -> FetchedPage:
        """Read *url* and return it as markdown."""
        ...


class FetchBudget(BaseModel):
    """How much of a response a rung accepts.

    The two ceilings travel together: bytes bound what is read off the wire,
    characters bound what reaches the agent, and every rung needs both. They
    are one argument because they are one decision, and because a rung
    configured with one and not the other is not a state an operator can
    express.

    Attributes:
        max_response_bytes: Hard ceiling on the body read from the wire.
        char_budget: Ceiling on the markdown handed back.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    max_response_bytes: int = Field(gt=0)
    char_budget: int = Field(gt=0)


@runtime_checkable
class RenderedPageSource(Protocol):
    """The slice of the browser tool the render rung drives.

    Declared beside the wiring that carries it rather than beside the provider
    that consumes it: the provider imports this module, so a field typed from
    there would close an import cycle and have to fall back to ``object``,
    which is a field that accepts anything and decides at runtime.
    """

    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Run one browser operation."""
        ...


class WebFetchRungs(BaseModel):
    """The ladder resolved from settings, plus what the tool needs to build it.

    The render rung is declared here but completed in the tool factory, which
    is the first place the browser tool exists; boot has the settings but not
    the sandbox.

    Attributes:
        providers: The rungs already built, keyed by backend.
        discover_docs_index: Whether a fetch also probes for ``llms.txt``.
        render_enabled: Whether the operator asked for the rendered rung.
        char_budget: Markdown ceiling, needed to finish the render rung.
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    providers: dict[FetchBackend, WebFetchProvider]
    discover_docs_index: bool = True
    render_enabled: bool = False
    char_budget: int = Field(gt=0)


class WebToolsWiring(BaseModel):
    """Everything the tool factory needs to build the web cohort.

    Grouped because these five travel together through every layer of the
    factory and passing them individually pushed the cohort builder over the
    argument cap.

    Attributes:
        network_policy: SSRF policy shared by every web tool.
        request_timeout: Per-request timeout for the plain HTTP tool.
        search_provider: The bound search backend, or ``None`` when unset.
        fetch_rungs: The resolved fetch ladder, or ``None`` when off.
        render_source: The browser tool backing the rendered rung, or ``None``.
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    network_policy: NetworkPolicy | None = None
    request_timeout: float = Field(gt=0)
    search_provider: WebSearchProvider | None = None
    fetch_rungs: WebFetchRungs | None = None
    render_source: RenderedPageSource | None = None


class WebFetchTool(BaseWebTool):
    """Read a URL as markdown over the configured rungs."""

    args_model: ClassVar[type[BaseModel] | None] = WebFetchArgs

    def __init__(
        self,
        *,
        providers: dict[FetchBackend, WebFetchProvider],
        network_policy: NetworkPolicy | None = None,
        discover_docs_index: bool = True,
        probe_timeout_seconds: float = _DEFAULT_PROBE_TIMEOUT,
        clock: Clock | None = None,
    ) -> None:
        """Wire the tool to every rung the operator configured.

        Args:
            providers: The available rungs, keyed by backend. Must be non-empty:
                a fetch tool with no rung can answer nothing, so it is not
                registered at all rather than registered and always failing.
            network_policy: SSRF + scheme allowlist applied to the target URL on
                every rung. ``None`` uses the default conservative policy.
            discover_docs_index: Whether a successful fetch also probes the
                origin for an ``llms.txt`` documentation index.
            probe_timeout_seconds: Timeout for that probe.
            clock: Time source for expiring the per-origin probe memory.

        Raises:
            ValueError: If ``providers`` is empty.
        """
        if not providers:
            msg = "WebFetchTool needs at least one configured backend"
            raise ValueError(msg)
        available = ", ".join(sorted(b.value for b in providers))
        super().__init__(
            name="web_fetch",
            description=(
                "Read a web page as clean markdown, with navigation, ads and "
                "cookie banners stripped and headings, tables and fenced code "
                "kept. Use this to read the primary source: reference docs for "
                "a library or API, a changelog or release note, an RFC or "
                "standard, a specific result returned by web_search. Your "
                "training data is older than the libraries you are asked to "
                "write against, so prefer reading the current page over "
                "recalling it. Fetching one authoritative page costs far less "
                "than debugging code written from a stale memory of an API. "
                f"Available backends: {available}. Start with the cheapest one "
                "offered; if it returns no content the result names the "
                "backends you have not tried."
            ),
            parameters_schema=WebFetchArgs.model_json_schema(),
            action_type=ActionType.EXTERNAL_DATA_REQUEST,
            network_policy=network_policy,
        )
        self._providers = dict(providers)
        self._discover_docs_index = discover_docs_index
        self._probe_timeout = probe_timeout_seconds
        # Scoped to the tool instance rather than the module: a settings change
        # rebuilds the tool, which is exactly when a remembered answer should
        # stop being trusted, and it keeps one test's origins out of the next
        # test's cache without anything having to remember to reset it.
        self._index_probe_cache = IndexProbeCache(
            clock=clock if clock is not None else SystemClock(),
            ttl_seconds=INDEX_PROBE_TTL_SECONDS,
        )

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Read one URL over the requested rung.

        Args:
            arguments: Must contain ``url``; optionally ``via``.

        Returns:
            A ``ToolExecutionResult`` carrying the markdown, or a stable error.
        """
        args = parse_typed("tool.execute", arguments, WebFetchArgs)
        url = args.url
        requested = FetchBackend(args.via) if args.via else self._default_backend()

        provider = self._providers.get(requested)
        if provider is None:
            # Logged because it is the signal that agents want a rung the
            # operator has not enabled; without it the only evidence is an
            # error the agent absorbs silently.
            logger.info(
                WEB_FETCH_BACKEND_UNAVAILABLE,
                url=redact_url(url),
                backend=requested.value,
                available=self._available_names(),
            )
            return ToolExecutionResult(
                content=(
                    f"Backend {requested.value!r} is not configured. "
                    f"Available: {self._available_names()}."
                ),
                is_error=True,
            )

        validation = await self._validate_url(url)
        if isinstance(validation, str):
            return ToolExecutionResult(
                content=f"URL blocked: {validation}",
                is_error=True,
            )

        logger.info(WEB_FETCH_START, url=redact_url(url), backend=requested.value)
        try:
            page = await provider.fetch(url)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                WEB_FETCH_FAILED,
                url=redact_url(url),
                backend=requested.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=(
                    f"Fetch failed via {requested.value}. "
                    f"{self._remaining_hint(requested)}"
                ),
                is_error=True,
            )

        if not page.markdown.strip():
            logger.info(WEB_FETCH_EMPTY, url=redact_url(url), backend=requested.value)
            return ToolExecutionResult(
                content=(
                    f"No readable content extracted from {url} via "
                    f"{requested.value}. {self._remaining_hint(requested)}"
                ),
                metadata={
                    "url": url,
                    "backend": requested.value,
                    "result_characters": 0,
                },
            )

        docs_index = await self._discover_index(url)
        logger.info(
            WEB_FETCH_SUCCESS,
            url=redact_url(url),
            backend=requested.value,
            result_characters=len(page.markdown),
            truncated=page.truncated,
        )
        return ToolExecutionResult(
            content=f"{self._render(page)}{discovery_notice(docs_index)}",
            metadata={
                "url": url,
                "final_url": page.final_url or url,
                "title": page.title,
                "backend": requested.value,
                "truncated": page.truncated,
                "result_characters": len(page.markdown),
                "docs_index_url": docs_index,
            },
        )

    async def _discover_index(self, url: str) -> str:
        """Probe the fetched origin for an ``llms.txt`` documentation index.

        Returns:
            The index URL, or an empty string when disabled or not published.
        """
        if not self._discover_docs_index:
            return ""
        return await discover_llms_txt(
            url,
            network_policy=self._network_policy,
            timeout_seconds=self._probe_timeout,
            cache=self._index_probe_cache,
        )

    def _default_backend(self) -> FetchBackend:
        """The cheapest configured rung.

        Returns:
            ``LOCAL`` when configured, else the next cheapest available. This
            is an ordering over a fixed three-member enum, not a search over
            anything the operator can reorder, so it cannot silently change.

        Raises:
            ValueError: If no rung is configured, which the constructor already
                refuses; kept as a loud invariant rather than a quiet default.
        """
        for backend in (FetchBackend.LOCAL, FetchBackend.PROXY, FetchBackend.RENDER):
            if backend in self._providers:
                return backend
        msg = "WebFetchTool has no configured backend"
        raise ValueError(msg)

    def _available_names(self) -> str:
        """Comma-joined names of every configured rung.

        Returns:
            The rung names, sorted.
        """
        return ", ".join(sorted(b.value for b in self._providers))

    def _remaining_hint(self, tried: FetchBackend) -> str:
        """Name the rungs not yet tried, so escalation stays the agent's call.

        Returns:
            A sentence naming the untried rungs, or one saying there are none.
        """
        remaining = sorted(b.value for b in self._providers if b is not tried)
        if not remaining:
            return "No other backend is configured."
        joined = ", ".join(remaining)
        return f"Backends not tried: {joined}. Re-call with via set to one of them."

    @staticmethod
    def _render(page: FetchedPage) -> str:
        """Render a page as the title / URL header plus its markdown.

        Returns:
            The formatted block handed to the model.
        """
        header: list[str] = []
        if page.title:
            header.append(f"# {page.title}")
        header.append(f"Source: {page.final_url or page.url}")
        return "\n".join([*header, "", page.markdown])


__all__ = [
    "FetchBackend",
    "FetchedPage",
    "WebFetchProvider",
    "WebFetchRungs",
    "WebFetchTool",
    "WebToolsWiring",
]
