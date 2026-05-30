"""Web page source loader.

Fetches a page through an injected :class:`HtmlFetcher` (the factory
wires one built on the governed HTTP path with SSRF + DNS pinning),
sanitises the HTML with :class:`HTMLParseGuard` to strip scripts and
hidden-injection vectors, and emits one ``DOCUMENT`` :class:`RawUnit`
with a :class:`WebLocator`. Decoupling the fetcher keeps the loader
unit-testable without real network egress.
"""

import asyncio
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import ContentKind
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.errors import (
    KnowledgeIngestError,
    KnowledgeSourceUnavailableError,
)
from synthorg.knowledge.models import RawDocument, RawUnit, WebLocator
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.knowledge import (
    KNOWLEDGE_LOAD_FAILED,
    KNOWLEDGE_SOURCE_LOADED,
)
from synthorg.tools.html_parse_guard import HTMLParseGuard
from synthorg.versioning.hashing import compute_text_hash

if TYPE_CHECKING:
    from synthorg.knowledge.models import KnowledgeSource

logger = get_logger(__name__)


@runtime_checkable
class HtmlFetcher(Protocol):
    """Fetches a URL and returns its raw HTML (governed network egress)."""

    async def fetch(self, url: str) -> str:
        """Return the raw HTML at *url*.

        Raises:
            Exception: Any transport failure; the loader maps it to
                :class:`KnowledgeSourceUnavailableError`.
        """
        ...


class WebLoader:
    """Loads a web page into a single sanitised document unit."""

    __slots__ = ("_fetcher", "_guard")

    def __init__(
        self,
        *,
        fetcher: HtmlFetcher,
        guard: HTMLParseGuard | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._guard = guard if guard is not None else HTMLParseGuard()

    async def load(self, source: KnowledgeSource) -> RawDocument:
        """Fetch + sanitise ``source.uri`` into one document unit.

        Returns:
            A ``RawDocument`` with a single sanitised document unit (empty
            of units when the cleaned text is blank).

        Raises:
            KnowledgeSourceUnavailableError: When the page fetch fails.
            KnowledgeIngestError: When HTML sanitisation fails.
        """
        try:
            html = await self._fetcher.fetch(source.uri)
        except Exception as exc:
            reraise_critical(exc)
            msg = f"Failed to fetch web source {source.source_id!r}"
            logger.warning(
                KNOWLEDGE_LOAD_FAILED,
                source_id=source.source_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise KnowledgeSourceUnavailableError(msg) from exc
        try:
            cleaned = (await asyncio.to_thread(self._guard.sanitize, html)).cleaned
        except Exception as exc:
            reraise_critical(exc)
            msg = f"Failed to sanitise web source {source.source_id!r}"
            logger.warning(
                KNOWLEDGE_LOAD_FAILED,
                source_id=source.source_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise KnowledgeIngestError(msg) from exc
        units = (
            (
                RawUnit(
                    text=cleaned,
                    locator=WebLocator(
                        url=NotBlankStr(source.uri),
                        char_start=0,
                        char_end=len(cleaned),
                    ),
                    content_kind=ContentKind.DOCUMENT,
                ),
            )
            if cleaned.strip()
            else ()
        )
        logger.debug(
            KNOWLEDGE_SOURCE_LOADED,
            source_id=source.source_id,
            source_type=source.source_type.value,
            unit_count=len(units),
        )
        return RawDocument(
            source_id=source.source_id,
            source_type=source.source_type,
            uri=source.uri,
            title=NotBlankStr(source.title),
            content_hash=compute_text_hash(cleaned),
            units=units,
        )
