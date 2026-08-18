# module-kind: code
"""Parsing attacker-controlled HTML without letting it reach outward.

Split from ``html_parse_guard`` along the seam between two jobs: HOW hostile
markup is turned into a tree at all (here) and WHAT gets stripped from that
tree once it exists (there). The guard is the only consumer that strips, but
every caller that parses a fetched page needs this half, including the web
extractor, which builds its tree inside a third-party library and so cannot be
handed a parser of ours.
"""

import re
import threading
from typing import Final

from lxml.html import HtmlElement, HTMLParser

from synthorg.observability import get_logger
from synthorg.observability.events.tool import TOOL_HTML_PARSE_XXE_DETECTED

logger = get_logger(__name__)

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


def reject_xxe_constructs(raw: str) -> None:
    """Refuse HTML carrying an external DOCTYPE or an entity declaration.

    The pre-scan, kept apart from :func:`parse_html_safely` so every parser
    fed attacker-controlled HTML shares one definition of what is refused.
    ``lxml``'s HTML mode resolves neither DTDs nor external entities, and a
    third-party extractor builds its own parser out of reach of ours, so this
    scan is what actually carries the defence for those callers rather than a
    flag on a parser we do not own.

    Comments are stripped from a local copy first, so a ``<!DOCTYPE ...>``
    quoted inside ``<!-- ... -->`` does not trigger a false positive.

    Args:
        raw: Raw (potentially attacker-controlled) HTML string.

    Raises:
        XXEDetectedError: If the payload carries an external DOCTYPE
            (``SYSTEM`` / ``PUBLIC``) or any ``<!ENTITY>`` declaration.
    """
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


def parse_html_safely(raw: str) -> HtmlElement:
    """Parse *raw* HTML with explicit XXE and entity-expansion defences.

    Replaces a bare ``lxml.html.fromstring`` call, which would
    otherwise allow XXE / billion-laughs attacks against operator
    input.

    Pipeline:

    1. Reject an external DOCTYPE or entity declaration via
       :func:`reject_xxe_constructs`. :meth:`HTMLParseGuard.sanitize`
       catches that in its own ``except XXEDetectedError`` branch,
       which returns a safe-empty result without re-logging: the
       pre-scan already emitted the event, and the generic branch
       below it would attach a second one to the attacker's payload.
    2. Parse with a module-scope :class:`lxml.html.HTMLParser`
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
    reject_xxe_constructs(raw)

    from lxml import html as lxml_html  # noqa: PLC0415

    # Use the lxml.html fromstring so the returned element supports
    # ``text_content()`` / ``drop_tree()`` which the sanitiser relies
    # on. Pass this thread's safe parser explicitly so our no-network +
    # huge_tree guards apply.
    return lxml_html.fromstring(raw, parser=_SAFE_PARSERS.parser)


def _build_safe_parser() -> HTMLParser:
    """Build the shared ``HTMLParser`` used by :func:`parse_html_safely`.

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
    ENTITY rejection in :func:`parse_html_safely` carries the
    defence here rather than a parser flag.

    Returns:
        Result of type ``HTMLParser``.
    """
    from lxml import html as lxml_html  # noqa: PLC0415

    return lxml_html.HTMLParser(
        no_network=True,
        remove_blank_text=True,
        recover=True,
        huge_tree=False,
    )


class _ThreadParser(threading.local):
    """One HTML parser per thread, built the first time that thread parses.

    Reused across calls on one thread to avoid re-building lxml state on every
    invocation, but never SHARED between threads: lxml serialises access to a
    parser instance, and extraction runs on worker threads
    (:func:`synthorg.tools.web.extract.extract_markdown` dispatches through
    ``asyncio.to_thread``), so a single instance would make two agents reading
    two pages take turns inside the parser. The object is cheap; the
    contention is not.

    Subclassing :class:`threading.local` rather than holding a bare one is
    what makes the per-thread build automatic: ``__init__`` runs once per
    thread on first access, so there is no lazily-initialised slot for a
    caller to read before it is filled.
    """

    def __init__(self) -> None:
        """Build this thread's parser."""
        self.parser: HTMLParser = _build_safe_parser()


_SAFE_PARSERS = _ThreadParser()


__all__ = [
    "XXEDetectedError",
    "parse_html_safely",
    "reject_xxe_constructs",
]
