"""Stub benchmark-score provider for the Pareto view (#1982).

Implements :class:`BenchmarkScoreProvider` using calibrated per-tier
constants. Sibling issue #1980 (Golden-company benchmark) will land a
real implementation behind the same protocol; the factory wiring in
``lifecycle_helpers.py`` swaps the stub for the real provider when
#1980 is available.

The stub source identifier (``"stub:calibrated-v1"``) is rendered
verbatim by the dashboard so operators can see at a glance that the
quality axis is illustrative until the real benchmark scores ship.
"""

from collections.abc import Mapping  # noqa: TC003 -- runtime by return annotations
from datetime import UTC, datetime
from typing import Final

from synthorg.budget.benchmark_protocol import BenchmarkScore
from synthorg.core.types import NotBlankStr  # noqa: TC001 -- runtime annotation

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


def _tier_from_model_id(model_id: str) -> str | None:
    """Map ``example-<tier>-<rev>`` to its tier label."""
    parts = model_id.split("-")
    if len(parts) < 2:  # noqa: PLR2004
        return None
    if "local" in parts and "small" in parts:
        return "local-small"
    candidate = parts[-2].lower()
    if candidate in {"large", "medium", "small"}:
        return candidate
    return None


class StubBenchmarkScoreProvider:
    """Calibrated-constant :class:`BenchmarkScoreProvider`.

    Returns the same :class:`BenchmarkScore` for every model that
    matches a known tier (``large``/``medium``/``small``/``local-small``);
    unknown models return ``None`` so the Pareto analyzer skips them.

    The source identifier is fixed at ``"stub:calibrated-v1"`` so the
    dashboard can distinguish stub data from a real benchmark feed.
    """

    async def get_score(self, model_id: NotBlankStr) -> BenchmarkScore | None:
        """Return the stub score for ``model_id``, or ``None`` for unknown."""
        tier = _tier_from_model_id(model_id)
        if tier is None:
            return None
        return _TIER_SCORES[tier]

    async def list_scores(self) -> Mapping[NotBlankStr, BenchmarkScore]:
        """Return the per-tier stub scores keyed by tier label.

        Tiers double as model ids for the purposes of the frontier;
        the dashboard maps tier -> sample model id when rendering.
        """
        return dict(_TIER_SCORES)


__all__ = ["StubBenchmarkScoreProvider"]
