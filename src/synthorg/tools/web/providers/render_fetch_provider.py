# module-kind: adapter
"""The ``render`` fetch rung: run the page's scripts, then extract.

A documentation site that builds its body in JavaScript returns a near-empty
document to a plain GET, so the local rung reports nothing readable and this
rung is the answer. It drives the headless browser the project already ships
(Playwright in a ``DockerSandbox``) through the ``content`` mode, then runs
the identical extractor the other rungs use, so the markdown is comparable
across all three and only the fetch differs.
"""

from typing import Final, Protocol, runtime_checkable

from synthorg.observability import get_logger
from synthorg.observability.events.web import WEB_FETCH_FAILED
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.web.errors import WebFetchResponseError
from synthorg.tools.web.extract import extract_markdown
from synthorg.tools.web.web_fetch import FetchBackend, FetchedPage

logger = get_logger(__name__)

_CONTENT_MODE: Final[str] = "content"


@runtime_checkable
class RenderedPageSource(Protocol):
    """The slice of the browser tool this rung drives."""

    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Run one browser operation."""
        ...


class RenderFetchProvider:
    """Read a page after its scripts have run.

    Args:
        browser: The headless-browser tool, already bound to a sandbox.
        char_budget: Ceiling on the markdown handed back.

    Raises:
        ValueError: If ``char_budget`` is not positive.
    """

    def __init__(
        self,
        *,
        browser: RenderedPageSource,
        char_budget: int,
    ) -> None:
        if char_budget <= 0:
            msg = f"char_budget must be positive, got {char_budget}"
            raise ValueError(msg)
        self._browser = browser
        self._char_budget = char_budget

    @property
    def backend(self) -> FetchBackend:
        """This rung's identity."""
        return FetchBackend.RENDER

    @property
    def capabilities(self) -> tuple[str, ...]:
        """What this rung offers that a plain GET cannot."""
        return ("javascript rendering",)

    async def fetch(self, url: str) -> FetchedPage:
        """Render *url* and extract it to markdown.

        Args:
            url: Absolute http(s) URL, already policy-checked by the tool.

        Returns:
            The extracted page.

        Raises:
            WebFetchResponseError: If the browser reported a failure or
                returned no document.
        """
        result = await self._browser.execute(
            arguments={"mode": _CONTENT_MODE, "url": url}
        )
        if result.is_error:
            # The browser tool has already logged this failure with its own
            # context; repeating its message here would only re-emit whatever
            # the page put in it.
            logger.warning(
                WEB_FETCH_FAILED,
                backend=FetchBackend.RENDER.value,
                reason="browser_error",
            )
            msg = "headless browser could not render the page"
            raise WebFetchResponseError(msg)

        metadata = result.metadata or {}
        raw_html = metadata.get("html")
        if not isinstance(raw_html, str) or not raw_html:
            msg = "headless browser returned no document"
            raise WebFetchResponseError(msg)
        final = metadata.get("final_url")
        final_url = final if isinstance(final, str) else url

        document = extract_markdown(
            raw_html,
            char_budget=self._char_budget,
            url=url,
        )
        return FetchedPage(
            url=url,
            final_url=final_url,
            title=document.title,
            markdown=document.markdown,
            backend=FetchBackend.RENDER,
            truncated=document.truncated,
        )


__all__ = ["RenderFetchProvider", "RenderedPageSource"]
