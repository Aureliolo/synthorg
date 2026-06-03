"""Eval-backed golden-scorecard provider for the tool-validation gate.

Adapts the golden-company eval spine into the toolsmith's
:class:`GoldenScorecardProvider` seam so the no-regression gate runs
end-to-end instead of failing closed for want of a provider.

The provider depends only on an injected ``run_scorecard`` callable that
returns a suite total (``Scorecard.total``). This keeps the framework's
production code decoupled from the out-of-package eval harness: the
eval-bound runner is constructed at the composition root (the toolsmith
wiring step), and the adapter itself imports nothing from ``evals``.

No-regression semantics: the default deterministic eval provider ignores
authored tools, so a candidate tool cannot move the suite total. In that
mode the provider runs the suite once and reports ``candidate ==
baseline`` -- a no-regression *smoke check* that registers any tool whose
presence does not break the golden run. A genuinely-measured delta (a
candidate arm scored against a live provider, or a cassette recorded with
the candidate tool active) requires ``candidate_sensitive=True``, which
runs both arms; that path is how a regressing tool is actually rejected.
"""

from collections.abc import Awaitable, Callable

from synthorg.meta.toolsmith.models import ToolBlueprint

GoldenScoreRunner = Callable[[ToolBlueprint | None], Awaitable[int]]
"""Runs the golden suite for a candidate (or ``None`` baseline) -> total."""


class EvalGoldenScorecardProvider:
    """Scores the golden benchmark with/without a candidate via the eval spine.

    Args:
        run_scorecard: Runs the golden suite and returns its total. Called
            with ``None`` for the baseline arm and the candidate blueprint
            for the candidate arm.
        candidate_sensitive: Whether ``run_scorecard``'s total actually
            depends on the candidate tool. ``False`` (the deterministic
            default) runs the suite once and reports ``candidate ==
            baseline``; ``True`` runs both arms so a regressing candidate
            can score below the baseline.
    """

    def __init__(
        self,
        *,
        run_scorecard: GoldenScoreRunner,
        candidate_sensitive: bool = False,
    ) -> None:
        self._run_scorecard = run_scorecard
        self._candidate_sensitive = candidate_sensitive

    async def score(self, blueprint: ToolBlueprint) -> tuple[int, int]:
        """Return ``(baseline_total, candidate_total)`` golden scores.

        In the deterministic default the candidate arm is identical to the
        baseline, so the suite runs once and both totals are equal. Only
        ``candidate_sensitive`` runs the candidate arm separately.

        Returns:
            The ``(baseline_total, candidate_total)`` suite-score pair.
        """
        baseline = await self._run_scorecard(None)
        if not self._candidate_sensitive:
            return baseline, baseline
        candidate = await self._run_scorecard(blueprint)
        return baseline, candidate


__all__ = ["EvalGoldenScorecardProvider", "GoldenScoreRunner"]
