"""Calibrated-judge grading for ``kind=judged`` briefs.

Ordinal-only calibration: the judge re-scores the hand-scored anchor
set on every run; if the Spearman rho between the judge's per-anchor
totals and the hand-scored totals falls below
:data:`SPEARMAN_GATE`, the brief is not scored. The eval refuses to
emit a number it cannot defend.

Trust the ORDERING, not the absolute scale. A model upgrade can shift
absolute scores while preserving ordering; an upgrade that flips the
ordering is real news, not a flake, and the eval should fail loud
rather than silently drift the scorecard.
"""

from typing import TYPE_CHECKING, Final, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.errors import JudgeCalibrationFailedError
from evals.models.brief import Brief, BriefKind, JudgedRubric, RubricGradeType
from evals.models.scorecard import JudgeCalibrationReport
from evals.scoring.spearman import spearman_rho
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from evals.loader.anchors import AnchorSet

logger = get_logger(__name__)

# Minimum acceptable Spearman rho between judge totals and hand-scored
# totals. The judge passes when rho >= gate (inclusive). Tuned to fail
# at ordering inversions while tolerating mild noise.
SPEARMAN_GATE: Final[float] = 0.7

# Brief-grade scale for judged briefs; matches the executable side so
# the scorecard's totals are commensurable across both kinds.
JUDGED_TOTAL: Final[int] = 100

# Ternary grade points; binary maps {0.0, 1.0}; score is the raw value
# in [0.0, 1.0]. Encoded once so the validator and the grader agree on
# what each grade scale accepts.
TERNARY_VALUES: Final[tuple[float, ...]] = (0.0, 0.5, 1.0)
BINARY_VALUES: Final[tuple[float, ...]] = (0.0, 1.0)


class JudgedOutput(BaseModel):
    """The candidate output the judge is asked to score against a rubric."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    text: str


@runtime_checkable
class JudgeProtocol(Protocol):
    """Anything that can produce per-dimension scores for an output."""

    def score_against_rubric(
        self,
        rubric: JudgedRubric,
        text: str,
    ) -> dict[str, float]:
        """Return a ``{dimension_name: score}`` mapping for *text*.

        Score values are expected in ``[0.0, 1.0]``; values outside
        that range will be clamped by the grader and binary / ternary
        dimensions will be snapped to their nearest allowed value
        (see :func:`_quantize_to_scale`). The mapping MUST cover every
        dimension named in *rubric.dimensions*; unknown dimension
        names raise.
        """
        ...


class ScriptedJudge(BaseModel):
    """A deterministic judge that returns pre-canned scores by key.

    The judge looks up ``text`` in :attr:`responses`; missing keys
    fall through to :attr:`default_scores`. This is the test-double
    used by the unit suite and by the broken-vs-reference acceptance
    test where we want to assert score gaps without spending LLM
    tokens.

    Invariant: at least one of ``responses`` / ``default_scores`` MUST
    be populated. A judge with neither would raise on every call and
    is almost certainly a wiring mistake.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    responses: dict[str, dict[str, float]] = Field(default_factory=dict)
    default_scores: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _has_response_data(self) -> Self:
        if not self.responses and not self.default_scores:
            msg = (
                "ScriptedJudge requires at least one of "
                "'responses' or 'default_scores' to be non-empty"
            )
            raise ValueError(msg)
        return self

    def score_against_rubric(
        self,
        rubric: JudgedRubric,
        text: str,
    ) -> dict[str, float]:
        """Look *text* up in :attr:`responses`; fall back to ``default_scores``."""
        canned = self.responses.get(text)
        if canned is not None:
            return dict(canned)
        if self.default_scores:
            return dict(self.default_scores)
        msg = (
            f"ScriptedJudge has no response for text={text[:80]!r} "
            f"and no default_scores set (rubric={rubric.rubric_id})"
        )
        raise KeyError(msg)


class JudgedGrade(BaseModel):
    """Aggregate grade for one judged brief.

    Invariant: ``calibration.passed`` MUST be True. A judged grade
    constructed from a failing calibration would be silently
    untrustworthy, so we refuse to materialise one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    score: int = Field(ge=0, le=JUDGED_TOTAL)
    per_dimension: dict[str, float] = Field(default_factory=dict)
    calibration: JudgeCalibrationReport

    @model_validator(mode="after")
    def _calibration_must_pass(self) -> Self:
        if not self.calibration.passed:
            msg = (
                f"JudgedGrade: calibration for rubric "
                f"{self.calibration.rubric_id!r} did not pass "
                f"(spearman_rho={self.calibration.spearman_rho:.3f}, "
                f"gate={self.calibration.gate})"
            )
            raise ValueError(msg)
        return self


def _quantize_to_scale(raw: float, grade_type: RubricGradeType) -> float:
    """Snap *raw* to the closest allowed value for *grade_type*."""
    if grade_type is RubricGradeType.SCORE:
        return max(0.0, min(1.0, raw))
    allowed = BINARY_VALUES if grade_type is RubricGradeType.BINARY else TERNARY_VALUES
    return min(allowed, key=lambda v: abs(v - raw))


def _weighted_sum(
    per_dimension: dict[str, float],
    rubric: JudgedRubric,
) -> float:
    total = 0.0
    for dim in rubric.dimensions:
        if dim.name not in per_dimension:
            msg = (
                f"Judge omitted rubric dimension {dim.name!r} "
                f"(rubric={rubric.rubric_id!r})"
            )
            raise ValueError(msg)
        snapped = _quantize_to_scale(per_dimension[dim.name], dim.grade_type)
        total += snapped * dim.weight
    return total


def calibrate_judge(
    rubric: JudgedRubric,
    judge: JudgeProtocol,
    anchors: AnchorSet,
) -> JudgeCalibrationReport:
    """Run the calibration step; return a report carrying the rho + gate.

    Raises:
        JudgeCalibrationFailedError: When the judge's ordering does
            not correlate with the hand-scored ordering at the
            configured gate.
    """
    if rubric.rubric_id != anchors.rubric_id:
        msg = (
            f"Calibration mismatch: rubric={rubric.rubric_id!r} "
            f"vs anchor set={anchors.rubric_id!r}"
        )
        raise ValueError(msg)

    judge_totals: list[float] = []
    hand_totals: list[float] = []
    for item in anchors.items:
        judge_scores = judge.score_against_rubric(rubric, item.output)
        judge_totals.append(_weighted_sum(judge_scores, rubric))
        hand_totals.append(_weighted_sum(dict(item.hand_scores), rubric))

    rho = spearman_rho(judge_totals, hand_totals)
    passed = rho >= SPEARMAN_GATE
    report = JudgeCalibrationReport(
        rubric_id=rubric.rubric_id,
        spearman_rho=rho,
        gate=SPEARMAN_GATE,
        passed=passed,
        anchor_count=len(anchors.items),
    )
    if not passed:
        logger.warning(
            "evals.judge.calibration_failed",
            rubric_id=rubric.rubric_id,
            rho=rho,
            gate=SPEARMAN_GATE,
            anchor_count=len(anchors.items),
        )
        msg = (
            f"Judge ordering rho={rho:.3f} for rubric={rubric.rubric_id!r} "
            f"is below gate {SPEARMAN_GATE}"
        )
        raise JudgeCalibrationFailedError(msg)
    return report


def grade_judged(
    brief: Brief,
    output: JudgedOutput,
    *,
    judge: JudgeProtocol,
    anchors: AnchorSet,
) -> JudgedGrade:
    """Score *output* against *brief.rubric* using the calibrated judge.

    Args:
        brief: A judged brief (``kind=judged``).
        output: The candidate output to score.
        judge: A :class:`JudgeProtocol` implementation; in tests this
            is :class:`ScriptedJudge`, in production it is the LLM
            judge wired to the cassette.
        anchors: The hand-scored anchor set for the brief's rubric.

    Returns:
        :class:`JudgedGrade` carrying the weighted score in
        ``[0, JUDGED_TOTAL]`` and the calibration report.

    Raises:
        ValueError: If *brief* is not judged.
        JudgeCalibrationFailedError: Propagated from
            :func:`calibrate_judge` when the ordering gate fails.
    """
    if brief.kind is not BriefKind.JUDGED:
        msg = f"grade_judged called with kind={brief.kind.value!r}"
        raise ValueError(msg)
    if brief.rubric is None:
        msg = "judged brief is missing its 'rubric' block"
        raise ValueError(msg)

    rubric = brief.rubric
    calibration = calibrate_judge(rubric, judge, anchors)

    per_dimension = judge.score_against_rubric(rubric, output.text)
    weighted = _weighted_sum(per_dimension, rubric)
    score = round(weighted * JUDGED_TOTAL)

    return JudgedGrade(
        score=score,
        per_dimension={
            k: _quantize_to_scale(v, _grade_type_for(rubric, k))
            for k, v in per_dimension.items()
        },
        calibration=calibration,
    )


def _grade_type_for(rubric: JudgedRubric, name: str) -> RubricGradeType:
    for dim in rubric.dimensions:
        if dim.name == name:
            return dim.grade_type
    msg = (
        f"Judge returned unknown dimension {name!r} "
        f"for rubric={rubric.rubric_id!r}; expected one of "
        f"{tuple(d.name for d in rubric.dimensions)}"
    )
    raise ValueError(msg)


__all__ = [
    "BINARY_VALUES",
    "JUDGED_TOTAL",
    "SPEARMAN_GATE",
    "TERNARY_VALUES",
    "JudgeProtocol",
    "JudgedGrade",
    "JudgedOutput",
    "ScriptedJudge",
    "calibrate_judge",
    "grade_judged",
]
