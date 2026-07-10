# module-kind: code
"""Rubric-grading review stage.

Wires the pluggable :class:`CriteriaDecomposer` + :class:`RubricGrader`
verification subsystem onto the live post-completion review path: it
decomposes a task's acceptance criteria into atomic probes, grades the
work against a calibrated rubric with a *separate* evaluator identity
(self-evaluation is rejected), and maps the structured verdict onto the
review pipeline's PASS / FAIL / SKIP verdict.

The deterministic default (identity decomposer + heuristic grader)
grades the proportion of acceptance criteria marked met, so the stage is
meaningful without a provider or the external artifact store. The
operator may switch to the LLM decomposer / grader via the
``simulations.verification_*`` settings.

A setup fault (rubric resolution / criteria decomposition) or a grader
fault both fail OPEN (the stage SKIPs) so a verifier defect can never
block task completion, but under distinct events so an operator triages
the right component. A REFER verdict (grader confidence below
the rubric threshold) does not hard-fail the work either: it is surfaced
in stage metadata for human review while the verdict stays PASS.
"""

import time
from collections.abc import Callable

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.quality.decomposer_protocol import CriteriaDecomposer
from synthorg.engine.quality.grader_protocol import RubricGrader
from synthorg.engine.quality.rubric_catalog import get_rubric
from synthorg.engine.quality.verification import (
    AtomicProbe,
    VerificationResult,
    VerificationRubric,
    VerificationVerdict,
)
from synthorg.engine.review.models import ReviewStageResult, ReviewVerdict
from synthorg.engine.workflow.handoff import HandoffArtifact
from synthorg.observability import (
    get_logger,
    safe_error_description,
)
from synthorg.observability.events.review_pipeline import (
    REVIEW_STAGE_DECIDED,
    REVIEW_STAGE_GRADER_FAULT,
    REVIEW_STAGE_RUBRIC_FALLBACK,
    REVIEW_STAGE_SETUP_FAULT,
)

logger = get_logger(__name__)

_DEFAULT_RUBRIC_NAME: NotBlankStr = NotBlankStr("default-task")
_DEFAULT_EVALUATOR_ID: NotBlankStr = NotBlankStr("verification-evaluator")
_RUBRIC_METADATA_KEY = "verification_rubric"

RubricLookup = Callable[[NotBlankStr], VerificationRubric]


class VerificationReviewStage:
    """Grades a task against a verification rubric as a review stage.

    Args:
        decomposer: Strategy that turns acceptance criteria into atomic
            probes (built via ``build_decomposer``).
        grader: Strategy that grades the artifact against the rubric
            (built via ``build_grader``).
        evaluator_agent_id: Identity that performs the evaluation. Must
            differ from the generator; a colliding id is suffixed so the
            self-evaluation guard never trips on the sentinel.
        rubric_lookup: Resolver from rubric name to rubric. Defaults to
            the built-in catalog.
        default_rubric_name: Rubric used when the task does not pin one.
        clock: Injectable time source for the handoff-artifact timestamp.
            Defaults to :class:`SystemClock`; tests inject ``FakeClock``.
    """

    _NAME: str = "verification"

    def __init__(  # noqa: PLR0913 -- DI seam: two strategies + evaluator identity + rubric lookup + clock
        self,
        *,
        decomposer: CriteriaDecomposer,
        grader: RubricGrader,
        evaluator_agent_id: NotBlankStr = _DEFAULT_EVALUATOR_ID,
        rubric_lookup: RubricLookup = get_rubric,
        default_rubric_name: NotBlankStr = _DEFAULT_RUBRIC_NAME,
        clock: Clock | None = None,
    ) -> None:
        self._decomposer = decomposer
        self._grader = grader
        self._evaluator_agent_id = evaluator_agent_id
        self._rubric_lookup = rubric_lookup
        self._default_rubric_name = default_rubric_name
        self._clock: Clock = clock if clock is not None else SystemClock()

    @property
    def name(self) -> str:
        """Stage identifier."""
        return self._NAME

    async def execute(self, task: Task) -> ReviewStageResult:
        """Decompose, grade, and map the verdict for *task*.

        Returns:
            A :class:`ReviewStageResult`. SKIP when the task has no
            assignee (no generator identity) or the grader faults
            (fail-open); otherwise the rubric verdict mapped onto the
            review pipeline.
        """
        start_ns = time.perf_counter_ns()
        generator = task.assigned_to
        if generator is None:
            return self._skip(
                "task has no assignee; cannot identify the generator",
                start_ns,
            )

        evaluator = self._distinct_evaluator(generator)
        # SETUP: rubric resolution + criteria decomposition. A fault here is a
        # rubric-catalog / decomposer defect, NOT a grader defect, so it fails
        # open under its own event so an operator triages the right component.
        try:
            rubric = self._resolve_rubric(task)
            artifact, probes = await self._prepare_grading(
                task, generator=generator, evaluator=evaluator
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised below
            # lint-allow: swallow-ok -- fail-open detector
            reraise_critical(exc)
            logger.warning(
                REVIEW_STAGE_SETUP_FAULT,
                stage=self._NAME,
                task_id=str(task.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return self._skip(
                f"verification setup fault: {safe_error_description(exc)}",
                start_ns,
                error_type=type(exc).__name__,
            )
        # GRADE: the real grading path. A fault here is a grader defect.
        try:
            result = await self._grader.grade(
                artifact=artifact,
                rubric=rubric,
                probes=probes,
                generator_agent_id=generator,
                evaluator_agent_id=evaluator,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised below
            # lint-allow: swallow-ok -- fail-open detector
            reraise_critical(exc)
            # WARNING, not INFO: a grader fault is an unexpected verifier
            # defect an operator must see, distinct from a routine skip.
            logger.warning(
                REVIEW_STAGE_GRADER_FAULT,
                stage=self._NAME,
                task_id=str(task.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return self._skip(
                f"verification grader fault: {safe_error_description(exc)}",
                start_ns,
                error_type=type(exc).__name__,
            )
        return self._to_stage_result(result, rubric=rubric, start_ns=start_ns)

    def _distinct_evaluator(self, generator: NotBlankStr) -> NotBlankStr:
        """Return an evaluator id guaranteed to differ from the generator."""
        if self._evaluator_agent_id != generator:
            return self._evaluator_agent_id
        return NotBlankStr(f"{self._evaluator_agent_id}:auto")

    def _resolve_rubric(self, task: Task) -> VerificationRubric:
        """Resolve the rubric pinned on the task, else the default.

        An unknown pinned rubric falls back to the default with a
        warning rather than failing the stage.

        Returns:
            The pinned rubric when valid, otherwise the default rubric.

        Raises:
            KeyError: When the default rubric itself is absent; the caller's
                fail-open guard turns this into a SKIP.
        """
        pinned = task.metadata.get(_RUBRIC_METADATA_KEY)
        name = self._default_rubric_name
        if isinstance(pinned, str) and pinned.strip():
            name = NotBlankStr(pinned.strip())
        try:
            return self._rubric_lookup(name)
        except KeyError:
            if name == self._default_rubric_name:
                # The default itself is missing: re-looking up the same key
                # would raise again. Let it propagate to execute()'s fail-open
                # guard, which SKIPs rather than blocking the task.
                raise
            logger.warning(
                REVIEW_STAGE_RUBRIC_FALLBACK,
                stage=self._NAME,
                task_id=str(task.id),
                requested_rubric=name,
                default_rubric=self._default_rubric_name,
            )
            return self._rubric_lookup(self._default_rubric_name)

    async def _prepare_grading(
        self,
        task: Task,
        *,
        generator: NotBlankStr,
        evaluator: NotBlankStr,
    ) -> tuple[HandoffArtifact, tuple[AtomicProbe, ...]]:
        """Decompose acceptance criteria and build the grading artifact.

        This is the setup phase that runs before the grader: a fault here
        (decomposer defect) is distinct from a grader fault, so the caller
        guards it under :data:`REVIEW_STAGE_SETUP_FAULT`.

        Returns:
            The ``(artifact, probes)`` pair the grader scores.
        """
        task_id = NotBlankStr(str(task.id))
        # Decompose under the evaluator identity (not the generator): the whole
        # verification stage runs as the evaluator, so decomposition cost /
        # guarding must be attributed to it too, matching the grade() call.
        probes = await self._decomposer.decompose(
            task.acceptance_criteria,
            task_id=task_id,
            agent_id=evaluator,
        )
        # The deterministic default grades the proportion of acceptance
        # criteria marked met; the met-criteria text is the artifact
        # surface the heuristic grader matches probes against, so the
        # grade reflects acceptance-criteria completion without needing
        # the external artifact store.
        met_text = " ".join(c.description for c in task.acceptance_criteria if c.met)
        artifact = HandoffArtifact(
            created_at=self._clock.now(),
            from_agent_id=generator,
            to_agent_id=evaluator,
            from_stage=NotBlankStr("generator"),
            to_stage=NotBlankStr("evaluator"),
            payload={"met_criteria": met_text},
            acceptance_probes=probes,
        )
        return artifact, probes

    def _to_stage_result(
        self,
        result: VerificationResult,
        *,
        rubric: VerificationRubric,
        start_ns: int,
    ) -> ReviewStageResult:
        """Map a verification verdict onto a review stage result.

        Returns:
            A PASS / FAIL :class:`ReviewStageResult` carrying the
            structured grade in its metadata (REFER maps to PASS).
        """
        duration_ms = max(0, (time.perf_counter_ns() - start_ns) // 1_000_000)
        findings = "; ".join(result.findings) or None
        is_refer = result.verdict is VerificationVerdict.REFER
        # FAIL bounces the task for rework; PASS and REFER both let the
        # task proceed (REFER = escalate-to-human, surfaced in metadata
        # rather than hard-failing uncertain work).
        verdict = (
            ReviewVerdict.FAIL
            if result.verdict is VerificationVerdict.FAIL
            else ReviewVerdict.PASS
        )
        metadata: dict[str, object] = {
            "verification_verdict": result.verdict.value,
            "confidence": result.confidence,
            "per_criterion_grades": dict(result.per_criterion_grades),
            "findings": list(result.findings),
            "evaluator_agent_id": result.evaluator_agent_id,
            "generator_agent_id": result.generator_agent_id,
            "rubric_name": rubric.name,
            "refer": is_refer,
        }
        logger.info(
            REVIEW_STAGE_DECIDED,
            stage=self._NAME,
            verdict=verdict.value,
            verification_verdict=result.verdict.value,
            confidence=result.confidence,
            duration_ms=duration_ms,
        )
        return ReviewStageResult(
            stage_name=self._NAME,
            verdict=verdict,
            reason=findings,
            duration_ms=duration_ms,
            metadata=metadata,
        )

    def _skip(
        self,
        reason: str,
        start_ns: int,
        **extra: object,
    ) -> ReviewStageResult:
        """Build a SKIP result with *reason* and stage timing.

        Returns:
            A SKIP :class:`ReviewStageResult` carrying *reason*.
        """
        duration_ms = max(0, (time.perf_counter_ns() - start_ns) // 1_000_000)
        logger.info(
            REVIEW_STAGE_DECIDED,
            stage=self._NAME,
            verdict=ReviewVerdict.SKIP.value,
            reason=reason,
            duration_ms=duration_ms,
            **extra,
        )
        return ReviewStageResult(
            stage_name=self._NAME,
            verdict=ReviewVerdict.SKIP,
            reason=reason,
            duration_ms=duration_ms,
        )
