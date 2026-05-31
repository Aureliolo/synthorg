# module-kind: code
"""Filesystem scorecard history and learning-curve assembly.

The benchmark emits one :class:`~evals.models.scorecard.Scorecard` per run.
:class:`ScorecardHistory` records each into a history directory and reads them
back, in chronological order, as a :class:`LearningCurve` with derived
regression flags. This is aligned with the eval spine's deliberate
filesystem-artefact design (cassettes + scorecards are JSON on purpose): no
database table, no migration, no persistence-boundary coupling.
"""

from pathlib import Path
from typing import Final

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
)

from evals.models.scorecard import Scorecard
from synthorg.core.types import NotBlankStr

# Suffix marking a recorded scorecard in the history directory.
_HISTORY_SUFFIX: Final[str] = ".scorecard.json"
# A run is flagged a regression when its total drops more than this many points
# below the previous run; large enough to ignore benign run-to-run noise, small
# enough to catch real backsliding.
DEFAULT_REGRESSION_THRESHOLD: Final[int] = 5
# Hex characters of the determinism-source digest kept in a run's label.
_LABEL_DIGEST_LEN: Final[int] = 12
_LABEL_TIMESTAMP_FORMAT: Final[str] = "%Y%m%dT%H%M%S%f"


class LearningCurvePoint(BaseModel):
    """One run on the learning curve (a recorded scorecard's summary)."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    run_label: NotBlankStr
    generated_at: AwareDatetime
    total: int = Field(ge=0)
    max_total: int = Field(gt=0)
    is_passing: bool
    delta: int = Field(description="total minus the previous run's total (0 first)")
    is_regression: bool

    @computed_field(  # type: ignore[prop-decorator]
        description="Fraction of the maximum achievable score",
    )
    @property
    def score_fraction(self) -> float:
        """Return the run's score as a fraction of the achievable maximum."""
        return self.total / self.max_total


class LearningCurve(BaseModel):
    """The ordered learning curve assembled from recorded scorecards."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    points: tuple[LearningCurvePoint, ...] = Field(default=())

    @computed_field(  # type: ignore[prop-decorator]
        description="Whether any run on the curve is a regression",
    )
    @property
    def has_regression(self) -> bool:
        """Return whether any recorded run regressed against its predecessor."""
        return any(point.is_regression for point in self.points)

    @computed_field(  # type: ignore[prop-decorator]
        description="The most recent run's total, or None when empty",
    )
    @property
    def latest_total(self) -> int | None:
        """Return the most recent run's total score, or ``None`` when empty."""
        return self.points[-1].total if self.points else None


def _scorecard_label(scorecard: Scorecard) -> str:
    """Build a chronological, collision-resistant label for a recorded run.

    Returns:
        A ``<timestamp>_<digest>`` label string.
    """
    stamp = scorecard.generated_at.strftime(_LABEL_TIMESTAMP_FORMAT)
    return f"{stamp}_{scorecard.cassette_sha256[:_LABEL_DIGEST_LEN]}"


def _build_curve(
    scorecards: tuple[Scorecard, ...],
    *,
    regression_threshold: int,
) -> LearningCurve:
    """Assemble an ordered curve with per-run deltas + regression flags.

    Returns:
        The assembled :class:`LearningCurve`.
    """
    points: list[LearningCurvePoint] = []
    previous_total: int | None = None
    for scorecard in scorecards:
        delta = 0 if previous_total is None else scorecard.total - previous_total
        is_regression = previous_total is not None and delta < -regression_threshold
        points.append(
            LearningCurvePoint(
                run_label=NotBlankStr(_scorecard_label(scorecard)),
                generated_at=scorecard.generated_at,
                total=scorecard.total,
                max_total=scorecard.max_total,
                is_passing=scorecard.is_passing,
                delta=delta,
                is_regression=is_regression,
            )
        )
        previous_total = scorecard.total
    return LearningCurve(points=tuple(points))


class ScorecardHistory:
    """Records benchmark scorecards to a directory and reads the curve back."""

    def __init__(self, history_dir: Path) -> None:
        self._dir = history_dir

    def record(self, scorecard: Scorecard) -> Path:
        """Write *scorecard* into the history directory.

        Returns:
            The path the scorecard was written to.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        target = self._dir / f"{_scorecard_label(scorecard)}{_HISTORY_SUFFIX}"
        target.write_text(scorecard.model_dump_json(indent=2), encoding="utf-8")
        return target

    def load_curve(
        self,
        *,
        regression_threshold: int = DEFAULT_REGRESSION_THRESHOLD,
    ) -> LearningCurve:
        """Read every recorded scorecard and assemble the ordered curve.

        Returns:
            The chronological :class:`LearningCurve` (empty when no history).
        """
        return _build_curve(
            self._load_scorecards(), regression_threshold=regression_threshold
        )

    def _load_scorecards(self) -> tuple[Scorecard, ...]:
        """Load + chronologically sort every recorded scorecard.

        Returns:
            Recorded scorecards ordered by ``generated_at``.
        """
        if not self._dir.is_dir():
            return ()
        cards = [
            Scorecard.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(self._dir.glob(f"*{_HISTORY_SUFFIX}"))
        ]
        cards.sort(key=lambda card: card.generated_at)
        return tuple(cards)


__all__ = [
    "DEFAULT_REGRESSION_THRESHOLD",
    "LearningCurve",
    "LearningCurvePoint",
    "ScorecardHistory",
]
