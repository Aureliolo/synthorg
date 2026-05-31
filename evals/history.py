# module-kind: code
"""Filesystem scorecard history: derive + record per-run summaries.

The benchmark emits one :class:`~evals.models.scorecard.Scorecard` per run.
:class:`ScorecardHistory` derives the minimal :class:`ScorecardSummary` the
learning curve needs (owned in-package by :mod:`synthorg.meta.learning_curve`,
so the REST controller and the in-app self-improvement loop read the curve
without depending on this out-of-package ``evals`` layer) and appends it to a
history directory. Aligned with the spine's filesystem-artefact design: no
database table, no migration, no persistence-boundary coupling.
"""

from pathlib import Path
from typing import Final

from evals.models.scorecard import Scorecard
from synthorg.core.types import NotBlankStr
from synthorg.meta.learning_curve import (
    DEFAULT_REGRESSION_THRESHOLD,
    LearningCurve,
    ScorecardSummary,
    append_summary,
    read_learning_curve,
)

# Hex characters of the determinism-source digest kept in a run's label.
_LABEL_DIGEST_LEN: Final[int] = 12
_LABEL_TIMESTAMP_FORMAT: Final[str] = "%Y%m%dT%H%M%S%f"


def _scorecard_label(scorecard: Scorecard) -> str:
    """Build a chronological, collision-resistant label for a recorded run.

    Returns:
        A ``<timestamp>_<digest>`` label string.
    """
    stamp = scorecard.generated_at.strftime(_LABEL_TIMESTAMP_FORMAT)
    return f"{stamp}_{scorecard.cassette_sha256[:_LABEL_DIGEST_LEN]}"


def _summary(scorecard: Scorecard) -> ScorecardSummary:
    """Derive the curve summary from a full scorecard.

    Returns:
        The minimal :class:`ScorecardSummary` for the learning curve.
    """
    return ScorecardSummary(
        run_label=NotBlankStr(_scorecard_label(scorecard)),
        generated_at=scorecard.generated_at,
        total=scorecard.total,
        max_total=scorecard.max_total,
        is_passing=scorecard.is_passing,
    )


class ScorecardHistory:
    """Records benchmark scorecards to a directory and reads the curve back."""

    def __init__(self, history_dir: Path) -> None:
        self._dir = history_dir

    def record(self, scorecard: Scorecard) -> Path:
        """Append *scorecard*'s curve summary to the history directory.

        Returns:
            The path the summary was written to.
        """
        return append_summary(self._dir, _summary(scorecard))

    def load_curve(
        self,
        *,
        regression_threshold: int = DEFAULT_REGRESSION_THRESHOLD,
    ) -> LearningCurve:
        """Read every recorded summary and assemble the ordered curve.

        Returns:
            The chronological :class:`LearningCurve` (empty when no history).
        """
        return read_learning_curve(self._dir, regression_threshold=regression_threshold)


__all__ = ["ScorecardHistory"]
