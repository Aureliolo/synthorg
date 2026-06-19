# module-kind: code
"""Learning-curve model and filesystem reader (in-package, API-facing).

The golden-company benchmark (the out-of-package ``evals`` tooling) records one
:class:`ScorecardSummary` per run into a history directory. This module owns the
summary format and assembles the chronological :class:`LearningCurve` with
derived regression flags. It lives in-package so the REST controller and the
in-app self-improvement feedback loop can read the curve without depending on
the out-of-package ``evals`` layer (the benchmark writes summaries here; the
app reads them).
"""

from pathlib import Path
from typing import Final

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    computed_field,
)

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import META_LEARNING_CURVE_SUMMARY_SKIPPED

logger = get_logger(__name__)

# Suffix marking a recorded run summary in the history directory.
SUMMARY_SUFFIX: Final[str] = ".curvepoint.json"
# A run is flagged a regression when its total drops more than this many points
# below the previous run; large enough to ignore benign run-to-run noise, small
# enough to catch real backsliding.
DEFAULT_REGRESSION_THRESHOLD: Final[int] = 5


class ScorecardSummary(BaseModel):
    """The minimal per-run record the learning curve is assembled from.

    The benchmark derives one of these from each :class:`Scorecard` and appends
    it to the history directory. The full scorecard JSON/Markdown stays in the
    run's own output directory; only this summary feeds the curve.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    run_label: NotBlankStr
    generated_at: AwareDatetime
    total: int = Field(ge=0)
    max_total: int = Field(gt=0)
    is_passing: bool


class LearningCurvePoint(BaseModel):
    """One run on the learning curve (a summary plus derived deltas)."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    run_label: NotBlankStr
    generated_at: AwareDatetime
    total: int = Field(ge=0)
    max_total: int = Field(gt=0)
    is_passing: bool
    delta: int = Field(description="total minus the previous run's total (0 first)")
    is_regression: bool

    @computed_field(
        description="Fraction of the maximum achievable score",
    )
    @property
    def score_fraction(self) -> float:
        """Return the run's score as a fraction of the achievable maximum."""
        return self.total / self.max_total


class LearningCurve(BaseModel):
    """The ordered learning curve assembled from recorded run summaries."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    points: tuple[LearningCurvePoint, ...] = Field(default=())

    @computed_field(
        description="Whether any run on the curve is a regression",
    )
    @property
    def has_regression(self) -> bool:
        """Return whether any recorded run regressed against its predecessor."""
        return any(point.is_regression for point in self.points)

    @computed_field(
        description="The most recent run's total, or None when empty",
    )
    @property
    def latest_total(self) -> int | None:
        """Return the most recent run's total score, or ``None`` when empty."""
        return self.points[-1].total if self.points else None


def append_summary(history_dir: Path, summary: ScorecardSummary) -> Path:
    """Append a run summary to *history_dir* (created if absent).

    Returns:
        The path the summary was written to.
    """
    history_dir.mkdir(parents=True, exist_ok=True)
    target = history_dir / f"{summary.run_label}{SUMMARY_SUFFIX}"
    target.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
    return target


def _load_summaries(history_dir: Path) -> tuple[ScorecardSummary, ...]:
    """Load + chronologically sort every recorded summary.

    Returns:
        Recorded summaries ordered by ``generated_at``.
    """
    if not history_dir.is_dir():
        return ()
    summaries: list[ScorecardSummary] = []
    for path in sorted(history_dir.glob(f"*{SUMMARY_SUFFIX}")):
        try:
            summaries.append(
                ScorecardSummary.model_validate_json(path.read_text(encoding="utf-8"))
            )
        except (ValidationError, ValueError, OSError) as exc:
            # One corrupt / schema-drifted / truncated summary must not break
            # the whole read (harvest curation, the curve endpoint, the
            # in-app feedback signal); skip it and keep the rest.
            logger.warning(
                META_LEARNING_CURVE_SUMMARY_SKIPPED,
                path=str(path),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
    summaries.sort(key=lambda summary: summary.generated_at)
    return tuple(summaries)


def assemble_curve(
    summaries: tuple[ScorecardSummary, ...],
    *,
    regression_threshold: int = DEFAULT_REGRESSION_THRESHOLD,
) -> LearningCurve:
    """Assemble an ordered curve with per-run deltas + regression flags.

    Returns:
        The assembled :class:`LearningCurve`.
    """
    points: list[LearningCurvePoint] = []
    previous_total: int | None = None
    for summary in summaries:
        delta = 0 if previous_total is None else summary.total - previous_total
        is_regression = previous_total is not None and delta < -regression_threshold
        points.append(
            LearningCurvePoint(
                run_label=summary.run_label,
                generated_at=summary.generated_at,
                total=summary.total,
                max_total=summary.max_total,
                is_passing=summary.is_passing,
                delta=delta,
                is_regression=is_regression,
            )
        )
        previous_total = summary.total
    return LearningCurve(points=tuple(points))


def read_learning_curve(
    history_dir: Path,
    *,
    regression_threshold: int = DEFAULT_REGRESSION_THRESHOLD,
) -> LearningCurve:
    """Read every recorded summary from *history_dir* and assemble the curve.

    Returns:
        The chronological :class:`LearningCurve` (empty when no history).
    """
    return assemble_curve(
        _load_summaries(history_dir), regression_threshold=regression_threshold
    )


def latest_summary(history_dir: Path) -> ScorecardSummary | None:
    """Return the most recent recorded summary, or ``None`` when empty.

    Used by the in-app self-improvement loop to read the latest benchmark
    signal without re-assembling the whole curve.

    Returns:
        The newest :class:`ScorecardSummary`, or ``None``.
    """
    summaries = _load_summaries(history_dir)
    return summaries[-1] if summaries else None


__all__ = [
    "DEFAULT_REGRESSION_THRESHOLD",
    "SUMMARY_SUFFIX",
    "LearningCurve",
    "LearningCurvePoint",
    "ScorecardSummary",
    "append_summary",
    "assemble_curve",
    "latest_summary",
    "read_learning_curve",
]
