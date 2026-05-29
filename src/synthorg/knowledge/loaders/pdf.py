"""PDF source loader built on pdfplumber.

Emits one :class:`RawUnit` per page (``PDF_PAGE``) carrying the page text
and a :class:`PdfLocator` with the 1-indexed page number, so citations
resolve to the exact page. Word-level bounding boxes (pdfplumber exposes
them via ``page.extract_words``) are a planned refinement; today the
locator carries the page number and per-chunk char offsets, with
``bbox`` left unset.

pdfplumber is optional (the ``synthorg[knowledge]`` extra); its absence
raises :class:`KnowledgeDependencyError`. Parsing runs in a worker thread
because pdfplumber is synchronous and CPU-bound.
"""

import asyncio
import builtins
from typing import TYPE_CHECKING, Any

from synthorg.core.enums import ContentKind
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.errors import (
    KnowledgeDependencyError,
    KnowledgeIngestError,
    KnowledgeSourceUnavailableError,
)
from synthorg.knowledge.models import PdfLocator, RawDocument, RawUnit
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.knowledge import (
    KNOWLEDGE_LOAD_FAILED,
    KNOWLEDGE_SOURCE_LOADED,
)
from synthorg.versioning.hashing import compute_text_hash

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

    from synthorg.knowledge.models import KnowledgeSource

    # A PDF opener takes a path and returns a context manager yielding a
    # pdfplumber-like object exposing ``.pages``. Aliased here (type-only)
    # so tests can inject a fake without satisfying a strict Protocol.
    PdfOpener = Callable[[str], AbstractContextManager[Any]]

logger = get_logger(__name__)


def _default_opener(path: str) -> AbstractContextManager[Any]:
    """Open *path* with pdfplumber, lazily importing the optional dep.

    Returns:
        A context manager yielding a pdfplumber-like object exposing
        ``.pages``.

    Raises:
        KnowledgeDependencyError: When ``pdfplumber`` is not installed.
    """
    try:
        import pdfplumber  # noqa: PLC0415
    except ImportError as exc:
        msg = (
            "PDF ingestion needs pdfplumber. Install with "
            "`pip install synthorg[knowledge]`."
        )
        raise KnowledgeDependencyError(msg) from exc
    # pdfplumber ships no type stubs, so the open() result is typed Any;
    # the context manager interface is well-documented and stable.
    opened: AbstractContextManager[Any] = pdfplumber.open(path)
    return opened


class PdfLoader:
    """Loads a PDF into one page-addressable unit per page."""

    __slots__ = ("_opener",)

    def __init__(self, *, opener: PdfOpener | None = None) -> None:
        self._opener = opener if opener is not None else _default_opener

    async def load(self, source: KnowledgeSource) -> RawDocument:
        """Parse the PDF at ``source.uri`` into per-page units.

        Returns:
            A ``RawDocument`` with one page-addressable unit per PDF page.
        """
        document = await asyncio.to_thread(self._load_sync, source)
        logger.debug(
            KNOWLEDGE_SOURCE_LOADED,
            source_id=source.source_id,
            source_type=source.source_type.value,
            unit_count=len(document.units),
        )
        return document

    def _load_sync(self, source: KnowledgeSource) -> RawDocument:
        units: list[RawUnit] = []
        page_texts: list[str] = []
        try:
            with self._opener(source.uri) as pdf:
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    units.append(
                        RawUnit(
                            text=text,
                            locator=PdfLocator(
                                page=int(page.page_number),
                                bbox=None,
                                char_start=0,
                                char_end=len(text),
                            ),
                            content_kind=ContentKind.PDF_PAGE,
                        )
                    )
                    page_texts.append(text)
        except KnowledgeDependencyError:
            raise
        except builtins.MemoryError, RecursionError:
            raise
        except (FileNotFoundError, PermissionError, IsADirectoryError) as exc:
            msg = f"Failed to access PDF source {source.source_id!r}"
            logger.warning(
                KNOWLEDGE_LOAD_FAILED,
                source_id=source.source_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise KnowledgeSourceUnavailableError(msg) from exc
        except Exception as exc:
            msg = f"Failed to parse PDF source {source.source_id!r}"
            logger.warning(
                KNOWLEDGE_LOAD_FAILED,
                source_id=source.source_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise KnowledgeIngestError(msg) from exc
        return RawDocument(
            source_id=source.source_id,
            source_type=source.source_type,
            uri=source.uri,
            title=NotBlankStr(source.title),
            content_hash=compute_text_hash("\n\f\n".join(page_texts)),
            units=tuple(units),
        )
