"""Heuristic rubric grader -- rule-based, deterministic."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

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


class HeuristicGraderConfig(BaseModel):
    """Operator-tunable thresholds + per-criterion grades for the heuristic grader.

    ``pass_threshold`` is the probe-pass-ratio cutoff between PASS
    and FAIL; ``pass_grade`` / ``fail_grade`` are the per-criterion
    scores assigned in each branch; ``confidence_ceiling`` clamps the
    derived confidence; ``confidence_bias`` bumps the floor so a 0%
    pass rate still produces non-zero confidence.

    Defaults match the historical hardcoded values; production wiring
    populates the fields from
    :func:`ConfigResolver.get_engine_bridge_config` so operators tune
    via ``/settings`` without code changes.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    pass_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    pass_grade: float = Field(default=0.8, ge=0.0, le=1.0)
    fail_grade: float = Field(default=0.3, ge=0.0, le=1.0)
    confidence_ceiling: float = Field(default=0.9, ge=0.0, le=1.0)
    confidence_bias: float = Field(default=0.1, ge=0.0, le=1.0)

    @classmethod
    def from_bridge_config(cls, bridge: object) -> HeuristicGraderConfig:
        """Project the heuristic-grader subset out of an ``EngineBridgeConfig``.

        See ``RoutingScorerConfig.from_bridge_config`` (in
        :mod:`synthorg.engine.routing.scorer`) for the import-cycle
        rationale behind the ``object``-typed parameter.
        """
        return cls(
            pass_threshold=bridge.quality_heuristic_pass_threshold,  # type: ignore[attr-defined]
            pass_grade=bridge.quality_heuristic_pass_grade,  # type: ignore[attr-defined]
            fail_grade=bridge.quality_heuristic_fail_grade,  # type: ignore[attr-defined]
            confidence_ceiling=bridge.quality_heuristic_confidence_ceiling,  # type: ignore[attr-defined]
            confidence_bias=bridge.quality_heuristic_confidence_bias,  # type: ignore[attr-defined]
        )


class HeuristicRubricGrader:
    """Rule-based grader for testing and deterministic fallback.

    Grades binary probes by checking whether the source criterion
    text appears (case-insensitive) in the payload values. Per-criterion
    scores and the pass/fail probe-ratio cutoff come from the injected
    :class:`HeuristicGraderConfig`. Empty probes produce a REFER
    verdict (cannot verify).
    """

    __slots__ = ("_config",)

    def __init__(self, config: HeuristicGraderConfig | None = None) -> None:
        self._config = config if config is not None else HeuristicGraderConfig()

    @property
    def name(self) -> str:
        """Strategy name."""
        return "heuristic"

    @property
    def config(self) -> HeuristicGraderConfig:
        """Snapshot of the operator-tunable grader thresholds."""
        return self._config

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

        cfg = self._config
        per_criterion_grades: dict[str, float] = {}
        for criterion in rubric.criteria:
            # All criteria share the global probe_ratio because the
            # data model has no probe-to-criterion mapping yet.
            per_criterion_grades[criterion.name] = (
                cfg.pass_grade if probe_ratio >= cfg.pass_threshold else cfg.fail_grade
            )

        confidence = min(cfg.confidence_ceiling, probe_ratio + cfg.confidence_bias)

        min_conf = rubric.min_confidence
        if confidence < min_conf:
            verdict = VerificationVerdict.REFER
        elif probe_ratio >= cfg.pass_threshold:
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
