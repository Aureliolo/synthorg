"""Source-loader protocol.

A loader reads a source's bytes and produces a :class:`RawDocument`:
ordered structural units (PDF pages, repo files, web text, ticket
comments) plus the content hash of the whole source, which lets the
service short-circuit a re-ingest when nothing changed.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.knowledge.models import KnowledgeSource, RawDocument


@runtime_checkable
class SourceLoader(Protocol):
    """Loads a knowledge source into a :class:`RawDocument`."""

    async def load(self, source: KnowledgeSource) -> RawDocument:
        """Read *source* and return its structural units.

        Raises:
            KnowledgeSourceUnavailableError: The source cannot be reached.
            KnowledgeIngestError: The bytes cannot be parsed.
            KnowledgeDependencyError: A required optional dep is missing.
        """
        ...
