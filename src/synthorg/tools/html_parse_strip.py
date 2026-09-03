"""The DOM strip the HTML parse guard applies to a parsed document.

What a renderer never shows a human but a model reads in full is the
substance of an indirect prompt injection, so the guard drops it before the
text reaches an agent: script and style bodies, comments, event handlers,
and every element hidden by attribute or by CSS. The guard owns the verdict
(how much was hidden, whether that crosses the gap threshold); this module
owns only the mutation and the measure.
"""

import re
from typing import Final

from lxml import etree
from lxml.html import HtmlElement

from synthorg.core.normalization import compare_ci

# CSS spellings of a hidden element, as ``(value, terminated)``: the value
# is what a style attribute has to say, and ``terminated`` is whether the
# strip reads it only up to a ``;`` or the end of the attribute, because
# ``font-size: 0.5em`` and ``opacity: 0.4`` open with the same characters as
# their hidden spellings and are not hidden.
#
# "Hidden" has more spellings than the obvious two. Each spelling below was
# confirmed to carry injected text through the extractor and into a model's
# context: a zero font size renders nothing, and a large negative offset parks
# the element outside the viewport while leaving it in the document.
#
# Deliberately NOT matched: text coloured to match its background. Deciding
# that needs the computed cascade and a colour comparison, neither of which a
# per-element attribute scan has, and a guess would strip legitimately styled
# prose. It is the one hiding technique left standing here.
_HIDDEN_STYLE_VALUES: Final[tuple[tuple[str, bool], ...]] = (
    (r"display\s*:\s*none", False),
    (r"visibility\s*:\s*hidden", False),
    (r"font-size\s*:\s*0(?:\.0*)?\s*(?:px|em|rem|pt|%)?", True),
    (r"(?:left|top|right|bottom)\s*:\s*-\d{4,}\s*(?:px|em|rem|pt)", False),
    (r"text-indent\s*:\s*-\d{4,}\s*(?:px|em|rem|pt)", False),
    (r"clip\s*:\s*rect\s*\(\s*0[a-z%]*[\s,]+0[a-z%]*[\s,]", False),
    (r"opacity\s*:\s*0(?:\.0*)?", True),
)
_STYLE_TERMINATOR: Final[str] = r"\s*(?:;|$)"

_HIDDEN_STYLE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(value + (_STYLE_TERMINATOR if terminated else ""), re.IGNORECASE)
    for value, terminated in _HIDDEN_STYLE_VALUES
)

# What a FRAGMENT has to carry for the guard to judge it at all, derived
# from the same spellings the strip removes so the trigger can never know
# less than the strip does: the boolean ``hidden`` attribute, and every
# hidden style. A value that a component literal can also spell
# (``opacity: 0}}``) has to end the way a style attribute ends, at a
# terminator, a closing quote or the line, so JSX stays out.
HIDDEN_CONSTRUCT_TRIGGER: Final[str] = r"\shidden(?=[\s>/=])|" + "|".join(
    f"(?:{value})" + (r"""\s*(?:;|['"]|$)""" if terminated else "")
    for value, terminated in _HIDDEN_STYLE_VALUES
)

# Tags to strip entirely (content and all).
_STRIP_TAGS: Final[frozenset[str]] = frozenset(
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
_EVENT_HANDLER_PREFIXES: Final[frozenset[str]] = frozenset(
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

_SVG_SCRIPT_TAG: Final[str] = "{http://www.w3.org/2000/svg}script"

# A comment as TEXT: what a renderer never shows from ``<!--`` to the closing
# ``-->``, or to the end of the input when nothing closes it, which is how a
# renderer reads an unterminated one.
_COMMENT_PATTERN: Final[re.Pattern[str]] = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)


def strip_dangerous_elements(doc: HtmlElement) -> int:
    """Strip scripts, styles, comments, handlers and hidden elements in place.

    Returns:
        How many elements and attributes were removed.
    """
    stripped = 0

    # Every walk is materialised before the first drop: ``drop_tree`` on the
    # element a live iterator is standing on moves the iterator's next step
    # with it, and an adjacent match is skipped, which leaves the second of
    # two scripts in a row in the cleaned output.
    for tag in _STRIP_TAGS:
        for element in list(doc.iter(tag)):
            element.drop_tree()
            stripped += 1

    # A comment is text a renderer never shows, so it counts as hidden
    # content: a document whose only concealment is a comment is still
    # one that was rewritten.
    for comment in list(doc.iter(etree.Comment)):
        comment.drop_tree()
        stripped += 1

    for element in list(doc.iter(_SVG_SCRIPT_TAG)):
        element.drop_tree()
        stripped += 1

    stripped += _strip_event_handlers(doc)
    stripped += _strip_hidden_elements(doc)
    return stripped


def strip_comments(raw: str) -> tuple[str, int]:
    """Cut every HTML comment out of *raw* as text, touching nothing else.

    The DOM strip drops comments with everything else it removes; this is
    for a fragment that is not parsed at all, so what surrounds the comment
    comes back byte for byte.

    Returns:
        The text without its comments, and how many were cut.
    """
    return _COMMENT_PATTERN.subn("", raw)


def gap_ratio(original: str, cleaned: str) -> float:
    """The share of *original* that the strip removed.

    Returns:
        A ratio in ``[0, 1]``; an empty original reads as nothing hidden.
    """
    original_len = len(original) or 1
    hidden_len = max(0, original_len - len(cleaned))
    return min(hidden_len / original_len, 1.0)


def _strip_event_handlers(doc: HtmlElement) -> int:
    """Strip event handler attributes from every element.

    Returns:
        How many attributes were removed.
    """
    stripped = 0
    for element in doc.iter():
        if not isinstance(element.tag, str):
            continue
        for attr in list(element.attrib):
            if attr.lower() in _EVENT_HANDLER_PREFIXES:
                del element.attrib[attr]
                stripped += 1
    return stripped


def _strip_hidden_elements(doc: HtmlElement) -> int:
    """Strip elements hidden by attribute or by CSS.

    Returns:
        How many elements were dropped.
    """
    elements_to_drop: list[HtmlElement] = []
    for element in doc.iter():
        if not isinstance(element.tag, str):
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

    # Dropping an element detaches its subtree, so a hidden descendant of a
    # hidden ancestor has no parent by the time its turn comes.
    dropped = 0
    for element in elements_to_drop:
        if element.getparent() is not None:
            element.drop_tree()
            dropped += 1
    return dropped


__all__ = [
    "HIDDEN_CONSTRUCT_TRIGGER",
    "gap_ratio",
    "strip_comments",
    "strip_dangerous_elements",
]
