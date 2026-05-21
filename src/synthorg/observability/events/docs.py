"""Living-documentation engine event constants.

Structured event names emitted by ``synthorg.docs_engine`` services.
Loggers from this module always pass these as the first positional
``event`` argument and add typed kwargs alongside (per the project's
structured-logging convention).
"""

from typing import Final

DOC_WRITTEN: Final[str] = "docs.doc.written"
DOC_WRITE_FAILED: Final[str] = "docs.doc.write_failed"
DOC_INDEXED: Final[str] = "docs.doc.indexed"
DOC_INDEX_FAILED: Final[str] = "docs.doc.index_failed"
DOC_RETRIEVED: Final[str] = "docs.doc.retrieved"
DOC_COMMIT_PUSHED: Final[str] = "docs.doc.commit_pushed"
DOC_NOT_FOUND: Final[str] = "docs.doc.not_found"
DOC_VALIDATION_FAILED: Final[str] = "docs.doc.validation_failed"
DOC_SLUG_DERIVED: Final[str] = "docs.slug.derived"
DOC_SEARCH_START: Final[str] = "docs.search.start"
DOC_SEARCH_COMPLETE: Final[str] = "docs.search.complete"
DOC_SEARCH_FAILED: Final[str] = "docs.search.failed"
DOC_HISTORY_READ: Final[str] = "docs.history.read"
DOC_FACADE_FANOUT: Final[str] = "docs.facade.fanout"
DOC_FACADE_FANOUT_FAILED: Final[str] = "docs.facade.fanout_failed"
