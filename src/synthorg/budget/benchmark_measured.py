"""Measured benchmark-score provider backed by the repository.

Reads measured per-model scores from a
:class:`~synthorg.persistence.benchmark_score_protocol.BenchmarkScoreRepository`
and composes a cold-start fallback (the
:class:`~synthorg.budget.benchmark_stub.StubBenchmarkScoreProvider`):
a model with a measured row returns its measured ``benchmark:...`` score,
and a model with no row falls through to the calibrated stub so the
Pareto / stakes-routing seams always get an answer where one is
available. The ``source`` field is surfaced verbatim, so the dashboard
distinguishes measured rows from stub fallbacks.
"""

from collections.abc import Mapping

from synthorg.budget.benchmark_protocol import BenchmarkScore, BenchmarkScoreProvider
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.persistence.benchmark_score_protocol import BenchmarkScoreRepository

logger = get_logger(__name__)


class MeasuredBenchmarkScoreProvider:
    """Repository-backed :class:`BenchmarkScoreProvider` with stub fallback.

    Args:
        repo: The measured benchmark-score repository.
        fallback: Cold-start provider consulted when a model has no
            measured row (typically
            :class:`StubBenchmarkScoreProvider`). When omitted, an
            unmeasured model returns ``None`` so the Pareto analyzer
            skips it.
    """

    __slots__ = ("_fallback", "_repo")

    def __init__(
        self,
        repo: BenchmarkScoreRepository,
        *,
        fallback: BenchmarkScoreProvider | None = None,
    ) -> None:
        self._repo = repo
        self._fallback = fallback

    async def get_score(self, model_id: NotBlankStr) -> BenchmarkScore | None:
        """Return the measured score for ``model_id``, else the fallback.

        Returns:
            The measured ``BenchmarkScore`` when a row exists; otherwise
            the fallback provider's score, or ``None`` when no fallback
            is wired or the fallback also has no score.
        """
        record = await self._repo.get(model_id)
        if record is not None:
            return record.to_score()
        if self._fallback is not None:
            return await self._fallback.get_score(model_id)
        return None

    async def list_scores(self) -> Mapping[NotBlankStr, BenchmarkScore]:
        """Return all known scores, measured rows overriding the fallback.

        Returns:
            A merge of the fallback's scores and the measured rows, with
            measured rows taking precedence on a model-id collision.
        """
        merged: dict[NotBlankStr, BenchmarkScore] = {}
        if self._fallback is not None:
            merged.update(await self._fallback.list_scores())
        for record in await self._repo.list_items():
            merged[record.model_id] = record.to_score()
        return merged


__all__ = ["MeasuredBenchmarkScoreProvider"]
