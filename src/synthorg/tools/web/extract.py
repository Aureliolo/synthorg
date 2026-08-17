# module-kind: code
"""HTML to markdown extraction shared by every ``web_fetch`` rung.

Boilerplate removal runs through ``trafilatura``: navigation, cookie banners
and footers are dropped while headings, fenced code, tables and links survive,
which is the shape an API reference has to keep to be worth reading.

The same pass runs for the local and rendered rungs, so a page fetched two
ways yields the same markdown and a comparison between rungs measures the
fetch, not the extractor.
"""

from configparser import ConfigParser
from typing import Final

import trafilatura
from pydantic import BaseModel, ConfigDict
from trafilatura.settings import use_config

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.web import WEB_FETCH_EXTRACT_FAILED

logger = get_logger(__name__)

_PARAGRAPH_BREAK: Final[str] = "\n\n"
_MIN_BOUNDARY_RATIO: Final[float] = 0.6
_NO_MINIMUM: Final[str] = "1"


def _build_config() -> ConfigParser:
    """Extraction config that keeps structure on a short page.

    Below ``MIN_EXTRACTED_SIZE`` (250 by default) the extractor throws away its
    structured result and salvages plain text instead, which silently strips
    every heading, fenced code block and link. A short API reference, a
    changelog entry and a single spec section all sit under that threshold, so
    the default loses formatting exactly where it is worth most.

    The size heuristics exist to decide whether a page was worth extracting at
    all; that decision belongs to the caller here, which reports an empty read
    honestly rather than salvaging it into unstructured text.

    Returns:
        The tuned extractor configuration.
    """
    config = use_config()
    config.set("DEFAULT", "MIN_EXTRACTED_SIZE", _NO_MINIMUM)
    config.set("DEFAULT", "MIN_OUTPUT_SIZE", _NO_MINIMUM)
    return config


_CONFIG: Final = _build_config()
TRUNCATION_MARKER: Final[str] = (
    "\n\n[truncated: the page exceeds the per-fetch budget. Narrow the request"
    " by fetching a more specific URL, or raise tools.web_fetch_max_characters.]"
)


class ExtractedDocument(BaseModel):
    """Markdown extracted from one HTML document.

    Attributes:
        markdown: The extracted main content, empty when nothing survived.
        title: Document title, empty when the page declares none.
        truncated: Whether the markdown was cut to fit the character budget.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    markdown: str
    title: str = ""
    truncated: bool = False


def truncate_at_block(text: str, budget: int) -> tuple[str, bool]:
    """Cut *text* to *budget* characters, preferring a paragraph boundary.

    A mid-sentence cut strands a half-written code fence, so the cut walks back
    to the last paragraph break. It only walks back while the boundary keeps
    most of the budget: on a page that is one long block, honouring the
    boundary would discard nearly everything, and a hard cut reads better than
    an empty result.

    Returns:
        The possibly-cut text, and whether a cut happened.
    """
    if len(text) <= budget:
        return text, False
    window = text[:budget]
    boundary = window.rfind(_PARAGRAPH_BREAK)
    if boundary >= int(budget * _MIN_BOUNDARY_RATIO):
        return window[:boundary], True
    return window, True


def extract_markdown(
    html: str,
    *,
    char_budget: int,
    url: str | None = None,
) -> ExtractedDocument:
    """Extract *html* to markdown, capped at *char_budget* characters.

    Args:
        html: The raw HTML document.
        char_budget: Maximum characters of markdown to return.
        url: Source URL, used by the extractor to resolve relative links.

    Returns:
        The extracted document. ``markdown`` is empty when the extractor found
        no main content, which the caller reports rather than papering over:
        an empty read and a read that failed are different answers and the
        agent picks its next rung from which one it got.

    Raises:
        ValueError: If ``char_budget`` is not positive.
    """
    if char_budget <= 0:
        msg = f"char_budget must be positive, got {char_budget}"
        raise ValueError(msg)
    try:
        markdown = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_tables=True,
            include_links=True,
            include_formatting=True,
            with_metadata=False,
            config=_CONFIG,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            WEB_FETCH_EXTRACT_FAILED,
            reason="extractor_error",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ExtractedDocument(markdown="")
    if not markdown:
        return ExtractedDocument(markdown="", title=_title_of(html))
    body, truncated = truncate_at_block(markdown, char_budget)
    if truncated:
        body = f"{body}{TRUNCATION_MARKER}"
    return ExtractedDocument(
        markdown=body,
        title=_title_of(html),
        truncated=truncated,
    )


def _title_of(html: str) -> str:
    """Read the document title, or an empty string when unreadable.

    Returns:
        The title, stripped; empty when the page declares none.
    """
    try:
        meta = trafilatura.extract_metadata(html)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.debug(
            WEB_FETCH_EXTRACT_FAILED,
            reason="metadata_error",
            error_type=type(exc).__name__,
        )
        return ""
    title = meta.title if meta is not None else None
    return title.strip() if isinstance(title, str) else ""


__all__ = [
    "TRUNCATION_MARKER",
    "ExtractedDocument",
    "extract_markdown",
    "truncate_at_block",
]
