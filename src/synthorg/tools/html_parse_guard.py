"""HTML parse guard for tool output sanitization.

Parses HTML-returning tool output with ``lxml``, strips hidden
injection vectors (scripts, styles, hidden elements), and detects
render-gap attacks where rendered text differs substantially from
raw visible HTML content.

This is a standalone post-processor called from ``ToolInvoker``,
not a middleware.
"""

import re
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import compare_ci
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.tool import (
    TOOL_HTML_PARSE_ERROR,
    TOOL_HTML_PARSE_GAP_DETECTED,
    TOOL_HTML_PARSE_XXE_DETECTED,
)

logger = get_logger(__name__)

# Patterns that indicate the content is likely HTML.
_HTML_TAG_PATTERN = re.compile(r"<[a-zA-Z][^>]*>")

# CSS patterns for hidden elements.
_HIDDEN_STYLE_PATTERNS = (
    re.compile(r"display\s*:\s*none", re.IGNORECASE),
    re.compile(r"visibility\s*:\s*hidden", re.IGNORECASE),
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

# Pre-parse rejection patterns for XXE.
#
# ``<!DOCTYPE foo SYSTEM "...">`` and the PUBLIC variant load external
# entities which can reach internal network or filesystem resources.
# ``<!ENTITY>`` declarations (any form) enable billion-laughs expansion
# and reference to external entities.  The regexes are case-insensitive
# and intentionally loose: any match triggers a safe-empty fallback,
# so a false positive only loses sanitisation of that one tool
# response, not a security property.
_EXTERNAL_DOCTYPE_RE: Final[re.Pattern[str]] = re.compile(
    r"<!DOCTYPE[^>]*\b(SYSTEM|PUBLIC)\b",
    re.IGNORECASE,
)
_ENTITY_DECL_RE: Final[re.Pattern[str]] = re.compile(
    r"<!ENTITY\b",
    re.IGNORECASE,
)
# HTML comments are stripped from the pre-scan copy so a DOCTYPE
# mentioned inside a comment does not trigger a false positive.
_HTML_COMMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"<!--.*?-->",
    re.DOTALL,
)


class XXEDetectedError(
    ValueError,
):  # lint-allow: domain-error-hierarchy -- caught by HTMLParseGuard.sanitize
    """Pre-parse detection of an XXE payload.

    Subclass of ``ValueError``. :meth:`HTMLParseGuard.sanitize` catches
    this explicitly (ahead of its generic ``except Exception`` branch)
    and returns a safe-empty :class:`HTMLSanitizeResult` so the XXE
    rejection event is not double-emitted.

    ``is_retryable = False`` so the resilience layer's retry-classifier
    (see ``BaseCompletionProvider`` / ``api/exception_handlers.py``)
    never retries on an XXE-detected payload -- retrying a malicious
    DOCTYPE buys nothing and wastes a real call.
    """

    is_retryable = False


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
        except Exception as exc:
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

    def _sanitize_html(self, raw: str) -> HTMLSanitizeResult:
        """Parse and sanitize HTML content using lxml.

        Returns:
            Result of type ``HTMLSanitizeResult``.
        """
        doc = _parse_html_safely(raw)
        # Capture original text before stripping (single parse).
        original_text = doc.text_content().strip()  # type: ignore[attr-defined]
        stripped_count = self._strip_dangerous_elements(doc)
        cleaned_text = doc.text_content().strip()  # type: ignore[attr-defined]
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
    def _strip_event_handlers(doc: Any) -> int:
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
    def _strip_dangerous_elements(doc: Any) -> int:
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
    def _strip_hidden_elements(doc: Any) -> int:
        """Strip elements hidden via attributes or CSS.

        Returns:
            Result of type ``int``.
        """
        elements_to_drop: list[object] = []
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
                element.drop_tree()  # type: ignore[attr-defined]
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


def _parse_html_safely(raw: str) -> Any:
    """Parse *raw* HTML with explicit XXE and entity-expansion defences.

    Replaces a bare ``lxml.html.fromstring`` call, which would
    otherwise allow XXE / billion-laughs attacks against operator
    input.

    Pipeline:

    1. Strip HTML comments from a local copy before the XXE pre-scan
       so a ``<!DOCTYPE ...>`` inside a ``<!-- ... -->`` block does not
       trigger a false positive.
    2. Reject any external DOCTYPE (``SYSTEM`` / ``PUBLIC``
       identifiers) or internal ``<!ENTITY>`` declaration by raising
       :class:`XXEDetectedError`. Callers catch this via the ``except
       Exception`` branch in :meth:`HTMLParseGuard.sanitize` which
       returns a safe-empty result.
    3. Parse with a module-scope :class:`lxml.html.HTMLParser`
       configured with ``no_network=True``, ``recover=True``,
       ``remove_blank_text=True``, and ``huge_tree=False``,
       belt-and-braces in case a novel payload slips past the
       pre-scan.  (``resolve_entities`` and ``load_dtd`` are
       ``XMLParser``-only knobs; see :func:`_build_safe_parser` for
       the rationale.)

    Args:
        raw: Raw (potentially attacker-controlled) HTML string.

    Returns:
        Parsed root ``lxml`` element.

    Raises:
        XXEDetectedError: If the payload carries an external DOCTYPE
            or any entity declaration.
    """
    # Strip comments before the XXE scan.  The parser itself still
    # removes comments from the tree later.
    scan_source = _HTML_COMMENT_RE.sub("", raw)
    if _EXTERNAL_DOCTYPE_RE.search(scan_source):
        logger.warning(
            TOOL_HTML_PARSE_XXE_DETECTED,
            reason="external_doctype",
            content_length=len(raw),
        )
        msg = "external DOCTYPE (SYSTEM/PUBLIC) detected; refusing to parse"
        raise XXEDetectedError(msg)
    if _ENTITY_DECL_RE.search(scan_source):
        logger.warning(
            TOOL_HTML_PARSE_XXE_DETECTED,
            reason="entity_declaration",
            content_length=len(raw),
        )
        msg = "ENTITY declaration detected; refusing to parse"
        raise XXEDetectedError(msg)

    from lxml import html as lxml_html  # noqa: PLC0415

    # Use the lxml.html fromstring so the returned element supports
    # ``text_content()`` / ``drop_tree()`` which the sanitiser relies
    # on. Pass the shared safe parser explicitly so our no-network +
    # huge_tree guards apply.
    return lxml_html.fromstring(raw, parser=_SAFE_PARSER)


def _build_safe_parser() -> Any:
    """Build the shared ``HTMLParser`` used by :func:`_parse_html_safely`.

    ``no_network=True`` blocks external resource loads (the primary
    XXE vector); ``huge_tree=False`` caps entity expansion; ``recover``
    keeps existing sanitiser behaviour on malformed input.

    Uses :class:`lxml.html.HTMLParser` rather than
    :class:`lxml.etree.HTMLParser` so parsed elements carry the
    ``HtmlElement`` API (``text_content``, ``drop_tree``, etc.) the
    sanitiser depends on.

    Note: ``resolve_entities`` / ``load_dtd`` are ``XMLParser`` knobs,
    not valid on ``HTMLParser``.  lxml's HTML parser does not resolve
    DTDs or external entities by default, so our pre-parse DOCTYPE /
    ENTITY rejection in :func:`_parse_html_safely` carries the
    defence here rather than a parser flag.

    Returns:
        Result of type ``Any``.
    """
    from lxml import html as lxml_html  # noqa: PLC0415

    return lxml_html.HTMLParser(
        no_network=True,
        remove_blank_text=True,
        recover=True,
        huge_tree=False,
    )


_SAFE_PARSER: Final[Any] = _build_safe_parser()
"""Module-scope HTML parser with XXE / entity-expansion defences.

Reused across calls to avoid re-building lxml state on every
``sanitize()`` invocation.
"""
