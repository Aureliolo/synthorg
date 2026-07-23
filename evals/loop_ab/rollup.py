# module-kind: code
"""Roll per-tier scores up into the complexity buckets promotion decides on.

Each brief is measured on every model tier, but ``loop_complexity_overrides``
routes on complexity alone: whichever loop it names runs on whatever model the
agent is pinned to. So a promotion recommendation has to hold across tiers, not
just on the tier that flatters a loop most.

Two rules follow from that:

* A loop's standing in a bucket is its **mean** composite across every
  ``(brief, tier)`` cell that lands in the bucket (the current suite has one
  brief per complexity, so today that is a mean across tiers), so a single
  flattering cell cannot carry it.
* A loop disqualified on **any** tier is disqualified for the bucket. Promoting
  a loop that fails on the small model would break every small-model
  deployment, which is precisely what the gate exists to prevent.
"""

import statistics

from evals.loop_ab.rubric import DimensionScores, LoopCellScore
from synthorg.core.task_enums import Complexity
from synthorg.core.types import NotBlankStr

#: Maps a brief's 1..5 ``estimated_complexity`` onto the complexity vocabulary
#: ``engine.loop_complexity_overrides`` accepts. The top two estimates both fold
#: into EPIC because the setting has no finer bucket to route them to.
_COMPLEXITY_BY_ESTIMATE: dict[int, Complexity] = {
    1: Complexity.SIMPLE,
    2: Complexity.MEDIUM,
    3: Complexity.COMPLEX,
    4: Complexity.EPIC,
    5: Complexity.EPIC,
}


def complexity_for_estimate(estimate: int) -> Complexity:
    """Map a brief's estimated complexity onto a routing bucket.

    Returns:
        The :class:`Complexity` the brief's estimate routes to.

    Raises:
        ValueError: The estimate is outside the brief schema's 1..5 range.
    """
    bucket = _COMPLEXITY_BY_ESTIMATE.get(estimate)
    if bucket is None:
        msg = (
            f"estimated_complexity {estimate} has no routing bucket; "
            f"expected one of {sorted(_COMPLEXITY_BY_ESTIMATE)}"
        )
        raise ValueError(msg)
    return bucket


def _merge(loop_type: str, scores: tuple[LoopCellScore, ...]) -> LoopCellScore:
    """Combine one loop's per-tier scores into a single bucket-level score.

    Returns:
        The merged :class:`LoopCellScore` for the bucket.
    """
    disqualifying = [s for s in scores if s.disqualified]
    reason = None
    if disqualifying:
        reason = (
            f"disqualified on {len(disqualifying)} of {len(scores)} measured "
            f"tier(s): {disqualifying[0].disqualification_reason}"
        )
    return LoopCellScore(
        loop_type=NotBlankStr(loop_type),
        dimensions=DimensionScores(
            correctness=statistics.mean(s.dimensions.correctness for s in scores),
            tokens=statistics.mean(s.dimensions.tokens for s in scores),
            latency=statistics.mean(s.dimensions.latency for s in scores),
            turns=statistics.mean(s.dimensions.turns for s in scores),
            resilience=statistics.mean(s.dimensions.resilience for s in scores),
        ),
        composite=statistics.mean(s.composite for s in scores),
        disqualified=bool(disqualifying),
        disqualification_reason=reason,
    )


def rollup_by_complexity(
    scored: tuple[tuple[int, tuple[LoopCellScore, ...]], ...],
) -> dict[Complexity, tuple[LoopCellScore, ...]]:
    """Combine ``(estimated_complexity, per-tier scores)`` into routing buckets.

    Args:
        scored: One entry per measured ``(brief, tier)`` cell, pairing the
            brief's estimated complexity with that cell's scored loops.

    Returns:
        Bucket-level scores per complexity, one row per loop.
    """
    by_bucket: dict[Complexity, dict[str, list[LoopCellScore]]] = {}
    for estimate, cell in scored:
        bucket = by_bucket.setdefault(complexity_for_estimate(estimate), {})
        for score in cell:
            bucket.setdefault(score.loop_type, []).append(score)
    return {
        complexity: tuple(
            _merge(loop_type, tuple(scores))
            for loop_type, scores in sorted(per_loop.items())
        )
        for complexity, per_loop in by_bucket.items()
    }


__all__ = ["complexity_for_estimate", "rollup_by_complexity"]
