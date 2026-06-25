"""Citation binding and validation for knowledge synthesis.

Resolves the reference ids a synthesised claim cites back to the retrieved
:class:`~synthorg.knowledge.models.KnowledgeHit` set, returning each hit's
:class:`~synthorg.knowledge.models.Citation`. A claim citing an unknown
reference id is a synthesis failure: the binder raises rather than emit an
unverifiable answer.
"""

from collections.abc import Mapping

from synthorg.knowledge.errors import KnowledgeSynthesisError
from synthorg.knowledge.models import Citation, KnowledgeHit
from synthorg.observability import get_logger
from synthorg.observability.events.knowledge import KNOWLEDGE_SYNTHESIS_FAILED

logger = get_logger(__name__)


class KnowledgeCitationBinder:
    """Resolves cited reference ids to validated chunk citations."""

    __slots__ = ()

    def resolve(
        self,
        ref_ids: tuple[str, ...],
        hits_by_ref: Mapping[str, KnowledgeHit],
    ) -> tuple[Citation, ...]:
        """Return citations for *ref_ids*, preserving order and uniqueness.

        Args:
            ref_ids: Reference ids a claim cites.
            hits_by_ref: Retrieved hits keyed by their assigned ``ref_id``.

        Returns:
            One citation per distinct, resolvable reference id.

        Raises:
            KnowledgeSynthesisError: If any reference id is unknown or the
                claim cites no sources.
        """
        if not ref_ids:
            msg = "claim cited no sources"
            logger.warning(
                KNOWLEDGE_SYNTHESIS_FAILED,
                reason="claim_cited_no_sources",
                error_type=KnowledgeSynthesisError.__name__,
            )
            raise KnowledgeSynthesisError(msg)
        citations: list[Citation] = []
        seen: set[str] = set()
        for ref_id in ref_ids:
            if ref_id in seen:
                continue
            hit = hits_by_ref.get(ref_id)
            if hit is None:
                msg = f"claim cited unknown source ref_id {ref_id!r}"
                logger.warning(
                    KNOWLEDGE_SYNTHESIS_FAILED,
                    reason="unknown_source_ref_id",
                    ref_id=ref_id,
                    error_type=KnowledgeSynthesisError.__name__,
                )
                raise KnowledgeSynthesisError(msg)
            seen.add(ref_id)
            citations.append(hit.citation)
        return tuple(citations)
