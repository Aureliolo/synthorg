# module-kind: code
"""Measured benchmark-score provider backed by the repository.

Reads measured per-model scores from a
:class:`~synthorg.persistence.benchmark_score_protocol.BenchmarkScoreRepository`.
A model with a measured row returns its measured ``benchmark:...`` score;
a model with no row returns ``None`` so the Pareto / stakes-routing
seams render the quality axis as explicitly absent rather than a
fabricated number. The ``source`` field is surfaced verbatim, so the
dashboard shows the real provenance of every measured row.
"""

from collections.abc import Mapping

from synthorg.budget.benchmark_protocol import BenchmarkScore
from synthorg.core.pagination import DEFAULT_PAGE_SIZE
from synthorg.core.types import NotBlankStr
from synthorg.persistence.benchmark_score_protocol import BenchmarkScoreRepository


class MeasuredBenchmarkScoreProvider:
    """Repository-backed :class:`BenchmarkScoreProvider`.

    A model with no measured row returns ``None`` so the Pareto analyzer
    skips it (the quality axis is shown as absent, never faked).

    Args:
        repo: The measured benchmark-score repository.
    """

    __slots__ = ("_repo",)

    def __init__(self, repo: BenchmarkScoreRepository) -> None:
        self._repo = repo

    async def get_score(self, model_id: NotBlankStr) -> BenchmarkScore | None:
        """Return the measured score for ``model_id``, or ``None``.

        Returns:
            The measured ``BenchmarkScore`` when a row exists; otherwise
            ``None`` (the model has not been benchmarked).
        """
        record = await self._repo.get(model_id)
        if record is not None:
            return record.to_score()
        return None

    async def list_scores(self) -> Mapping[NotBlankStr, BenchmarkScore]:
        """Return every measured score keyed by canonical model id.

        Returns:
            The measured rows; empty when no model has been benchmarked.
        """
        measured: dict[NotBlankStr, BenchmarkScore] = {}
        # Page through every measured row: a single default-limit call
        # would silently truncate at DEFAULT_PAGE_SIZE once the operator
        # records more models than one page holds.
        offset = 0
        # lint-allow: long-running-loop-kill-switch -- bounded pagination drain
        while True:
            page = await self._repo.list_items(limit=DEFAULT_PAGE_SIZE, offset=offset)
            if not page:
                break
            for record in page:
                measured[record.model_id] = record.to_score()
            # Advance by the page's actual length and stop on an empty page
            # rather than on a short one: a repository that caps the limit
            # below DEFAULT_PAGE_SIZE would otherwise look "done" after the
            # first capped page and silently drop later rows.
            offset += len(page)
        return measured


__all__ = ["MeasuredBenchmarkScoreProvider"]
