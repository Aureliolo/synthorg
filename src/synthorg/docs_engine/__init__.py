"""Living-documentation engine.

Per-project documentation store that is dual-purpose: (a) human-browsable
as a wiki in the dashboard, (b) chunked + embedded into the existing
hybrid-retrieval memory pipeline as a first-class RAG namespace. Status
reports and deliverables land here as living documents, versioned in the
project git workspace via the existing per-project push queue.

See ``docs/design/living-documentation.md`` for the architecture write-up
and ``docs/reference/conventions.md`` for the persistence-boundary rules
that constrain this engine (no SQL / sqlite / psycopg below this module).
"""

from synthorg.docs_engine.errors import (
    DocCommitError,
    DocIndexError,
    DocNotFoundError,
    DocValidationError,
    DocVersionConflictError,
)
from synthorg.docs_engine.models import (
    BulletListBlock,
    CodeBlock,
    DecisionBlock,
    DocBlock,
    DocChunk,
    DocMetadata,
    DocSearchHit,
    DocSummary,
    DocVersion,
    HeadingBlock,
    LinkBlock,
    LivingDocument,
    MetricBlock,
    ProseBlock,
)

# Note: ``chunker``, ``indexer``, ``writer``, ``service``, ``factory``,
# ``retrieval_facade`` and ``tool_factory`` are deliberately NOT
# re-exported from this package init. They depend on the persistence
# protocol (``synthorg.persistence.docs_protocol``), which in turn
# imports ``DocMetadata`` from ``synthorg.docs_engine.models``. Doing
# the imports here would create a partially-initialised module cycle
# during cold persistence boot; consumers import the concrete classes
# directly from their modules (cf. ``synthorg.persistence.factory``
# for the analogous pattern).

__all__ = [
    "BulletListBlock",
    "CodeBlock",
    "DecisionBlock",
    "DocBlock",
    "DocChunk",
    "DocCommitError",
    "DocIndexError",
    "DocMetadata",
    "DocNotFoundError",
    "DocSearchHit",
    "DocSummary",
    "DocValidationError",
    "DocVersion",
    "DocVersionConflictError",
    "HeadingBlock",
    "LinkBlock",
    "LivingDocument",
    "MetricBlock",
    "ProseBlock",
]
