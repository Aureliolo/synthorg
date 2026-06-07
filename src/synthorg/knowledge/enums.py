"""Knowledge substrate enumerations."""

from enum import StrEnum


class SourceType(StrEnum):
    """Origin of an ingested knowledge source.

    Drives ``SourceLoader`` selection in the knowledge substrate. A
    ``REPO`` source is a code tree, ``PDF`` / ``WEB`` / ``DESIGN_DOC`` are
    documents, and ``TICKET`` is an issue thread fetched through the
    governed external-API access tool.
    """

    PDF = "pdf"
    WEB = "web"
    REPO = "repo"
    TICKET = "ticket"
    DESIGN_DOC = "design_doc"


class ContentKind(StrEnum):
    """Structural kind of a loaded unit; drives chunker selection.

    ``CODE`` uses the AST-aware chunker, ``DOCUMENT`` the section-aware
    chunker, ``PDF_PAGE`` the page/region chunker, and ``TICKET_THREAD``
    the document chunker over comment text.
    """

    CODE = "code"
    DOCUMENT = "document"
    PDF_PAGE = "pdf_page"
    TICKET_THREAD = "ticket_thread"


class SourceStatus(StrEnum):
    """Ingestion lifecycle state of a knowledge source."""

    PENDING = "pending"
    INDEXED = "indexed"
    STALE = "stale"
    FAILED = "failed"
