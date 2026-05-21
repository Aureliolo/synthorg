"""Source-loader selection by :class:`SourceType`.

``PDF`` and ``DESIGN_DOC`` use the pdfplumber loader, ``WEB`` the
sanitising web loader (requires an :class:`HtmlFetcher`), ``REPO`` the
filesystem walker, and ``TICKET`` the governed-connection loader.
"""

from typing import TYPE_CHECKING

from synthorg.core.enums import SourceType
from synthorg.knowledge.errors import KnowledgeValidationError
from synthorg.knowledge.loaders.pdf import PdfLoader
from synthorg.knowledge.loaders.repo import RepoLoader
from synthorg.knowledge.loaders.ticket import TicketLoader
from synthorg.knowledge.loaders.web import WebLoader

if TYPE_CHECKING:
    from synthorg.knowledge.loaders.protocol import SourceLoader
    from synthorg.knowledge.loaders.web import HtmlFetcher


def build_source_loader(
    source_type: SourceType,
    *,
    html_fetcher: HtmlFetcher | None = None,
) -> SourceLoader:
    """Return the loader for *source_type*.

    Args:
        source_type: The source origin to load.
        html_fetcher: Required for ``WEB`` sources (governed HTTP egress).

    Raises:
        KnowledgeValidationError: ``WEB`` requested without a fetcher.
    """
    if source_type in (SourceType.PDF, SourceType.DESIGN_DOC):
        return PdfLoader()
    if source_type is SourceType.REPO:
        return RepoLoader()
    if source_type is SourceType.WEB:
        if html_fetcher is None:
            msg = "WEB source loading requires an html_fetcher"
            raise KnowledgeValidationError(msg)
        return WebLoader(fetcher=html_fetcher)
    return TicketLoader()
