"""Replay retrieval source.

During replay, the persisted run's ``retrieved_items`` are the single
source of truth for retrieval: a :class:`ReplayRetrievalSource` serves the
recorded items for one source family by sub-query index, so a recorded run
reproduces identical retrieval without touching any external provider.
"""

from collections import defaultdict
from typing import TYPE_CHECKING

from synthorg.research.constants import RESEARCH_DEFAULT_PER_QUERY_LIMIT
from synthorg.research.enums import ResearchSourceType

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.research.models import RetrievedItem, SubQuery


class ReplayRetrievalSource:
    """Serves recorded items for one source family, keyed by sub-query."""

    __slots__ = ("_by_index", "_source_type")

    def __init__(
        self,
        *,
        source_type: ResearchSourceType,
        items: tuple[RetrievedItem, ...],
    ) -> None:
        self._source_type = source_type
        by_index: defaultdict[int, list[RetrievedItem]] = defaultdict(list)
        for item in items:
            if item.source_type is source_type:
                by_index[item.sub_query_index].append(item)
        self._by_index = {index: tuple(group) for index, group in by_index.items()}

    @property
    def source_type(self) -> ResearchSourceType:
        """The source family these recorded items belong to."""
        return self._source_type

    async def retrieve(
        self,
        sub_query: SubQuery,
        *,
        project_id: NotBlankStr | None = None,  # noqa: ARG002 -- protocol contract
        limit: int = RESEARCH_DEFAULT_PER_QUERY_LIMIT,  # noqa: ARG002 -- protocol contract
    ) -> tuple[RetrievedItem, ...]:
        """Return the recorded items for this sub-query index (ignores limit)."""
        return self._by_index.get(sub_query.index, ())


def build_replay_sources(
    items: tuple[RetrievedItem, ...],
) -> dict[ResearchSourceType, ReplayRetrievalSource]:
    """Build one replay source per source family.

    A source is created for every :class:`ResearchSourceType` so the
    service can route any planned sub-query; each source filters *items*
    to its own family and serves an empty result when none were recorded.

    Returns:
        A map from every ``ResearchSourceType`` to a replay source over
        ``items``.
    """
    return {
        source_type: ReplayRetrievalSource(source_type=source_type, items=items)
        for source_type in ResearchSourceType
    }
