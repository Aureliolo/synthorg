"""Unit tests for the rubric-grading verification review stage."""

from datetime import UTC, datetime

import pytest

from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.quality.verification import (
    AtomicProbe,
    VerificationResult,
    VerificationRubric,
    VerificationVerdict,
)
from synthorg.engine.quality.verification_config import VerificationConfig
from synthorg.engine.quality.verification_factory import (
    build_decomposer,
    build_grader,
)
from synthorg.engine.review.models import ReviewVerdict
from synthorg.engine.review.stages.verification import VerificationReviewStage
from synthorg.engine.workflow.handoff import HandoffArtifact
from tests._shared import FakeClock, as_uuid

pytestmark = pytest.mark.unit

_GENERATOR = NotBlankStr("agent-a")


def _criterion(text: str, *, met: bool) -> AcceptanceCriterion:
    return AcceptanceCriterion(description=NotBlankStr(text), met=met)


def _task(
    *,
    criteria: tuple[AcceptanceCriterion, ...] = (),
    assigned: NotBlankStr | None = _GENERATOR,
    status: TaskStatus = TaskStatus.IN_REVIEW,
    metadata: dict[str, object] | None = None,
) -> Task:
    return Task(
        id=as_uuid("task-1"),
        title=NotBlankStr("t"),
        description="d",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=NotBlankStr("proj"),
        created_by=NotBlankStr("creator"),
        assigned_to=assigned,
        status=status,
        acceptance_criteria=criteria,
        metadata=metadata or {},
    )


def _deterministic_stage() -> VerificationReviewStage:
    config = VerificationConfig()  # identity decomposer + heuristic grader
    return VerificationReviewStage(
        decomposer=build_decomposer(config),
        grader=build_grader(config),
    )


class _FailGrader:
    """RubricGrader that always returns a FAIL verdict."""

    @property
    def name(self) -> str:
        return "fail"

    async def grade(
        self,
        *,
        artifact: HandoffArtifact,
        rubric: VerificationRubric,
        probes: tuple[AtomicProbe, ...],
        generator_agent_id: NotBlankStr,
        evaluator_agent_id: NotBlankStr,
    ) -> VerificationResult:
        return VerificationResult(
            verdict=VerificationVerdict.FAIL,
            confidence=0.9,
            per_criterion_grades={c.name: 0.0 for c in rubric.criteria},
            findings=(NotBlankStr("forced fail"),),
            evaluator_agent_id=evaluator_agent_id,
            generator_agent_id=generator_agent_id,
            rubric_name=rubric.name,
            timestamp=datetime.now(UTC),
        )


class _BoomGrader:
    """RubricGrader that faults; the stage must fail open (SKIP)."""

    @property
    def name(self) -> str:
        return "boom"

    async def grade(self, **_kwargs: object) -> VerificationResult:
        msg = "grader exploded"
        raise ValueError(msg)


class _CaptureGrader:
    """RubricGrader that records the artifact it was handed, then PASSes."""

    def __init__(self) -> None:
        self.artifact: HandoffArtifact | None = None

    @property
    def name(self) -> str:
        return "capture"

    async def grade(
        self,
        *,
        artifact: HandoffArtifact,
        rubric: VerificationRubric,
        probes: tuple[AtomicProbe, ...],
        generator_agent_id: NotBlankStr,
        evaluator_agent_id: NotBlankStr,
    ) -> VerificationResult:
        self.artifact = artifact
        return VerificationResult(
            verdict=VerificationVerdict.PASS,
            confidence=0.9,
            per_criterion_grades={c.name: 1.0 for c in rubric.criteria},
            findings=(),
            evaluator_agent_id=evaluator_agent_id,
            generator_agent_id=generator_agent_id,
            rubric_name=rubric.name,
            timestamp=datetime.now(UTC),
        )


class TestVerificationReviewStage:
    async def test_all_criteria_met_passes(self) -> None:
        stage = _deterministic_stage()
        task = _task(
            criteria=(
                _criterion("login form works", met=True),
                _criterion("password reset works", met=True),
            ),
        )
        result = await stage.execute(task)
        assert result.verdict is ReviewVerdict.PASS
        assert result.metadata["verification_verdict"] == "pass"
        assert result.metadata["refer"] is False

    async def test_no_criteria_refers_but_does_not_fail(self) -> None:
        stage = _deterministic_stage()
        result = await stage.execute(_task(criteria=()))
        # No probes -> heuristic REFER; REFER is surfaced but does not
        # hard-fail the work.
        assert result.verdict is ReviewVerdict.PASS
        assert result.metadata["verification_verdict"] == "refer"
        assert result.metadata["refer"] is True

    async def test_unassigned_task_skips(self) -> None:
        stage = _deterministic_stage()
        task = _task(assigned=None, status=TaskStatus.CREATED)
        result = await stage.execute(task)
        assert result.verdict is ReviewVerdict.SKIP
        assert result.reason is not None
        assert "assignee" in result.reason

    async def test_grader_fault_fails_open_to_skip(self) -> None:
        config = VerificationConfig()
        stage = VerificationReviewStage(
            decomposer=build_decomposer(config),
            grader=_BoomGrader(),
        )
        task = _task(criteria=(_criterion("works", met=True),))
        result = await stage.execute(task)
        assert result.verdict is ReviewVerdict.SKIP
        assert result.reason is not None
        assert "grader fault" in result.reason

    async def test_missing_default_rubric_fails_open_to_skip(self) -> None:
        # A rubric catalog that lacks even the default must not crash the
        # stage: rubric resolution is inside the fail-open guard, so an
        # absent default SKIPs rather than blocking task completion.
        def _empty_catalog(_name: NotBlankStr) -> VerificationRubric:
            raise KeyError(_name)

        config = VerificationConfig()
        stage = VerificationReviewStage(
            decomposer=build_decomposer(config),
            grader=build_grader(config),
            rubric_lookup=_empty_catalog,
        )
        result = await stage.execute(_task(criteria=(_criterion("works", met=True),)))
        assert result.verdict is ReviewVerdict.SKIP
        assert result.reason is not None
        # A rubric-resolution fault is a SETUP fault, not a grader fault, so
        # an operator triages the right component.
        assert "setup fault" in result.reason

    async def test_fail_verdict_maps_to_fail(self) -> None:
        config = VerificationConfig()
        stage = VerificationReviewStage(
            decomposer=build_decomposer(config),
            grader=_FailGrader(),
        )
        task = _task(criteria=(_criterion("works", met=True),))
        result = await stage.execute(task)
        assert result.verdict is ReviewVerdict.FAIL
        assert result.metadata["verification_verdict"] == "fail"

    async def test_artifact_timestamp_uses_injected_clock(self) -> None:
        # The handoff-artifact ``created_at`` comes from the injected clock
        # seam, not wall time, so verification timestamps are deterministic
        # under test.
        fixed = datetime(2026, 3, 14, 9, 26, 53, tzinfo=UTC)
        config = VerificationConfig()
        grader = _CaptureGrader()
        stage = VerificationReviewStage(
            decomposer=build_decomposer(config),
            grader=grader,
            clock=FakeClock(start=fixed),
        )
        await stage.execute(_task(criteria=(_criterion("works", met=True),)))
        assert grader.artifact is not None
        assert grader.artifact.created_at == fixed

    async def test_evaluator_distinct_from_generator(self) -> None:
        stage = _deterministic_stage()
        task = _task(criteria=(_criterion("works", met=True),))
        result = await stage.execute(task)
        assert (
            result.metadata["evaluator_agent_id"]
            != result.metadata["generator_agent_id"]
        )

    async def test_evaluator_id_colliding_with_generator_is_suffixed(self) -> None:
        # When the configured evaluator id equals the generator, the
        # self-evaluation guard suffixes it (":auto") so verification is never
        # attributed to the generating agent.
        config = VerificationConfig()
        stage = VerificationReviewStage(
            decomposer=build_decomposer(config),
            grader=build_grader(config),
            evaluator_agent_id=_GENERATOR,
        )
        result = await stage.execute(_task(criteria=(_criterion("works", met=True),)))
        assert result.metadata["generator_agent_id"] == _GENERATOR
        assert result.metadata["evaluator_agent_id"] == f"{_GENERATOR}:auto"
