"""Unit tests for the ``render`` fetch rung.

The rung that exists for pages which build their body in JavaScript. It drives
the headless browser through its ``content`` mode and then runs the same
extractor the other two rungs use, so markdown is comparable across all three
and a comparison between rungs measures the fetch rather than the extractor.
"""

import json

import pytest
from pydantic import JsonValue

from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.web.errors import WebFetchResponseError
from synthorg.tools.web.providers.render_fetch_provider import RenderFetchProvider
from synthorg.tools.web.web_fetch import (
    FetchBackend,
    RenderedPageSource,
    WebFetchProvider,
)

pytestmark = pytest.mark.unit

_URL = "https://docs.example-provider.test/api"
_FINAL = "https://docs.example-provider.test/api/v2"

_DOCS_HTML = (
    "<html><head><title>Widget API</title></head><body>"
    '<nav><a href="/x">Navigation link</a></nav>'
    "<main><h1>Widget API</h1>"
    "<p>The widget renders after its scripts run and the body is filled in.</p>"
    "<pre><code>go(retries=3)</code></pre>"
    "</main><footer>Copyright notice</footer></body></html>"
)


class _StubBrowser:
    """Stands in for the browser tool at its ``execute`` seam."""

    def __init__(
        self,
        *,
        html: str | None = _DOCS_HTML,
        final_url: str | None = _FINAL,
        is_error: bool = False,
        source_truncated: bool = False,
    ) -> None:
        self._html = html
        self._final_url = final_url
        self._is_error = is_error
        self._source_truncated = source_truncated
        self.calls: list[dict[str, object]] = []

    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        self.calls.append(dict(arguments))
        if self._is_error:
            return ToolExecutionResult(content="browser blew up", is_error=True)
        metadata: dict[str, JsonValue] = {}
        if self._html is not None:
            metadata["html"] = self._html
        if self._final_url is not None:
            metadata["final_url"] = self._final_url
        metadata["source_truncated"] = self._source_truncated
        return ToolExecutionResult(
            content=json.dumps({"ok": True}),
            is_error=False,
            metadata=metadata,
        )


def _provider(
    browser: _StubBrowser,
    *,
    char_budget: int = 50_000,
) -> RenderFetchProvider:
    return RenderFetchProvider(browser=browser, char_budget=char_budget)


class TestContract:
    def test_satisfies_the_protocol(self) -> None:
        assert isinstance(_provider(_StubBrowser()), WebFetchProvider)

    def test_the_stub_satisfies_the_browser_seam(self) -> None:
        assert isinstance(_StubBrowser(), RenderedPageSource)

    def test_identifies_itself(self) -> None:
        assert _provider(_StubBrowser()).backend is FetchBackend.RENDER

    def test_declares_what_it_adds_over_a_plain_get(self) -> None:
        """The agent picks a rung from what each one offers."""
        assert _provider(_StubBrowser()).capabilities == ("javascript rendering",)

    def test_a_non_positive_budget_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="char_budget must be positive"):
            RenderFetchProvider(browser=_StubBrowser(), char_budget=0)


class TestRendering:
    async def test_it_drives_the_browser_content_mode(self) -> None:
        browser = _StubBrowser()
        await _provider(browser).fetch(_URL)
        assert browser.calls == [{"mode": "content", "url": _URL}]

    async def test_the_rendered_dom_goes_through_the_shared_extractor(self) -> None:
        page = await _provider(_StubBrowser()).fetch(_URL)
        assert "# Widget API" in page.markdown
        assert "go(retries=3)" in page.markdown

    async def test_chrome_is_dropped_like_every_other_rung(self) -> None:
        page = await _provider(_StubBrowser()).fetch(_URL)
        assert "Navigation link" not in page.markdown
        assert "Copyright notice" not in page.markdown

    async def test_it_reports_its_own_backend(self) -> None:
        page = await _provider(_StubBrowser()).fetch(_URL)
        assert page.backend is FetchBackend.RENDER

    async def test_the_title_is_read_from_the_rendered_document(self) -> None:
        page = await _provider(_StubBrowser()).fetch(_URL)
        assert page.title == "Widget API"

    async def test_a_redirect_is_reported_as_the_final_url(self) -> None:
        page = await _provider(_StubBrowser()).fetch(_URL)
        assert page.url == _URL
        assert page.final_url == _FINAL

    async def test_a_missing_final_url_falls_back_to_the_request(self) -> None:
        page = await _provider(_StubBrowser(final_url=None)).fetch(_URL)
        assert page.final_url == _URL

    async def test_an_oversized_render_is_cut_and_says_so(self) -> None:
        body = "".join(
            f"<p>Step {i} describes the widget's retry behaviour in detail,"
            f" including what happens when attempt {i} times out.</p>"
            for i in range(200)
        )
        browser = _StubBrowser(html=f"<html><body><main>{body}</main></body></html>")
        page = await _provider(browser, char_budget=1000).fetch(_URL)
        assert page.markdown != ""
        assert page.truncated is True

    async def test_a_capped_capture_is_carried_forward(self) -> None:
        """The browser bounds its own capture, and only it knows it did.

        The HTML handed over can be partial while the markdown extracted from
        it sits well inside the budget, so a rung reading only its own cut
        reports a page that was cut upstream as a whole one.
        """
        browser = _StubBrowser(source_truncated=True)

        page = await _provider(browser).fetch(_URL)

        assert page.markdown != ""
        assert page.truncated is True

    async def test_an_uncapped_capture_is_not_marked_truncated(self) -> None:
        page = await _provider(_StubBrowser()).fetch(_URL)

        assert page.truncated is False


class TestFailureModes:
    async def test_a_browser_error_is_a_response_error(self) -> None:
        with pytest.raises(WebFetchResponseError):
            await _provider(_StubBrowser(is_error=True)).fetch(_URL)

    async def test_a_missing_document_is_a_response_error(self) -> None:
        """An empty render is not a page, and saying so lets the agent re-rung."""
        with pytest.raises(WebFetchResponseError):
            await _provider(_StubBrowser(html=None)).fetch(_URL)

    async def test_an_empty_document_is_a_response_error(self) -> None:
        with pytest.raises(WebFetchResponseError):
            await _provider(_StubBrowser(html="")).fetch(_URL)

    async def test_an_unreadable_page_reports_empty_rather_than_raising(self) -> None:
        """A rendered page with no article content read, versus a failed read."""
        browser = _StubBrowser(html="<html><body></body></html>")
        page = await _provider(browser).fetch(_URL)
        assert page.markdown == ""
