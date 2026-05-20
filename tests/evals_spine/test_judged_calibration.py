"""Tests for the judged-brief grader and Spearman calibration gate."""

from collections.abc import Callable
from pathlib import Path

import pytest

from evals.errors import JudgeAnchorSetTooSmallError, JudgeCalibrationFailedError
from evals.loader.anchors import AnchorItem, AnchorSet
from evals.models.brief import (
    Brief,
    BriefKind,
    BriefPriority,
    JudgedRubric,
    LimitsSpec,
    RubricDimension,
    RubricGradeType,
)
from evals.scoring.judged import (
    JUDGED_TOTAL,
    SPEARMAN_GATE,
    JudgedOutput,
    ScriptedJudge,
    calibrate_judge,
    grade_judged,
)
from evals.scoring.spearman import (
    MIN_PAIRS_FOR_CORRELATION,
    average_ranks,
    spearman_rho,
)

# --- Spearman primitive ---------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("xs", "ys", "predicate"),
    [
        (
            [1.0, 2.0, 3.0, 4.0],
            [10.0, 20.0, 30.0, 40.0],
            lambda r: r == pytest.approx(1.0),
        ),
        (
            [1.0, 2.0, 3.0, 4.0],
            [40.0, 30.0, 20.0, 10.0],
            lambda r: r == pytest.approx(-1.0),
        ),
        (
            [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            [4.0, 3.0, 5.0, 6.0, 1.0, 2.0],
            lambda r: r < SPEARMAN_GATE,
        ),
        (
            [1.0, 1.0, 2.0, 3.0],
            [1.0, 2.0, 2.0, 3.0],
            lambda r: -1.0 <= r <= 1.0,
        ),
        (
            [5.0, 5.0, 5.0, 5.0],
            [9.0, 9.0, 9.0, 9.0],
            lambda r: r == pytest.approx(1.0),
        ),
        (
            [5.0, 5.0, 5.0, 5.0],
            [1.0, 2.0, 3.0, 4.0],
            lambda r: r == pytest.approx(0.0),
        ),
    ],
    ids=[
        "perfect_positive",
        "perfect_negative",
        "mid_correlation_below_gate",
        "handles_ties",
        "both_constant_returns_one",
        "one_side_constant_returns_zero",
    ],
)
def test_spearman_rho_cases(
    xs: list[float], ys: list[float], predicate: Callable[[float], bool]
) -> None:
    rho = spearman_rho(xs, ys)
    assert predicate(rho)


@pytest.mark.unit
def test_spearman_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="length mismatch"):
        spearman_rho([1.0, 2.0], [1.0, 2.0, 3.0])


@pytest.mark.unit
def test_spearman_too_few_pairs_raises() -> None:
    with pytest.raises(ValueError, match="paired samples"):
        spearman_rho([1.0, 2.0], [1.0, 2.0])


@pytest.mark.unit
def test_average_ranks_handles_ties() -> None:
    # Values [10, 20, 20, 30] -> sorted ranks are 1, 2.5, 2.5, 4
    assert average_ranks([10.0, 20.0, 20.0, 30.0]) == (1.0, 2.5, 2.5, 4.0)


# --- Calibration + grading -----------------------------------------------


def _rubric() -> JudgedRubric:
    return JudgedRubric(
        rubric_id="summarise",
        dimensions=(
            RubricDimension(
                name="faithfulness", weight=0.5, grade_type=RubricGradeType.TERNARY
            ),
            RubricDimension(
                name="clarity", weight=0.5, grade_type=RubricGradeType.TERNARY
            ),
        ),
        reference_answer_path="anchors/summarise_ref.md",
    )


def _anchor_set() -> AnchorSet:
    """Build an anchor set with N items, hand-scored across the score spectrum."""
    items: list[AnchorItem] = []
    # Hand scores: 5 items spanning from low to high.
    hand_specs = [
        ("a1", {"faithfulness": 0.0, "clarity": 0.0}),
        ("a2", {"faithfulness": 0.0, "clarity": 0.5}),
        ("a3", {"faithfulness": 0.5, "clarity": 0.5}),
        ("a4", {"faithfulness": 1.0, "clarity": 0.5}),
        ("a5", {"faithfulness": 1.0, "clarity": 1.0}),
    ]
    for anchor_id, hand in hand_specs:
        output = f"anchor-output-{anchor_id}"
        items.append(
            AnchorItem(
                anchor_id=anchor_id,
                output=output,
                hand_scores=hand,
            )
        )
    return AnchorSet(rubric_id="summarise", anchor_set_version=1, items=tuple(items))


def _judge_aligned_with(anchors: AnchorSet) -> ScriptedJudge:
    return ScriptedJudge(
        responses={item.output: dict(item.hand_scores) for item in anchors.items}
    )


def _judge_inverted_to(anchors: AnchorSet) -> ScriptedJudge:
    # Reverse the hand-scored ordering by inverting each dimension.
    return ScriptedJudge(
        responses={
            item.output: {k: 1.0 - v for k, v in item.hand_scores.items()}
            for item in anchors.items
        }
    )


@pytest.mark.unit
def test_calibration_passes_when_judge_agrees() -> None:
    rubric = _rubric()
    anchors = _anchor_set()
    judge = _judge_aligned_with(anchors)
    report = calibrate_judge(rubric, judge, anchors)
    assert report.passed is True
    assert report.spearman_rho >= SPEARMAN_GATE
    assert report.anchor_count == len(anchors.items)


@pytest.mark.unit
def test_calibration_fails_when_judge_inverts() -> None:
    rubric = _rubric()
    anchors = _anchor_set()
    judge = _judge_inverted_to(anchors)
    with pytest.raises(JudgeCalibrationFailedError):
        calibrate_judge(rubric, judge, anchors)


@pytest.mark.unit
def test_calibration_rejects_mismatched_rubric() -> None:
    rubric = _rubric()
    other_anchors = AnchorSet(
        rubric_id="different",
        anchor_set_version=1,
        items=(
            AnchorItem(
                anchor_id="x",
                output="x",
                hand_scores={"faithfulness": 0.0, "clarity": 0.0},
            ),
        ),
    )
    with pytest.raises(ValueError, match="mismatch"):
        calibrate_judge(rubric, _judge_aligned_with(other_anchors), other_anchors)


@pytest.mark.unit
def test_grade_judged_full_score_for_perfect_output() -> None:
    rubric = _rubric()
    anchors = _anchor_set()
    judge = ScriptedJudge(
        responses={item.output: dict(item.hand_scores) for item in anchors.items}
        | {"candidate": {"faithfulness": 1.0, "clarity": 1.0}}
    )
    brief = Brief(
        brief_id="BRIEF_J",
        schema_version=1,
        kind=BriefKind.JUDGED,
        title="t",
        description="d",
        priority=BriefPriority.LOW,
        estimated_complexity=1,
        acceptance_criteria=("c",),
        limits=LimitsSpec(
            max_total_cost_usd=1.0,
            max_wall_clock_seconds=30,
            max_turns=4,
        ),
        rubric=rubric,
    )
    grade = grade_judged(
        brief, JudgedOutput(text="candidate"), judge=judge, anchors=anchors
    )
    assert grade.score == JUDGED_TOTAL


@pytest.mark.unit
def test_grade_judged_partial_score_quantized_to_ternary() -> None:
    rubric = _rubric()
    anchors = _anchor_set()
    judge = ScriptedJudge(
        responses={item.output: dict(item.hand_scores) for item in anchors.items}
        | {"candidate": {"faithfulness": 0.4, "clarity": 0.4}}
    )
    brief = Brief(
        brief_id="BRIEF_J",
        schema_version=1,
        kind=BriefKind.JUDGED,
        title="t",
        description="d",
        priority=BriefPriority.LOW,
        estimated_complexity=1,
        acceptance_criteria=("c",),
        limits=LimitsSpec(
            max_total_cost_usd=1.0,
            max_wall_clock_seconds=30,
            max_turns=4,
        ),
        rubric=rubric,
    )
    grade = grade_judged(
        brief, JudgedOutput(text="candidate"), judge=judge, anchors=anchors
    )
    # 0.4 ternary -> snaps to 0.5; weighted sum = 0.5 * 0.5 + 0.5 * 0.5 = 0.5 -> 50
    assert grade.score == JUDGED_TOTAL // 2


@pytest.mark.unit
def test_grade_judged_anchor_set_minimum_enforced() -> None:
    # Below MIN_PAIRS_FOR_CORRELATION -> spearman_rho raises.
    assert MIN_PAIRS_FOR_CORRELATION == 3
    rubric = _rubric()
    tiny_anchors = AnchorSet(
        rubric_id="summarise",
        anchor_set_version=1,
        items=(
            AnchorItem(
                anchor_id="only",
                output="only-output",
                hand_scores={"faithfulness": 1.0, "clarity": 1.0},
            ),
        ),
    )
    judge = ScriptedJudge(
        responses={"only-output": {"faithfulness": 1.0, "clarity": 1.0}}
    )
    with pytest.raises(ValueError, match="paired samples"):
        calibrate_judge(rubric, judge, tiny_anchors)


@pytest.mark.unit
def test_judged_grade_requires_calibration_passed() -> None:
    """JudgedGrade refuses construction with a calibration that did not pass."""
    from pydantic import ValidationError as PydValidationError

    from evals.models.scorecard import JudgeCalibrationReport
    from evals.scoring.judged import JudgedGrade

    failing = JudgeCalibrationReport(
        rubric_id="r",
        spearman_rho=0.3,
        gate=0.7,
        passed=False,
        anchor_count=5,
    )
    with pytest.raises(PydValidationError, match="did not pass"):
        JudgedGrade(score=50, calibration=failing)


@pytest.mark.unit
def test_path_traversal_rubric_id_rejected(tmp_path: Path) -> None:
    """A rubric_id that escapes anchors_dir via .. must be refused."""
    from evals.loader.anchors import load_anchor_set

    # Drop a legit file outside anchors_dir to make sure the check would
    # otherwise find something; the containment guard must block it.
    parent_yaml = tmp_path.parent / "summarise.yaml"
    parent_yaml.write_text("{}", encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="path traversal blocked"):
            load_anchor_set(tmp_path, "../summarise")
    finally:
        parent_yaml.unlink(missing_ok=True)


@pytest.mark.unit
def test_anchor_item_hand_scores_outside_range_rejected() -> None:
    """AnchorItem refuses hand_scores outside [HAND_SCORE_FLOOR, ...CEILING]."""
    from pydantic import ValidationError as PydValidationError

    from evals.loader.anchors import AnchorItem

    with pytest.raises(PydValidationError, match="outside"):
        AnchorItem(
            anchor_id="a1",
            output="o",
            hand_scores={"faithfulness": 1.5},
        )
    with pytest.raises(PydValidationError, match="outside"):
        AnchorItem(
            anchor_id="a1",
            output="o",
            hand_scores={"faithfulness": -0.1},
        )


@pytest.mark.unit
def test_scripted_judge_requires_at_least_one_data_source() -> None:
    """A ScriptedJudge with neither responses nor default_scores raises."""
    from pydantic import ValidationError as PydValidationError

    with pytest.raises(PydValidationError, match="at least one"):
        ScriptedJudge()


@pytest.mark.unit
def test_load_anchor_set_below_minimum_raises(tmp_path: Path) -> None:
    import yaml

    from evals.loader.anchors import MIN_ANCHOR_SET_SIZE, load_anchor_set

    payload = {
        "rubric_id": "summarise",
        "anchor_set_version": 1,
        "items": [
            {
                "anchor_id": f"a{i}",
                "output": f"o{i}",
                "hand_scores": {"faithfulness": 0.5},
            }
            for i in range(MIN_ANCHOR_SET_SIZE - 1)
        ],
    }
    (tmp_path / "summarise.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(JudgeAnchorSetTooSmallError):
        load_anchor_set(tmp_path, "summarise")
