"""Unit tests for HTML-to-markdown extraction.

The point of the fetch tool is that a documentation page survives the trip:
headings, fenced code and tables are the parts worth spending tokens on, and
the navigation and cookie chrome are the parts that are not. These assert that
split directly rather than asserting a character count.
"""

import pytest

from synthorg.tools.web.extract import (
    TRUNCATION_MARKER,
    extract_markdown,
    truncate_at_block,
)

pytestmark = pytest.mark.unit

_DOCS_HTML = """<!DOCTYPE html>
<html><head><title>Widget API reference</title></head>
<body>
<nav id="sidebar"><ul><li><a href="/a">Getting started</a></li>
<li><a href="/b">Tutorials</a></li></ul></nav>
<div class="cookie-banner">We use cookies. <button>Accept all</button></div>
<main>
<h1>Widget API reference</h1>
<p>The <code>Widget</code> class renders a widget. Added in <strong>v3.2</strong>.</p>
<h2>Constructor</h2>
<pre><code class="language-python">from acme import Widget

w = Widget(name="hello", retries=3)
</code></pre>
<h3>Parameters</h3>
<table>
<tr><th>Name</th><th>Type</th></tr>
<tr><td>retries</td><td>int</td></tr>
</table>
<p>See the <a href="https://example.invalid/guide">migration guide</a>.</p>
</main>
<footer>Copyright 2026 Acme. <a href="/privacy">Privacy</a></footer>
</body></html>
"""

_LARGE_BUDGET = 100_000


class TestDocumentationSurvives:
    def test_headings_are_kept(self) -> None:
        doc = extract_markdown(_DOCS_HTML, char_budget=_LARGE_BUDGET)
        assert "# Widget API reference" in doc.markdown
        assert "## Constructor" in doc.markdown

    def test_code_block_body_is_kept(self) -> None:
        """A flattened code sample is the failure this tool exists to fix."""
        doc = extract_markdown(_DOCS_HTML, char_budget=_LARGE_BUDGET)
        assert 'Widget(name="hello", retries=3)' in doc.markdown
        assert "```" in doc.markdown

    def test_inline_code_and_tables_are_kept(self) -> None:
        doc = extract_markdown(_DOCS_HTML, char_budget=_LARGE_BUDGET)
        assert "`Widget`" in doc.markdown
        assert "retries" in doc.markdown

    def test_link_targets_are_kept(self) -> None:
        doc = extract_markdown(_DOCS_HTML, char_budget=_LARGE_BUDGET)
        assert "example.invalid/guide" in doc.markdown

    def test_title_is_read(self) -> None:
        doc = extract_markdown(_DOCS_HTML, char_budget=_LARGE_BUDGET)
        assert doc.title == "Widget API reference"


class TestChromeIsDropped:
    @pytest.mark.parametrize(
        "noise",
        ["Getting started", "Accept all", "Privacy"],
    )
    def test_navigation_cookie_and_footer_do_not_survive(self, noise: str) -> None:
        doc = extract_markdown(_DOCS_HTML, char_budget=_LARGE_BUDGET)
        assert noise not in doc.markdown


class TestEmptyAndMalformed:
    def test_empty_document_reports_empty_not_an_error(self) -> None:
        """An unreadable page and a failed fetch are different answers."""
        doc = extract_markdown("<html><body></body></html>", char_budget=1000)
        assert doc.markdown == ""
        assert doc.truncated is False

    def test_plain_text_input_returns_its_text(self) -> None:
        """A text/plain response is already the content; returning it is right."""
        doc = extract_markdown("not html at all", char_budget=1000)
        assert doc.markdown == "not html at all"

    def test_zero_budget_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="char_budget must be positive"):
            extract_markdown(_DOCS_HTML, char_budget=0)


class TestTruncation:
    def test_under_budget_is_untouched(self) -> None:
        text, cut = truncate_at_block("short", 100)
        assert text == "short"
        assert cut is False

    def test_cut_prefers_a_paragraph_boundary(self) -> None:
        text = "a" * 60 + "\n\n" + "b" * 60
        cut_text, cut = truncate_at_block(text, 100)
        assert cut is True
        assert cut_text == "a" * 60
        assert "b" not in cut_text

    def test_one_long_block_is_hard_cut_rather_than_emptied(self) -> None:
        """Honouring a boundary that early would discard nearly everything."""
        text = "x" * 40 + "\n\n" + "y" * 500
        cut_text, cut = truncate_at_block(text, 400)
        assert cut is True
        assert len(cut_text) == 400

    def test_truncated_extraction_says_so_in_the_content(self) -> None:
        """The model must be able to tell a partial read from a whole one."""
        doc = extract_markdown(_DOCS_HTML, char_budget=80)
        assert doc.truncated is True
        assert TRUNCATION_MARKER.strip() in doc.markdown


class TestShortPagesKeepStructure:
    """A short page is where the default extractor silently drops formatting.

    Below trafilatura's ``MIN_EXTRACTED_SIZE`` the structured result is thrown
    away for a plain-text salvage, so a one-screen API reference arrives with
    its code fences flattened. These pin the tuned configuration that prevents
    it, because the regression is invisible in a longer fixture.
    """

    def test_a_page_under_the_default_threshold_keeps_its_structure(self) -> None:
        html = (
            "<html><head><title>T</title></head><body><main>"
            "<h1>Short reference</h1>"
            "<p>Call <code>go()</code>.</p>"
            "<pre><code>go(retries=3)</code></pre>"
            "</main></body></html>"
        )
        doc = extract_markdown(html, char_budget=_LARGE_BUDGET)
        assert len(doc.markdown) < 250
        assert "# Short reference" in doc.markdown
        assert "`go(retries=3)`" in doc.markdown

    def test_a_short_page_keeps_a_multi_line_fence(self) -> None:
        html = (
            "<html><head><title>T</title></head><body><main>"
            "<h2>Usage</h2>"
            "<pre><code>import acme\nacme.go(retries=3)\n</code></pre>"
            "</main></body></html>"
        )
        doc = extract_markdown(html, char_budget=_LARGE_BUDGET)
        assert len(doc.markdown) < 250
        assert "## Usage" in doc.markdown
        assert "```" in doc.markdown
        assert "acme.go(retries=3)" in doc.markdown
