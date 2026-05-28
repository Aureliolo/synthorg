"""Entry selectors for the consolidation axis split (ADR-0005).

One selector covers all three pre-split strategies: they shared an
identical selection rule (group by category, drop groups below
``group_threshold``, keep the highest-relevance entry with most-recent
``created_at`` as the tiebreak). Density classification is *not*
selection -- in DualMode it routes the op, so it lives in
``DensityRoutingOp``, not here.
"""

from itertools import groupby
from operator import attrgetter
from typing import Final

from synthorg.memory.consolidation.axis import SelectionGroup
from synthorg.memory.models import MemoryEntry

_MIN_GROUP_THRESHOLD: Final[int] = 2
_DEFAULT_GROUP_THRESHOLD: Final[int] = 3


class HighestRelevanceSelector:
    """Keep the highest-relevance entry per category group.

    Reproduces the ``_select_entries`` + grouping logic that was
    duplicated verbatim across ``SimpleConsolidationStrategy``,
    ``LLMConsolidationStrategy`` and ``DualModeConsolidationStrategy``.

    Args:
        group_threshold: Minimum group size to trigger consolidation
            (must be >= 2). Groups smaller than this are not returned.

    Raises:
        ValueError: If ``group_threshold`` is less than 2.
    """

    def __init__(
        self,
        *,
        group_threshold: int = _DEFAULT_GROUP_THRESHOLD,
    ) -> None:
        if group_threshold < _MIN_GROUP_THRESHOLD:
            msg = (
                f"group_threshold must be >= {_MIN_GROUP_THRESHOLD}, "
                f"got {group_threshold}"
            )
            raise ValueError(msg)
        self._group_threshold = group_threshold

    def select(
        self,
        entries: tuple[MemoryEntry, ...],
    ) -> tuple[SelectionGroup, ...]:
        """Group by category and split each eligible group keep/remove.

        Entries with a ``None`` relevance score are treated as ``0.0``;
        ties are broken by most-recent ``created_at``. Groups smaller
        than ``group_threshold`` are omitted.

        Returns:
            Tuple of ``SelectionGroup``.
        """
        if not entries:
            return ()
        groups: list[SelectionGroup] = []
        sorted_entries = sorted(entries, key=attrgetter("category"))
        for category, group_iter in groupby(sorted_entries, key=attrgetter("category")):
            group = list(group_iter)
            if len(group) < self._group_threshold:
                continue
            best = max(
                group,
                key=lambda e: (
                    e.relevance_score if e.relevance_score is not None else 0.0,
                    e.created_at,
                ),
            )
            to_remove = tuple(e for e in group if e.id != best.id)
            groups.append(
                SelectionGroup(
                    category=category,
                    kept=best,
                    to_remove=to_remove,
                )
            )
        return tuple(groups)
