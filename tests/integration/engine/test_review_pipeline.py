"""Integration tests for the review pipeline and stages."""

import pytest

from synthorg.client import (
    AIClient,
    ClientInterface,
    ClientProfile,
    PoolConstraints,
)
from synthorg.client.feedback import BinaryFeedback
from synthorg.client.generators import ProceduralGenerator
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.engine.review import (
    ClientReviewStage,
    InternalReviewStage,
    PipelineResult,
    ReviewPipeline,
    ReviewStageResult,
    ReviewVerdict,
)
from tests._shared import as_uuid

pytestmark = pytest.mark.integration


def _task(
    *,
    status: TaskStatus = TaskStatus.IN_REVIEW,
    criteria: tuple[str, ...] = ("First criterion",),
    title: str = "Test task",
    description: str = (
        "A test task description long enough to pass binary feedback thresholds easily."
    ),
) -> Task:
    acceptance = tuple(AcceptanceCriterion(description=c) for c in criteria)
    return Task(
        id=as_uuid("task-1"),
        title=title,
        description=description,
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-1",
        created_by="author",
        assigned_to="worker",
        status=status,
        acceptance_criteria=acceptance,
    )


class _FirstClientStrategy:
    """Test pool strategy that returns the first client."""

    async def select_clients(
        self,
        pool: tuple[ClientInterface, ...],
        constraints: PoolConstraints,
    ) -> tuple[ClientInterface, ...]:
        del constraints
        return pool[:1]


class _StaticStage:
    """Test stage that returns a pre-configured verdict."""

    def __init__(
        self,
        *,
        stage_name: str,
        verdict: ReviewVerdict,
        reason: str | None = None,
    ) -> None:
        self._name = stage_name
        self._verdict = verdict
        self._reason = reason

    @property
    def name(self) -> str:
        return self._name

    async def execute(self, task: Task) -> ReviewStageResult:
        del task
        return ReviewStageResult(
            stage_name=self._name,
            verdict=self._verdict,
            reason=self._reason,
        )


class TestReviewPipelineShape:
    async def test_empty_pipeline_passes(self) -> None:
        pipeline = ReviewPipeline(stages=())
        result = await pipeline.run(_task())
        assert isinstance(result, PipelineResult)
        assert result.final_verdict is ReviewVerdict.PASS
        assert result.stage_results == ()

    async def test_single_stage_pass(self) -> None:
        pipeline = ReviewPipeline(stages=(InternalReviewStage(),))
        result = await pipeline.run(_task())
        assert result.final_verdict is ReviewVerdict.PASS
        assert len(result.stage_results) == 1
        assert result.stage_results[0].stage_name == "internal"

    async def test_pipeline_result_has_total_duration(self) -> None:
        pipeline = ReviewPipeline(stages=(InternalReviewStage(),))
        result = await pipeline.run(_task())
        assert result.total_duration_ms >= 0


class TestPipelineShortCircuit:
    async def test_fail_stops_pipeline(self) -> None:
        failing = _StaticStage(
            stage_name="fail-stage",
            verdict=ReviewVerdict.FAIL,
            reason="boom",
        )
        follow_up_calls: list[str] = []

        class _RecordingStage:
            name = "recording"

            async def execute(self, task: Task) -> ReviewStageResult:
                follow_up_calls.append(str(task.id))
                return ReviewStageResult(
                    stage_name="recording",
                    verdict=ReviewVerdict.PASS,
                )

        pipeline = ReviewPipeline(
            stages=(failing, _RecordingStage()),
        )
        result = await pipeline.run(_task())
        assert result.final_verdict is ReviewVerdict.FAIL
        assert len(result.stage_results) == 1
        assert follow_up_calls == []

    async def test_skip_continues(self) -> None:
        pipeline = ReviewPipeline(
            stages=(
                _StaticStage(
                    stage_name="skippable",
                    verdict=ReviewVerdict.SKIP,
                    reason="not applicable",
                ),
                _StaticStage(
                    stage_name="final",
                    verdict=ReviewVerdict.PASS,
                ),
            ),
        )
        result = await pipeline.run(_task())
        assert result.final_verdict is ReviewVerdict.PASS
        assert len(result.stage_results) == 2

    async def test_multi_stage_all_pass(self) -> None:
        pipeline = ReviewPipeline(
            stages=(
                _StaticStage(stage_name="first", verdict=ReviewVerdict.PASS),
                _StaticStage(stage_name="second", verdict=ReviewVerdict.PASS),
                _StaticStage(stage_name="third", verdict=ReviewVerdict.PASS),
            ),
        )
        result = await pipeline.run(_task())
        assert result.final_verdict is ReviewVerdict.PASS
        assert [r.stage_name for r in result.stage_results] == [
            "first",
            "second",
            "third",
        ]


class TestInternalReviewStage:
    async def test_passes_when_task_is_valid(self) -> None:
        stage = InternalReviewStage()
        result = await stage.execute(_task())
        assert result.stage_name == "internal"
        assert result.verdict is ReviewVerdict.PASS

    async def test_fails_when_status_is_not_in_review(self) -> None:
        stage = InternalReviewStage()
        result = await stage.execute(_task(status=TaskStatus.IN_PROGRESS))
        assert result.verdict is ReviewVerdict.FAIL
        assert result.reason is not None
        assert "in_review" in result.reason

    async def test_fails_without_acceptance_criteria(self) -> None:
        stage = InternalReviewStage()
        result = await stage.execute(_task(criteria=()))
        assert result.verdict is ReviewVerdict.FAIL
        assert result.reason is not None
        assert "acceptance" in result.reason.lower()

    async def test_require_criteria_met(self) -> None:
        # Build a task manually with an unmet criterion
        task = Task(
            id=as_uuid("task-2"),
            title="Test",
            description="A substantial description of the work done",
            type=TaskType.DEVELOPMENT,
            priority=Priority.MEDIUM,
            project="proj-1",
            created_by="author",
            assigned_to="worker",
            status=TaskStatus.IN_REVIEW,
            acceptance_criteria=(
                AcceptanceCriterion(description="Incomplete", met=False),
            ),
        )
        stage = InternalReviewStage(require_all_criteria_met=True)
        result = await stage.execute(task)
        assert result.verdict is ReviewVerdict.FAIL
        assert result.reason is not None
        assert "not met" in result.reason


class TestClientReviewStage:
    def _make_client(self, *, client_id: str = "client-1") -> AIClient:
        return AIClient(
            profile=ClientProfile(
                client_id=client_id,
                name="Test",
                persona="Tester",
            ),
            generator=ProceduralGenerator(seed=1),
            feedback=BinaryFeedback(client_id=client_id),
        )

    async def test_skips_with_empty_pool(self) -> None:
        stage = ClientReviewStage(
            pool=(),
            strategy=_FirstClientStrategy(),
        )
        result = await stage.execute(_task())
        assert result.verdict is ReviewVerdict.SKIP

    async def test_passes_when_client_accepts(self) -> None:
        client = self._make_client()
        stage = ClientReviewStage(
            pool=(client,),
            strategy=_FirstClientStrategy(),
        )
        result = await stage.execute(_task())
        assert result.verdict is ReviewVerdict.PASS
        assert result.metadata["client_id"] == "client-1"

    async def test_fails_when_client_rejects(self) -> None:
        class _RejectingFeedback:
            async def evaluate(self, context):  # type: ignore[no-untyped-def]
                from synthorg.client.models import ClientFeedback

                return ClientFeedback(
                    task_id=context.task_id,
                    client_id="client-1",
                    accepted=False,
                    reason="not good enough",
                    unmet_criteria=("First criterion",),
                )

        client = AIClient(
            profile=ClientProfile(
                client_id="client-1",
                name="Test",
                persona="Tester",
            ),
            generator=ProceduralGenerator(seed=1),
            feedback=_RejectingFeedback(),
        )
        stage = ClientReviewStage(
            pool=(client,),
            strategy=_FirstClientStrategy(),
        )
        result = await stage.execute(_task())
        assert result.verdict is ReviewVerdict.FAIL
        assert result.reason == "not good enough"
        assert result.metadata["unmet_criteria"] == ["First criterion"]

    async def test_skip_when_strategy_returns_nothing(self) -> None:
        class _EmptyStrategy:
            async def select_clients(
                self,
                pool: tuple[ClientInterface, ...],
                constraints: PoolConstraints,
            ) -> tuple[ClientInterface, ...]:
                del pool, constraints
                return ()

        client = self._make_client()
        stage = ClientReviewStage(
            pool=(client,),
            strategy=_EmptyStrategy(),
        )
        result = await stage.execute(_task())
        assert result.verdict is ReviewVerdict.SKIP


class TestFullPipelineWithClientStage:
    async def test_internal_plus_client(self) -> None:
        client = AIClient(
            profile=ClientProfile(
                client_id="reviewer",
                name="Reviewer",
                persona="Strict reviewer",
            ),
            generator=ProceduralGenerator(seed=1),
            feedback=BinaryFeedback(client_id="reviewer"),
        )
        pipeline = ReviewPipeline(
            stages=(
                InternalReviewStage(),
                ClientReviewStage(
                    pool=(client,),
                    strategy=_FirstClientStrategy(),
                ),
            ),
        )
        result = await pipeline.run(_task())
        assert result.final_verdict is ReviewVerdict.PASS
        assert [r.stage_name for r in result.stage_results] == [
            "internal",
            "client",
        ]
