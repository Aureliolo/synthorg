"""HTML parse guard for tool output sanitization.

Parses HTML-returning tool output with ``lxml``, strips hidden
injection vectors (scripts, styles, hidden elements), and detects
render-gap attacks where rendered text differs substantially from
raw visible HTML content.

This is a standalone post-processor called from ``ToolInvoker``,
not a middleware.
"""

import re
from dataclasses import dataclass

from lxml.html import HtmlElement, tostring
from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import compare_ci
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.tool import (
    TOOL_HTML_PARSE_ERROR,
    TOOL_HTML_PARSE_GAP_DETECTED,
    TOOL_HTML_PARSE_STRIPPED,
)
from synthorg.tools.html_parse_safety import (
    XXEDetectedError,
    parse_html_safely,
)

logger = get_logger(__name__)

# Patterns that indicate the content is likely HTML.
_HTML_TAG_PATTERN = re.compile(r"<[a-zA-Z][^>]*>")

# A tool RESULT is judged when it is an HTML document, or a fragment
# carrying one of the constructs below. A tag anywhere is neither:
# TypeScript generics, JSX, a here-document redirect, a unified diff and a
# JUnit report all carry one, and reading them as HTML returned generics
# without their arguments, a diff without its lines and an XML report as
# nothing at all, to an agent about to edit what it had just read.
_HTML_DOCUMENT_PATTERN = re.compile(
    r"<(?:!doctype\s+html|html|head|body)[\s>]",
    re.IGNORECASE,
)

# A FRAGMENT is judged only when it carries something the strip would take:
# a tag it drops, an event handler with a quoted value, a hidden style or an
# ``aria-hidden``. A forge issue body and a fetched snippet arrive without a
# document tag and are exactly where an injection hides. The quoted value is
# what keeps JSX out (``onClick={fn}``), an HTML comment is not a trigger
# because source code carries them, and a style value ends at a terminator
# so ``opacity: 0}}`` in a component is not one either.
_HIDDEN_CONSTRUCT_PATTERN = re.compile(
    r"<(?:script|style|noscript|iframe|object|embed|applet)\b"
    r"""|\son[a-z]+\s*=\s*['"]"""
    r"""|aria-hidden\s*=\s*['"]?true"""
    r"""|(?:display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?:\.0*)?)"""
    r"""\s*(?:;|['"]|$)""",
    re.IGNORECASE | re.MULTILINE,
)

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


def _rejected_result() -> HTMLSanitizeResult:
    """Return the safe-empty verdict for a payload that could not be judged.

    Returns:
        Result of type ``HTMLSanitizeResult``.
    """
    return HTMLSanitizeResult(
        cleaned="",
        gap_detected=True,
        gap_ratio=1.0,
        stripped_element_count=0,
    )


def looks_like_html_document(raw: str) -> bool:
    """Whether *raw* is an HTML document rather than text with a tag in it.

    Returns:
        ``True`` when a document-level tag opens somewhere in *raw*.
    """
    return bool(_HTML_DOCUMENT_PATTERN.search(raw))


def carries_hidden_construct(raw: str) -> bool:
    """Whether *raw* is tagged text carrying something the strip would take.

    Returns:
        ``True`` when *raw* has a tag and one of the constructs the
        sanitiser removes; a fragment with neither is left alone.
    """
    return bool(_HTML_TAG_PATTERN.search(raw) and _HIDDEN_CONSTRUCT_PATTERN.search(raw))


@dataclass(frozen=True, slots=True)
class GuardedToolOutput:
    """What the tool-result door hands the model, and why.

    Attributes:
        content: The text to hand the model: the original when nothing was
            judged or nothing was hidden, the re-serialised markup when
            something was stripped, empty when the payload was refused.
        verdict: The strip's measurement behind *content*.
        rejected: Whether the payload was refused rather than judged, so
            the caller can say so instead of passing off empty as success.
    """

    content: str
    verdict: HTMLSanitizeResult
    rejected: bool


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

        stripped = self._strip_or_reject(raw)
        if stripped is None:
            return _rejected_result()
        return stripped[1]

    def guard_tool_output(self, raw: str) -> GuardedToolOutput:
        """Hand a tool result to the model as the tool returned it, or safer.

        :meth:`sanitize` answers with the TEXT of anything carrying a tag,
        which is the reading a fetched page wants and the wrong one for a
        tool RESULT: an agent edits what it reads, so a TypeScript file
        returned without its generics, or a JUnit report returned as nothing,
        cannot be matched back to the file it came from. What is judged here
        is an HTML DOCUMENT, or a fragment carrying a construct the strip
        would take; either with nothing hidden in it is returned byte for
        byte.

        Returns:
            The content to hand the model with the verdict behind it, and
            whether the payload was refused rather than judged.
        """
        judged = looks_like_html_document(raw) or carries_hidden_construct(raw)
        if not self._config.enabled or not raw or not judged:
            return GuardedToolOutput(raw, _passthrough_result(raw), rejected=False)

        stripped = self._strip_or_reject(raw)
        if stripped is None:
            return GuardedToolOutput("", _rejected_result(), rejected=True)
        doc, result = stripped
        if result.stripped_element_count == 0 and not result.gap_detected:
            return GuardedToolOutput(raw, result, rejected=False)
        markup = tostring(doc, encoding="unicode", method="html")
        return GuardedToolOutput(markup, result, rejected=False)

    def _strip_or_reject(
        self,
        raw: str,
    ) -> tuple[HtmlElement, HTMLSanitizeResult] | None:
        """Parse and strip *raw*, or answer ``None`` for a payload refused.

        Returns:
            The stripped document with its verdict, or ``None`` when the
            payload was rejected by the XXE pre-scan or could not be parsed.
        """
        try:
            doc = parse_html_safely(raw)
        except XXEDetectedError:
            # XXE rejection was already logged via
            # ``TOOL_HTML_PARSE_XXE_DETECTED`` inside the pre-scan; do
            # not double-emit a generic parse-error event with a
            # traceback attached to the attacker-controlled payload.
            return None
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
            return None
        return doc, self._strip_and_measure(doc)

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

        # The verdict does not survive the invoker boundary, so a strip below
        # the gap threshold would otherwise leave no trace anywhere.
        if stripped_count:
            logger.info(
                TOOL_HTML_PARSE_STRIPPED,
                stripped_count=stripped_count,
                gap_ratio=gap_ratio,
                gap_detected=gap_detected,
            )
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

        # A comment is text a renderer never shows, so it counts as hidden
        # content: a document whose only concealment is a comment is still
        # one that was rewritten.
        for comment in doc.iter(etree.Comment):
            comment.drop_tree()
            stripped += 1

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
    "GuardedToolOutput",
    "HTMLParseGuard",
    "HTMLParseGuardConfig",
    "HTMLSanitizeResult",
    "carries_hidden_construct",
    "looks_like_html_document",
    "sanitize_html_document",
]
