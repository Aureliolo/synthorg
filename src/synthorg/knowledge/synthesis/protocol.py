"""Knowledge synthesiser protocol.

A synthesiser turns the retrieved, cited chunks into a
:class:`~synthorg.knowledge.models.KnowledgeAnswer` whose every claim cites at
least one chunk. Implementations return the answer plus any USD cost incurred.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.knowledge.models import KnowledgeAnswer, KnowledgeHit


@runtime_checkable
class Synthesizer(Protocol):
    """Produces a citation-backed answer from retrieved chunks."""

    async def synthesize(
        self,
        *,
        query: NotBlankStr,
        hits: tuple[KnowledgeHit, ...],
        project_id: NotBlankStr | None = None,
    ) -> tuple[KnowledgeAnswer, float]:
        """Return a cited answer and the USD cost of producing it.

        Args:
            query: The natural-language question to answer.
            hits: Retrieved, cited chunks the answer may cite.
            project_id: Optional project scope for cost attribution.

        Returns:
            A ``(answer, cost)`` pair. Every claim resolves to at least one
            of *hits*; an unsourced claim raises
            :class:`~synthorg.knowledge.errors.KnowledgeSynthesisError`.
        """
        ...
