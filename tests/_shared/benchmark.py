"""Test double for the benchmark-score provider.

Replicates the per-rung calibrated scores the routing / Pareto tests rely on
(basic 72, capable 85, expert 92), keyed by the ``example-<rung>-001``
archetype ids and resolved through the same heuristic the production
provider uses. This is a test fixture, not production data: the production
``MeasuredBenchmarkScoreProvider`` reads real measured scores and returns
``None`` for unmeasured models.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final

from synthorg.budget.benchmark_protocol import BenchmarkScore
from synthorg.budget.model_capability import ModelCapabilityMap, resolve_capability
from synthorg.core.types import NotBlankStr

FIXTURE_SOURCE: Final[str] = "benchmark:test-fixture"

_CAPABILITY_SCORES: Final[Mapping[str, BenchmarkScore]] = {
    "expert": BenchmarkScore(
        score=92.0,
        confidence_lower=88.0,
        confidence_upper=95.0,
        source=NotBlankStr(FIXTURE_SOURCE),
        last_updated=datetime(2026, 5, 20, tzinfo=UTC),
    ),
    "capable": BenchmarkScore(
        score=85.0,
        confidence_lower=80.0,
        confidence_upper=89.0,
        source=NotBlankStr(FIXTURE_SOURCE),
        last_updated=datetime(2026, 5, 20, tzinfo=UTC),
    ),
    "basic": BenchmarkScore(
        score=72.0,
        confidence_lower=65.0,
        confidence_upper=78.0,
        source=NotBlankStr(FIXTURE_SOURCE),
        last_updated=datetime(2026, 5, 20, tzinfo=UTC),
    ),
}


class FakeCapabilityBenchmarkScoreProvider:
    """Per-rung constant :class:`BenchmarkScoreProvider` for tests.

    Returns the same :class:`BenchmarkScore` for every model that resolves a
    known rung; unknown models return ``None`` so callers exercise the
    absent-score path.

    Args:
        capability_map: Operator model-id-to-rung overrides consulted before
            the archetype heuristic.
    """

    __slots__ = ("_capability_map",)

    def __init__(self, *, capability_map: ModelCapabilityMap | None = None) -> None:
        self._capability_map = capability_map

    async def get_score(self, model_id: NotBlankStr) -> BenchmarkScore | None:
        """Return the fixture score for ``model_id``, or ``None``.

        Returns:
            The matching ``BenchmarkScore``, or ``None`` when the model
            resolves no rung.
        """
        capability = resolve_capability(model_id, self._capability_map)
        if capability is None:
            return None
        return _CAPABILITY_SCORES[capability]

    async def list_scores(self) -> Mapping[NotBlankStr, BenchmarkScore]:
        """Return the fixture scores keyed by canonical model id.

        Returns:
            The per-rung scores keyed by ``example-<rung>-001`` plus any
            override ids in the capability map.
        """
        scores: dict[NotBlankStr, BenchmarkScore] = {
            NotBlankStr(f"example-{capability}-001"): score
            for capability, score in _CAPABILITY_SCORES.items()
        }
        if self._capability_map is not None:
            for model_id, capability in self._capability_map.overrides.items():
                scores[model_id] = _CAPABILITY_SCORES[capability]
        return scores
