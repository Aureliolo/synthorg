"""Heuristic rubric grader -- rule-based, deterministic."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.engine.quality.verification import (
    VerificationResult,
    VerificationRubric,
    VerificationVerdict,
)
from synthorg.observability import get_logger
from synthorg.observability.events.verification import (
    VERIFICATION_GRADING_COMPLETED,
    VERIFICATION_GRADING_STARTED,
)

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.engine.quality.verification import AtomicProbe
    from synthorg.engine.workflow.handoff import HandoffArtifact

logger = get_logger(__name__)

_PASS_THRESHOLD = 0.5
_PASS_GRADE = 0.8
_FAIL_GRADE = 0.3
_CONFIDENCE_CEILING = 0.9
_CONFIDENCE_BIAS = 0.1


class HeuristicRubricGrader:
    """Rule-based grader for testing and deterministic fallback.

    Grades binary probes by checking whether the source criterion
    text appears (case-insensitive) in the payload values.  Scores
    each rubric criterion at a fixed 0.8 for PASS, 0.3 for FAIL.
    Empty probes produce a REFER verdict (cannot verify).
    """

    @property
    def name(self) -> str:
        """Strategy name."""
        return "heuristic"

    async def grade(
        self,
        *,
        artifact: HandoffArtifact,
        rubric: VerificationRubric,
        probes: tuple[AtomicProbe, ...],
        generator_agent_id: NotBlankStr,
        evaluator_agent_id: NotBlankStr,
    ) -> VerificationResult:
        """Grade via simple heuristic matching."""
        logger.info(
            VERIFICATION_GRADING_STARTED,
            rubric_name=rubric.name,
            grader=self.name,
            probe_count=len(probes),
        )
        payload_text = " ".join(str(v) for v in artifact.payload.values()).lower()

        if not probes:
            result = VerificationResult(
                verdict=VerificationVerdict.REFER,
                confidence=0.0,
                per_criterion_grades={c.name: 0.0 for c in rubric.criteria},
                findings=("Heuristic: no probes to evaluate",),
                evaluator_agent_id=evaluator_agent_id,
                generator_agent_id=generator_agent_id,
                rubric_name=rubric.name,
                timestamp=datetime.now(UTC),
            )
            logger.info(
                VERIFICATION_GRADING_COMPLETED,
                rubric_name=rubric.name,
                verdict=result.verdict.value,
                confidence=result.confidence,
            )
            return result

        probe_pass_count = sum(
            1 for p in probes if p.source_criterion.lower() in payload_text
        )
        probe_ratio = probe_pass_count / len(probes)

        per_criterion_grades: dict[str, float] = {}
        for criterion in rubric.criteria:
            # All criteria share the global probe_ratio because the
            # data model has no probe-to-criterion mapping yet.
            per_criterion_grades[criterion.name] = (
                _PASS_GRADE if probe_ratio >= _PASS_THRESHOLD else _FAIL_GRADE
            )

        confidence = min(_CONFIDENCE_CEILING, probe_ratio + _CONFIDENCE_BIAS)

        min_conf = rubric.min_confidence
        if confidence < min_conf:
            verdict = VerificationVerdict.REFER
        elif probe_ratio >= _PASS_THRESHOLD:
            verdict = VerificationVerdict.PASS
        else:
            verdict = VerificationVerdict.FAIL

        result = VerificationResult(
            verdict=verdict,
            confidence=confidence,
            per_criterion_grades=per_criterion_grades,
            findings=(f"Heuristic: {probe_pass_count}/{len(probes)} probes matched",),
            evaluator_agent_id=evaluator_agent_id,
            generator_agent_id=generator_agent_id,
            rubric_name=rubric.name,
            timestamp=datetime.now(UTC),
        )
        logger.info(
            VERIFICATION_GRADING_COMPLETED,
            rubric_name=rubric.name,
            verdict=result.verdict.value,
            confidence=result.confidence,
        )
        return result
