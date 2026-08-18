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

The contracts the rungs and the boot wiring share live in ``fetch_types``;
this module is the tool that consumes them.
"""

from typing import ClassVar, Final, override

from pydantic import BaseModel

from synthorg.core.boundary import parse_typed
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
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
from synthorg.tools.web.fetch_types import (
    FetchBackend,
    FetchedPage,
    WebFetchProvider,
)
from synthorg.tools.web.llms_txt import (
    INDEX_PROBE_TTL_SECONDS,
    IndexProbeCache,
    discover_llms_txt,
    discovery_notice,
)

logger = get_logger(__name__)

_DEFAULT_PROBE_TIMEOUT: Final[float] = 5.0


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
            return self._unavailable_result(url, requested)

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
            return self._failed_result(url, requested, exc)

        if not page.markdown.strip():
            return self._empty_result(url, requested)
        return await self._page_result(page, url=url, requested=requested)

    def _unavailable_result(
        self,
        url: str,
        requested: FetchBackend,
    ) -> ToolExecutionResult:
        """Answer a call naming a rung the operator has not enabled.

        Returns:
            The error result naming what is configured instead.
        """
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

    def _failed_result(
        self,
        url: str,
        requested: FetchBackend,
        exc: Exception,
    ) -> ToolExecutionResult:
        """Answer a rung that raised, naming the rungs still untried.

        Returns:
            The error result.
        """
        logger.warning(
            WEB_FETCH_FAILED,
            url=redact_url(url),
            backend=requested.value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ToolExecutionResult(
            content=(
                f"Fetch failed via {requested.value}. {self._remaining_hint(requested)}"
            ),
            is_error=True,
        )

    def _empty_result(self, url: str, requested: FetchBackend) -> ToolExecutionResult:
        """Answer a rung that read the page and found nothing readable.

        Returns:
            A non-error result: an empty read is an answer, and the agent
            decides from it whether to spend another rung.
        """
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

    async def _page_result(
        self,
        page: FetchedPage,
        *,
        url: str,
        requested: FetchBackend,
    ) -> ToolExecutionResult:
        """Render a read page, probing its origin for a documentation index.

        Returns:
            The success result.
        """
        # Probed at the origin that actually SERVED the markdown. A rung that
        # follows redirects can land on a different host entirely (a vendor
        # docs site moved behind its own domain), and probing the requested
        # URL's origin then asks a host that answered nothing whether it
        # publishes an index for a page it does not serve.
        docs_index = await self._discover_index(page.final_url or url)
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
                "hidden_content_detected": page.hidden_content_detected,
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


__all__ = ["WebFetchTool"]
