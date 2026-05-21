"""Source-loader selection by :class:`SourceType`.

``PDF`` and ``DESIGN_DOC`` use the pdfplumber loader, ``WEB`` the
sanitising web loader (requires an :class:`HtmlFetcher`), ``REPO`` the
filesystem walker, and ``TICKET`` the governed ticket loader (requires
a :class:`TicketFetcher`). The fetcher seams carry the SSRF + DNS
pinning + credential-brokering guarantees that the loaders themselves
do not implement.
"""

from typing import TYPE_CHECKING

from synthorg.core.enums import SourceType
from synthorg.knowledge.errors import KnowledgeValidationError
from synthorg.knowledge.loaders.pdf import PdfLoader
from synthorg.knowledge.loaders.repo import RepoLoader
from synthorg.knowledge.loaders.ticket import TicketFetcher, TicketLoader
from synthorg.knowledge.loaders.web import HtmlFetcher, WebLoader

if TYPE_CHECKING:
    from synthorg.knowledge.loaders.protocol import SourceLoader


def build_source_loader(
    source_type: SourceType,
    *,
    html_fetcher: HtmlFetcher | None = None,
    ticket_fetcher: TicketFetcher | None = None,
) -> SourceLoader:
    """Return the loader for *source_type*.

    Args:
        source_type: The source origin to load.
        html_fetcher: Required for ``WEB`` sources. Must satisfy the
            :class:`HtmlFetcher` Protocol; this is the seam through
            which the SSRF + DNS-pinning guarantee is asserted at the
            boundary.
        ticket_fetcher: Required for ``TICKET`` sources. Must satisfy
            the :class:`TicketFetcher` Protocol; production wires one
            that routes through the governed external-API access path.

    Raises:
        KnowledgeValidationError: ``WEB`` requested without an
            ``html_fetcher`` or ``TICKET`` requested without a
            ``ticket_fetcher``.
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
    if ticket_fetcher is None:
        msg = (
            "TICKET source loading requires a ticket_fetcher routed "
            "through the governed external-API connection"
        )
        raise KnowledgeValidationError(msg)
    return TicketLoader(fetcher=ticket_fetcher)
