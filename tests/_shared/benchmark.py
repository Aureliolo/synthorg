"""Test double for the benchmark-score provider.

Replicates the per-tier calibrated scores the routing / Pareto tests
rely on (small 72, medium 85, large 92, local-small 58), keyed by the
``example-<tier>-001`` archetype ids and resolved through the same tier
heuristic the production provider uses. This is a test fixture, not
production data: the production ``MeasuredBenchmarkScoreProvider`` reads
real measured scores and returns ``None`` for unmeasured models.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final

from synthorg.budget.benchmark_protocol import BenchmarkScore
from synthorg.budget.model_capability import ModelCapabilityMap
from synthorg.core.types import NotBlankStr

FIXTURE_SOURCE: Final[str] = "benchmark:test-fixture"

_TIER_SCORES: Final[Mapping[str, BenchmarkScore]] = {
    "large": BenchmarkScore(
        score=92.0,
        confidence_lower=88.0,
        confidence_upper=95.0,
        source=NotBlankStr(FIXTURE_SOURCE),
        last_updated=datetime(2026, 5, 20, tzinfo=UTC),
    ),
    "medium": BenchmarkScore(
        score=85.0,
        confidence_lower=80.0,
        confidence_upper=89.0,
        source=NotBlankStr(FIXTURE_SOURCE),
        last_updated=datetime(2026, 5, 20, tzinfo=UTC),
    ),
    "small": BenchmarkScore(
        score=72.0,
        confidence_lower=65.0,
        confidence_upper=78.0,
        source=NotBlankStr(FIXTURE_SOURCE),
        last_updated=datetime(2026, 5, 20, tzinfo=UTC),
    ),
    "local-small": BenchmarkScore(
        score=58.0,
        confidence_lower=50.0,
        confidence_upper=65.0,
        source=NotBlankStr(FIXTURE_SOURCE),
        last_updated=datetime(2026, 5, 20, tzinfo=UTC),
    ),
}


class FakeTierBenchmarkScoreProvider:
    """Per-tier constant :class:`BenchmarkScoreProvider` for tests.

    Returns the same :class:`BenchmarkScore` for every model that
    resolves a known tier; unknown models return ``None`` so callers
    exercise the absent-score path.

    Args:
        tier_map: Operator model-id-to-tier overrides consulted before
            the archetype heuristic.
    """

    __slots__ = ("_tier_map",)

    def __init__(self, *, tier_map: ModelCapabilityMap | None = None) -> None:
        self._tier_map = tier_map

    async def get_score(self, model_id: NotBlankStr) -> BenchmarkScore | None:
        """Return the fixture score for ``model_id``, or ``None``.

        Returns:
            The matching ``BenchmarkScore``, or ``None`` when the model
            resolves no tier.
        """
        tier = resolve_capability(model_id, self._tier_map)
        if tier is None:
            return None
        return _TIER_SCORES[tier]

    async def list_scores(self) -> Mapping[NotBlankStr, BenchmarkScore]:
        """Return the fixture scores keyed by canonical model id.

        Returns:
            The per-tier scores keyed by ``example-<tier>-001`` plus any
            override ids in the tier map.
        """
        scores: dict[NotBlankStr, BenchmarkScore] = {
            NotBlankStr(f"example-{tier}-001"): score
            for tier, score in _TIER_SCORES.items()
        }
        if self._tier_map is not None:
            for model_id, tier in self._tier_map.overrides.items():
                scores[model_id] = _TIER_SCORES[tier]
        return scores
