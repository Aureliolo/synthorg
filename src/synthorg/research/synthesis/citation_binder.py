"""Citation binding and validation.

Resolves the reference ids a synthesised claim cites back to the retained
:class:`RetrievedItem` set, building a :class:`ResearchCitation` for each.
A claim citing an unknown reference id is a synthesis failure: the binder
raises rather than emit an unverifiable report.
"""

from typing import TYPE_CHECKING

from synthorg.research.errors import ResearchSynthesisError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.research.models import ResearchCitation, RetrievedItem


class CitationBinder:
    """Resolves cited reference ids to validated citations."""

    __slots__ = ()

    def resolve(
        self,
        ref_ids: tuple[str, ...],
        items_by_ref: Mapping[str, RetrievedItem],
    ) -> tuple[ResearchCitation, ...]:
        """Return citations for *ref_ids*, preserving order and uniqueness.

        Args:
            ref_ids: Reference ids a claim cites.
            items_by_ref: Retained items keyed by ``ref_id``.

        Returns:
            One citation per distinct, resolvable reference id.

        Raises:
            ResearchSynthesisError: If any reference id is unknown or the
                claim cites no sources.
        """
        if not ref_ids:
            msg = "claim cited no sources"
            raise ResearchSynthesisError(msg)
        citations: list[ResearchCitation] = []
        seen: set[str] = set()
        for ref_id in ref_ids:
            if ref_id in seen:
                continue
            item = items_by_ref.get(ref_id)
            if item is None:
                msg = f"claim cited unknown source ref_id {ref_id!r}"
                raise ResearchSynthesisError(msg)
            seen.add(ref_id)
            citations.append(item.citation)
        return tuple(citations)
