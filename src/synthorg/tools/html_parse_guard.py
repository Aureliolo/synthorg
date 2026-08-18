"""HTML parse guard for tool output sanitization.

Parses HTML-returning tool output with ``lxml``, strips hidden
injection vectors (scripts, styles, hidden elements), and detects
render-gap attacks where rendered text differs substantially from
raw visible HTML content.

This is a standalone post-processor called from ``ToolInvoker``,
not a middleware.
"""

import re

from lxml.html import HtmlElement, tostring
from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import compare_ci
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.tool import (
    TOOL_HTML_PARSE_ERROR,
    TOOL_HTML_PARSE_GAP_DETECTED,
)
from synthorg.tools.html_parse_safety import (
    XXEDetectedError,
    parse_html_safely,
)

logger = get_logger(__name__)

# Patterns that indicate the content is likely HTML.
_HTML_TAG_PATTERN = re.compile(r"<[a-zA-Z][^>]*>")

# CSS patterns for hidden elements.
#
# Text a human never sees but a model reads in full is the whole point of an
# indirect prompt injection, and "hidden" has more spellings than the obvious
# two. Each pattern below was confirmed to carry injected text through the
# extractor and into a model's context: a zero font size renders nothing, and
# a large negative offset parks the element outside the viewport while leaving
# it in the document.
#
# Deliberately NOT matched: text coloured to match its background. Deciding
# that needs the computed cascade and a colour comparison, neither of which a
# per-element attribute scan has, and a guess would strip legitimately styled
# prose. It is the one hiding technique left standing here.
_HIDDEN_STYLE_PATTERNS = (
    re.compile(r"display\s*:\s*none", re.IGNORECASE),
    re.compile(r"visibility\s*:\s*hidden", re.IGNORECASE),
    re.compile(
        r"font-size\s*:\s*0(?:\.0*)?\s*(?:px|em|rem|pt|%)?\s*(?:;|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:left|top|right|bottom)\s*:\s*-\d{4,}\s*(?:px|em|rem|pt)",
        re.IGNORECASE,
    ),
    re.compile(r"text-indent\s*:\s*-\d{4,}\s*(?:px|em|rem|pt)", re.IGNORECASE),
    re.compile(r"clip\s*:\s*rect\s*\(\s*0[a-z%]*[\s,]+0[a-z%]*[\s,]", re.IGNORECASE),
    re.compile(r"opacity\s*:\s*0(?:\.0*)?\s*(?:;|$)", re.IGNORECASE),
)

# Tags to strip entirely (content and all).
_STRIP_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "iframe",
        "object",
        "embed",
        "applet",
    }
)

# Event handler attributes to strip from all elements.
_EVENT_HANDLER_PREFIXES = frozenset(
    {
        "onclick",
        "ondblclick",
        "onmousedown",
        "onmouseup",
        "onmouseover",
        "onmousemove",
        "onmouseout",
        "onkeypress",
        "onkeydown",
        "onkeyup",
        "onfocus",
        "onblur",
        "onsubmit",
        "onreset",
        "onselect",
        "onchange",
        "onload",
        "onerror",
        "onresize",
        "onscroll",
        "onunload",
        "onabort",
        "oninput",
        "oncontextmenu",
        "ondrag",
        "ondrop",
        "onpaste",
        "formaction",
    }
)


class HTMLParseGuardConfig(BaseModel):
    """Configuration for the HTML parse guard.

    Attributes:
        enabled: Whether sanitization is active.
        gap_threshold_ratio: Ratio of hidden-to-total content above
            which ``gap_detected`` is set to ``True``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Whether HTML sanitization is active",
    )
    gap_threshold_ratio: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Hidden-to-total content ratio threshold for gap detection",
    )


class HTMLSanitizeResult(BaseModel):
    """Result of HTML sanitization.

    Attributes:
        cleaned: Sanitized output text.
        gap_detected: Whether a significant render gap was found.
        gap_ratio: Ratio of hidden content to total content.
        stripped_element_count: Number of elements stripped.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    cleaned: str = Field(description="Sanitized output text")
    gap_detected: bool = Field(
        default=False,
        description="Whether a significant render gap was found",
    )
    gap_ratio: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Hidden-to-total content ratio",
    )
    stripped_element_count: int = Field(
        default=0,
        ge=0,
        description="Number of elements stripped",
    )


def _passthrough_result(content: str) -> HTMLSanitizeResult:
    """Return an unchanged result for non-HTML or disabled guard.

    Returns:
        Result of type ``HTMLSanitizeResult``.
    """
    return HTMLSanitizeResult(
        cleaned=content,
        gap_detected=False,
        gap_ratio=0.0,
        stripped_element_count=0,
    )


class HTMLParseGuard:
    """Sanitize HTML tool output by stripping hidden injection vectors.

    Strips ``<script>``, ``<style>``, ``<noscript>`` tags, HTML
    comments, and elements with ``display:none``,
    ``visibility:hidden``, or ``aria-hidden="true"`` attributes.

    Detects render-gap attacks by comparing visible text length
    before and after stripping hidden content.

    Args:
        config: Guard configuration. Defaults to enabled with 5%
            gap threshold.
    """

    def __init__(
        self,
        config: HTMLParseGuardConfig | None = None,
    ) -> None:
        self._config = config or HTMLParseGuardConfig()

    def sanitize(self, raw: str) -> HTMLSanitizeResult:
        """Sanitize HTML content, stripping hidden injection vectors.

        Args:
            raw: Raw tool output (may or may not be HTML).

        Returns:
            Sanitization result with cleaned content and gap metadata.
        """
        if not self._config.enabled:
            return _passthrough_result(raw)

        if not raw or not _HTML_TAG_PATTERN.search(raw):
            return _passthrough_result(raw)

        try:
            return self._sanitize_html(raw)
        except XXEDetectedError:
            # XXE rejection was already logged via
            # ``TOOL_HTML_PARSE_XXE_DETECTED`` inside the pre-scan; do
            # not double-emit a generic parse-error event with a
            # traceback attached to the attacker-controlled payload.
            return HTMLSanitizeResult(
                cleaned="",
                gap_detected=True,
                gap_ratio=1.0,
                stripped_element_count=0,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # Parse failure on untrusted HTML: scrub the exception
            # description and drop ``exc_info`` so the raw payload (or
            # any credentials it carried) is not serialized via the
            # traceback frame locals.
            logger.warning(
                TOOL_HTML_PARSE_ERROR,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                content_length=len(raw),
            )
            # Return safe empty result instead of raw attacker-
            # controlled content.
            return HTMLSanitizeResult(
                cleaned="",
                gap_detected=True,
                gap_ratio=1.0,
                stripped_element_count=0,
            )

    def sanitize_document(self, raw: str) -> tuple[str, HTMLSanitizeResult]:
        """Strip hidden and dangerous content, re-serialised as HTML.

        :meth:`sanitize` answers with flat TEXT, which suits a caller whose
        tool returned prose and ruins one about to extract structure: the
        headings, tables and fenced code an extractor exists to preserve are
        gone before it sees them.

        This exists because the invoker's guard is keyed on a tool RESULT
        looking like HTML, so a tool that consumes HTML and returns markdown
        offers it nothing to act on. By then the page's hidden text has been
        inlined into ordinary prose, indistinguishable from the author's own
        words. The strip therefore has to run before the extractor rather
        than after the tool.

        Returns:
            The sanitised HTML, and the verdict whose ``gap_detected`` is the
            operator-visible alarm that a page carried substantial hidden
            content. ``cleaned`` stays the TEXT reading so the gap ratio
            remains comparable with every other caller's.

        Raises:
            XXEDetectedError: If the payload carries an external DOCTYPE or an
                entity declaration.
        """
        doc = parse_html_safely(raw)
        result = self._strip_and_measure(doc)
        return tostring(doc, encoding="unicode", method="html"), result

    def _sanitize_html(self, raw: str) -> HTMLSanitizeResult:
        """Parse and sanitize HTML content using lxml.

        Returns:
            Result of type ``HTMLSanitizeResult``.
        """
        return self._strip_and_measure(parse_html_safely(raw))

    def _strip_and_measure(self, doc: HtmlElement) -> HTMLSanitizeResult:
        """Strip *doc* in place and report how much content was hidden.

        Shared by the text-returning and HTML-returning entry points, so both
        judge a page by the same rule and one alarm covers both.

        Returns:
            The verdict for the stripped document.
        """
        # Capture original text before stripping (single parse).
        original_text = doc.text_content().strip()
        stripped_count = self._strip_dangerous_elements(doc)
        cleaned_text = doc.text_content().strip()
        gap_ratio = self._compute_gap_ratio(original_text, cleaned_text)
        gap_detected = gap_ratio > self._config.gap_threshold_ratio

        if gap_detected:
            logger.warning(
                TOOL_HTML_PARSE_GAP_DETECTED,
                gap_ratio=gap_ratio,
                threshold=self._config.gap_threshold_ratio,
                stripped_count=stripped_count,
                hidden_chars=max(0, len(original_text) - len(cleaned_text)),
            )

        return HTMLSanitizeResult(
            cleaned=cleaned_text,
            gap_detected=gap_detected,
            gap_ratio=gap_ratio,
            stripped_element_count=stripped_count,
        )

    @staticmethod
    def _strip_event_handlers(doc: HtmlElement) -> int:
        """Strip event handler attributes from all elements.

        Returns:
            Result of type ``int``.
        """
        stripped = 0
        for element in doc.iter():
            if not hasattr(element, "tag") or not isinstance(element.tag, str):
                continue
            for attr in list(element.attrib):
                if attr.lower() in _EVENT_HANDLER_PREFIXES:
                    del element.attrib[attr]
                    stripped += 1
        return stripped

    @staticmethod
    def _strip_dangerous_elements(doc: HtmlElement) -> int:
        """Strip scripts, styles, comments, and hidden elements.

        Returns the count of stripped elements.

        Returns:
            Result of type ``int``.
        """
        from lxml import etree  # noqa: PLC0415

        stripped = 0

        for tag in _STRIP_TAGS:
            for element in doc.iter(tag):
                element.drop_tree()
                stripped += 1

        for comment in doc.iter(etree.Comment):
            comment.drop_tree()

        # Strip SVG script injection vectors.
        for element in doc.iter("{http://www.w3.org/2000/svg}script"):
            element.drop_tree()
            stripped += 1

        stripped += HTMLParseGuard._strip_event_handlers(doc)
        stripped += HTMLParseGuard._strip_hidden_elements(doc)

        return stripped

    @staticmethod
    def _strip_hidden_elements(doc: HtmlElement) -> int:
        """Strip elements hidden via attributes or CSS.

        Returns:
            Result of type ``int``.
        """
        elements_to_drop: list[HtmlElement] = []
        for element in doc.iter():
            if not hasattr(element, "tag") or not isinstance(element.tag, str):
                continue
            if element.get("hidden") is not None:
                elements_to_drop.append(element)
                continue
            if compare_ci(element.get("aria-hidden", ""), "true"):
                elements_to_drop.append(element)
                continue
            style = element.get("style", "")
            if style and any(p.search(style) for p in _HIDDEN_STYLE_PATTERNS):
                elements_to_drop.append(element)

        dropped = 0
        for element in elements_to_drop:
            if getattr(element, "getparent", lambda: None)() is not None:
                element.drop_tree()
                dropped += 1

        return dropped

    @staticmethod
    def _compute_gap_ratio(original: str, cleaned: str) -> float:
        """Compute the ratio of hidden content to total content.

        Returns:
            Result of type ``float``.
        """
        original_len = len(original) or 1
        hidden_len = max(0, original_len - len(cleaned))
        return min(hidden_len / original_len, 1.0)


def sanitize_html_document(
    raw: str,
    config: HTMLParseGuardConfig | None = None,
) -> tuple[str, HTMLSanitizeResult]:
    """Strip hidden and dangerous content, keeping the result as HTML.

    Thin wrapper over :meth:`HTMLParseGuard.sanitize_document` for a caller
    that wants the default configuration.

    Returns:
        The sanitised HTML and the strip's verdict.

    Raises:
        XXEDetectedError: If the payload carries an external DOCTYPE or an
            entity declaration.
    """
    return HTMLParseGuard(config).sanitize_document(raw)


__all__ = [
    "HTMLParseGuard",
    "HTMLParseGuardConfig",
    "HTMLSanitizeResult",
    "sanitize_html_document",
]
