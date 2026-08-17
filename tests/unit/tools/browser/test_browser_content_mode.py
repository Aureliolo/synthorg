"""Unit tests for the browser tool's ``content`` mode.

The mode exists so a page that builds its body in JavaScript can be read at
all. What it hands the agent is extracted markdown within a budget, not the
serialised DOM: this result goes straight into an agent's context, and a
script-heavy page serialises to megabytes of markup wrapped around the part
worth reading. The raw document still travels in the metadata, because the
render fetch rung consumes it.
"""

import json
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest

from synthorg.tools.browser._args import BrowserToolArgs
from synthorg.tools.browser._constants import CONTENT_SOURCE_BUDGET_MULTIPLIER
from synthorg.tools.browser._settings import BrowserSettings
from synthorg.tools.browser.browser_tool import BrowserTool
from synthorg.tools.sandbox.protocol import SandboxBackend
from synthorg.tools.sandbox.result import SandboxResult
from synthorg.tools.web.extract import TRUNCATION_MARKER
from tests._shared import JsonDict
from tests._shared.mock_of import mock_of

pytestmark = pytest.mark.unit

_URL = "https://docs.example-provider.test/api"

_DOCS_HTML = (
    "<html><head><title>Widget API</title></head><body>"
    '<nav><a href="/x">Navigation link</a></nav>'
    "<main><h1>Widget API</h1>"
    "<p>Call <code>go()</code> to start.</p>"
    "<pre><code>go(retries=3)</code></pre>"
    "</main>"
    "<footer>Copyright notice</footer>"
    "</body></html>"
)


def _content_payload(html: str, *, source_truncated: bool = False) -> JsonDict:
    return {
        "status": "ok",
        "navigation": {
            "requested_url": _URL,
            "final_url": _URL,
            "status_code": 200,
            "duration_seconds": 0.1,
        },
        "content": html,
        "content_truncated": source_truncated,
    }


@pytest.fixture
def fake_sandbox() -> SandboxBackend:
    sandbox: SandboxBackend = mock_of[SandboxBackend](
        execute=AsyncMock(spec=SandboxBackend.execute),
        release_owner=AsyncMock(spec=SandboxBackend.release_owner),
    )
    return sandbox


def _tool(
    sandbox: SandboxBackend,
    workspace: Path,
    *,
    budget: int = 40000,
) -> BrowserTool:
    return BrowserTool(
        sandbox=sandbox,
        workspace=workspace,
        settings=BrowserSettings(content_max_characters=budget),
    )


def _serve(
    sandbox: SandboxBackend,
    html: str,
    *,
    source_truncated: bool = False,
) -> None:
    cast(AsyncMock, sandbox.execute).return_value = SandboxResult(
        stdout=json.dumps(_content_payload(html, source_truncated=source_truncated)),
        stderr="",
        returncode=0,
        timed_out=False,
    )


class TestAgentVisibleContent:
    async def test_the_agent_receives_markdown_not_the_dom(
        self,
        tmp_path: Path,
        fake_sandbox: SandboxBackend,
    ) -> None:
        _serve(fake_sandbox, _DOCS_HTML)
        result = await _tool(fake_sandbox, tmp_path).execute(
            arguments={"mode": "content", "url": _URL},
        )
        assert result.is_error is False
        assert "# Widget API" in result.content
        assert "<html>" not in result.content
        assert "<main>" not in result.content

    async def test_chrome_does_not_reach_the_agent(
        self,
        tmp_path: Path,
        fake_sandbox: SandboxBackend,
    ) -> None:
        _serve(fake_sandbox, _DOCS_HTML)
        result = await _tool(fake_sandbox, tmp_path).execute(
            arguments={"mode": "content", "url": _URL},
        )
        assert "Navigation link" not in result.content
        assert "Copyright notice" not in result.content

    async def test_the_title_is_reported(
        self,
        tmp_path: Path,
        fake_sandbox: SandboxBackend,
    ) -> None:
        _serve(fake_sandbox, _DOCS_HTML)
        result = await _tool(fake_sandbox, tmp_path).execute(
            arguments={"mode": "content", "url": _URL},
        )
        assert result.metadata["title"] == "Widget API"


def _long_article(paragraphs: int) -> str:
    """Build a page the extractor will actually accept.

    Varied prose on purpose: the extractor scores a candidate on how
    article-like it looks, and a page of one repeated sentence is discarded as
    boilerplate, which would let a budget assertion pass on an empty read.
    """
    body = "".join(
        f"<p>Section {index} explains how the widget handles retry number"
        f" {index}, why the timeout defaults to {index} seconds, and what the"
        f" caller should expect when attempt {index} fails partway through."
        "</p>"
        for index in range(paragraphs)
    )
    return (
        "<html><head><title>Guide</title></head>"
        f"<body><main>{body}</main></body></html>"
    )


class TestBudget:
    """The ceiling is the point of the mode, so it is asserted directly."""

    async def test_an_oversized_page_is_cut_and_says_so(
        self,
        tmp_path: Path,
        fake_sandbox: SandboxBackend,
    ) -> None:
        _serve(fake_sandbox, _long_article(200))
        result = await _tool(fake_sandbox, tmp_path, budget=1000).execute(
            arguments={"mode": "content", "url": _URL},
        )
        assert result.metadata["markdown"] != ""
        assert result.metadata["truncated"] is True
        assert TRUNCATION_MARKER.strip() in result.content

    async def test_the_agent_visible_content_stays_near_the_budget(
        self,
        tmp_path: Path,
        fake_sandbox: SandboxBackend,
    ) -> None:
        """The regression this guards: an unbounded DOM dump into context.

        The serialised document is far larger than the budget, so a result that
        tracked the document rather than the budget lands here. The
        non-empty assertion matters as much as the ceiling: an empty read would
        satisfy any upper bound while proving nothing.
        """
        html = _long_article(400)
        _serve(fake_sandbox, html)
        result = await _tool(fake_sandbox, tmp_path, budget=1000).execute(
            arguments={"mode": "content", "url": _URL},
        )
        assert len(html) > 10000
        assert result.metadata["markdown"] != ""
        assert len(result.content) < 1000 + len(TRUNCATION_MARKER) + 500

    async def test_a_page_under_the_budget_is_not_marked_truncated(
        self,
        tmp_path: Path,
        fake_sandbox: SandboxBackend,
    ) -> None:
        _serve(fake_sandbox, _long_article(3))
        result = await _tool(fake_sandbox, tmp_path, budget=40000).execute(
            arguments={"mode": "content", "url": _URL},
        )
        assert result.metadata["truncated"] is False
        assert TRUNCATION_MARKER.strip() not in result.content


class TestTheCaptureItselfIsBounded:
    """The budget cuts the markdown; something has to cut the DOM.

    The executor serialises the whole document into one JSON envelope that
    crosses the sandbox boundary, so without a ceiling in the container the
    target decides how much the host allocates to parse its reply.
    """

    def test_the_capture_ceiling_is_sent_to_the_executor(
        self,
        tmp_path: Path,
        fake_sandbox: SandboxBackend,
    ) -> None:
        tool = _tool(fake_sandbox, tmp_path, budget=1000)

        payload = tool._build_executor_payload(
            operation="content",
            url=_URL,
            args=BrowserToolArgs(mode="content", url=_URL),
            screenshot_path=None,
            axe_container="/axe.js",
        )

        # Generous against the markdown budget on purpose: extraction discards
        # most of a page, so a ceiling near the budget would starve it.
        assert payload["content_max_characters"] == 1000 * (
            CONTENT_SOURCE_BUDGET_MULTIPLIER
        )

    async def test_a_capped_capture_is_reported_as_truncated(
        self,
        tmp_path: Path,
        fake_sandbox: SandboxBackend,
    ) -> None:
        # The extracted markdown fits the budget, so only the executor knows
        # the document behind it was cut. Losing that flag reports a partial
        # page as a whole one.
        _serve(fake_sandbox, _long_article(3), source_truncated=True)

        result = await _tool(fake_sandbox, tmp_path, budget=40000).execute(
            arguments={"mode": "content", "url": _URL},
        )

        assert result.metadata["truncated"] is True
        assert result.metadata["source_truncated"] is True

    async def test_an_uncapped_capture_is_not_reported_as_truncated(
        self,
        tmp_path: Path,
        fake_sandbox: SandboxBackend,
    ) -> None:
        _serve(fake_sandbox, _long_article(3))

        result = await _tool(fake_sandbox, tmp_path, budget=40000).execute(
            arguments={"mode": "content", "url": _URL},
        )

        assert result.metadata["truncated"] is False
        assert result.metadata["source_truncated"] is False


class TestRenderRungHandoff:
    """The raw document still has to reach the render fetch rung."""

    async def test_the_dom_travels_in_metadata(
        self,
        tmp_path: Path,
        fake_sandbox: SandboxBackend,
    ) -> None:
        _serve(fake_sandbox, _DOCS_HTML)
        result = await _tool(fake_sandbox, tmp_path).execute(
            arguments={"mode": "content", "url": _URL},
        )
        assert result.metadata["html"] == _DOCS_HTML

    async def test_the_dom_length_is_reported_before_extraction(
        self,
        tmp_path: Path,
        fake_sandbox: SandboxBackend,
    ) -> None:
        _serve(fake_sandbox, _DOCS_HTML)
        result = await _tool(fake_sandbox, tmp_path).execute(
            arguments={"mode": "content", "url": _URL},
        )
        assert result.metadata["content_length"] == len(_DOCS_HTML)

    async def test_the_final_url_is_reported(
        self,
        tmp_path: Path,
        fake_sandbox: SandboxBackend,
    ) -> None:
        _serve(fake_sandbox, _DOCS_HTML)
        result = await _tool(fake_sandbox, tmp_path).execute(
            arguments={"mode": "content", "url": _URL},
        )
        assert result.metadata["final_url"] == _URL
