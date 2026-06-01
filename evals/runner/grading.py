# module-kind: code
"""Grade one brief's execution outcome into a ``[0, 100]`` integer grade.

Dispatches by :class:`~evals.models.brief.BriefKind`:

* ``judged`` -- a calibrated judge scores the deliverable text against the
  brief's rubric, gated by ordinal calibration against the anchor set.
* ``executable`` -- the brief's hidden-test / build / lint commands run against
  the produced workspace directory.
* ``research`` -- requires a research-mode integration the agent runner does
  not provide; refused rather than scored as a fabricated zero.
"""

from pathlib import Path

from evals.errors import BriefExecutionError, ResearchBriefUnsupportedError
from evals.loader.anchors import load_anchor_set
from evals.models.brief import Brief, BriefKind
from evals.models.scorecard import JudgeCalibrationReport
from evals.scoring.executable import grade_executable
from evals.scoring.judged import JudgedOutput, JudgeProtocol, grade_judged


def grade_brief(
    brief: Brief,
    *,
    deliverable_text: str | None,
    work_dir: Path,
    judge: JudgeProtocol,
    anchors_dir: Path,
) -> tuple[int, JudgeCalibrationReport | None]:
    """Grade *brief* and return ``(grade, judge_calibration)``.

    Args:
        brief: The exam item to grade.
        deliverable_text: The agent's final output text (judged briefs). An
            empty / ``None`` deliverable is scored on its merits (typically 0).
        work_dir: The agent's workspace directory (executable briefs).
        judge: Calibrated judge implementation for judged briefs.
        anchors_dir: Directory holding ``<rubric_id>.yaml`` anchor sets.

    Returns:
        ``(grade, judge_calibration)`` where ``grade`` is in ``[0, 100]`` and
        ``judge_calibration`` is populated only for judged briefs.

    Raises:
        BriefExecutionError: A judged brief is missing its rubric block.
        ResearchBriefUnsupportedError: The brief is a research brief.
        JudgeCalibrationFailedError: The judge fails its ordinal gate.
        EvalToolMissingError: An executable brief's command is not on PATH.
    """
    if brief.kind is BriefKind.JUDGED:
        if brief.rubric is None:
            msg = f"judged brief {brief.brief_id!r} is missing its rubric block"
            raise BriefExecutionError(msg)
        anchors = load_anchor_set(anchors_dir, brief.rubric.rubric_id)
        output = JudgedOutput(text=deliverable_text or "")
        graded = grade_judged(brief, output, judge=judge, anchors=anchors)
        return graded.score, graded.calibration
    if brief.kind is BriefKind.EXECUTABLE:
        graded_exec = grade_executable(brief, work_dir)
        return graded_exec.score, None
    msg = (
        f"research brief {brief.brief_id!r} requires a research-mode runner integration"
    )
    raise ResearchBriefUnsupportedError(msg)


__all__ = ["grade_brief"]
