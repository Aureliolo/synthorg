# module-kind: code
"""Stub benchmark-score provider for the Pareto view.

Implements :class:`BenchmarkScoreProvider` using calibrated per-tier
constants. Real benchmark implementations swap in behind the same
protocol via the factory wiring in ``lifecycle_helpers.py``.

The stub source identifier (``"stub:calibrated-v1"``) is rendered
verbatim by the dashboard so operators can see at a glance that the
quality axis is illustrative when real benchmark scores are absent.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final

from synthorg.budget.benchmark_protocol import BenchmarkScore
from synthorg.budget.model_tier import ModelTierMap, resolve_tier
from synthorg.core.types import NotBlankStr

# Per-tier calibrated stub scores. The values mirror the rough public
# benchmark spread observed between the SynthOrg-aligned tiers and are
# intentionally conservative on the upper bound so operators are not
# misled into thinking the stub data is authoritative.
_TIER_SCORES: Final[Mapping[str, BenchmarkScore]] = {
    "large": BenchmarkScore(
        score=92.0,
        confidence_lower=88.0,
        confidence_upper=95.0,
        source="stub:calibrated-v1",
        last_updated=datetime(2026, 5, 20, tzinfo=UTC),
    ),
    "medium": BenchmarkScore(
        score=85.0,
        confidence_lower=80.0,
        confidence_upper=89.0,
        source="stub:calibrated-v1",
        last_updated=datetime(2026, 5, 20, tzinfo=UTC),
    ),
    "small": BenchmarkScore(
        score=72.0,
        confidence_lower=65.0,
        confidence_upper=78.0,
        source="stub:calibrated-v1",
        last_updated=datetime(2026, 5, 20, tzinfo=UTC),
    ),
    "local-small": BenchmarkScore(
        score=58.0,
        confidence_lower=50.0,
        confidence_upper=65.0,
        source="stub:calibrated-v1",
        last_updated=datetime(2026, 5, 20, tzinfo=UTC),
    ),
}


class StubBenchmarkScoreProvider:
    """Calibrated-constant :class:`BenchmarkScoreProvider`.

    Returns the same :class:`BenchmarkScore` for every model that
    resolves a known tier (``large``/``medium``/``small``/``local-small``);
    unknown models return ``None`` so the Pareto analyzer skips them.

    Resolution honours the operator's :class:`ModelTierMap` overrides
    before the built-in archetype heuristic, so a custom operator id that
    is mapped only through ``budget.model_tier_overrides`` still resolves
    its calibrated cold-start score instead of falling through to ``None``.

    The source identifier is fixed at ``"stub:calibrated-v1"`` so the
    dashboard can distinguish stub data from a real benchmark feed.

    Args:
        tier_map: Operator model-id-to-tier overrides consulted before the
            heuristic. ``None`` (the default) leaves resolution entirely to
            the archetype heuristic.
    """

    __slots__ = ("_tier_map",)

    def __init__(self, *, tier_map: ModelTierMap | None = None) -> None:
        self._tier_map = tier_map

    async def get_score(self, model_id: NotBlankStr) -> BenchmarkScore | None:
        """Return the stub score for ``model_id``, or ``None`` for unknown.

        Returns:
            The matching ``BenchmarkScore``, or ``None`` when no match is found.
        """
        tier = resolve_tier(model_id, self._tier_map)
        if tier is None:
            return None
        return _TIER_SCORES[tier]

    async def list_scores(self) -> Mapping[NotBlankStr, BenchmarkScore]:
        """Return the stub scores keyed by canonical model id.

        The protocol contract keys scores by canonical model id; the
        stub maps each tier to its representative ``example-<tier>-001``
        sample model so callers receive model-id-indexed scores rather
        than bare tier labels. Operator override ids are surfaced under
        their own id so an override-only model is not absent from merged
        listings.

        Returns:
            Result of type ``Mapping[NotBlankStr, BenchmarkScore]``.
        """
        scores: dict[NotBlankStr, BenchmarkScore] = {
            f"example-{tier}-001": score for tier, score in _TIER_SCORES.items()
        }
        if self._tier_map is not None:
            for model_id, tier in self._tier_map.overrides.items():
                scores[model_id] = _TIER_SCORES[tier]
        return scores


__all__ = ["StubBenchmarkScoreProvider"]
