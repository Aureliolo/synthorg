"""Unit tests for the review-pipeline factory discriminator."""

import pytest

from synthorg.client.models import (
    ClientFeedback,
    GenerationContext,
    PoolConstraints,
    ReviewContext,
    TaskRequirement,
)
from synthorg.client.protocols import ClientInterface
from synthorg.core.task import Task
from synthorg.engine.review.factory import build_review_pipeline
from synthorg.engine.review.models import ReviewStageResult, ReviewVerdict
from synthorg.engine.review.stages.client import ClientReviewStage
from synthorg.engine.review.stages.internal import InternalReviewStage

pytestmark = pytest.mark.unit


class _StubStage:
    """ReviewStage stub used to assert pipeline ordering."""

    @property
    def name(self) -> str:
        return "stub-verification"

    async def execute(self, task: Task) -> ReviewStageResult:  # pragma: no cover
        del task
        return ReviewStageResult(
            stage_name="stub-verification",
            verdict=ReviewVerdict.PASS,
        )


class _FirstClientStrategy:
    """Pool strategy returning the first candidate client."""

    async def select_clients(
        self,
        pool: tuple[ClientInterface, ...],
        constraints: PoolConstraints,
    ) -> tuple[ClientInterface, ...]:
        del constraints
        return pool[:1]


class _StubClient:
    """ClientInterface stub (never invoked in these tests)."""

    async def submit_requirement(
        self, context: GenerationContext
    ) -> TaskRequirement | None:  # pragma: no cover
        del context
        return None

    async def review_deliverable(
        self, context: ReviewContext
    ) -> ClientFeedback:  # pragma: no cover
        del context
        raise NotImplementedError


class TestBuildReviewPipeline:
    def test_internal_only_is_default(self) -> None:
        pipeline = build_review_pipeline()
        assert pipeline.stage_names == ("internal",)
        assert isinstance(pipeline.stages[0], InternalReviewStage)

    def test_internal_only_ignores_pool(self) -> None:
        pipeline = build_review_pipeline(
            strategy="internal_only",
            client_pool=(_StubClient(),),
            pool_strategy=_FirstClientStrategy(),
        )
        assert pipeline.stage_names == ("internal",)

    def test_client_then_internal_prepends_client_stage(self) -> None:
        pipeline = build_review_pipeline(
            strategy="client_then_internal",
            client_pool=(_StubClient(),),
            pool_strategy=_FirstClientStrategy(),
        )
        assert pipeline.stage_names == ("client", "internal")
        assert isinstance(pipeline.stages[0], ClientReviewStage)
        assert isinstance(pipeline.stages[1], InternalReviewStage)

    def test_client_strategy_degrades_without_pool(self) -> None:
        pipeline = build_review_pipeline(
            strategy="client_then_internal",
            pool_strategy=_FirstClientStrategy(),
        )
        assert pipeline.stage_names == ("internal",)

    def test_client_strategy_degrades_without_pool_strategy(self) -> None:
        pipeline = build_review_pipeline(
            strategy="client_then_internal",
            client_pool=(_StubClient(),),
        )
        assert pipeline.stage_names == ("internal",)

    def test_verification_stage_prepended_when_supplied(self) -> None:
        pipeline = build_review_pipeline(verification_stage=_StubStage())
        assert pipeline.stage_names == ("stub-verification", "internal")

    def test_verification_stage_runs_before_client_and_internal(self) -> None:
        pipeline = build_review_pipeline(
            strategy="client_then_internal",
            client_pool=(_StubClient(),),
            pool_strategy=_FirstClientStrategy(),
            verification_stage=_StubStage(),
        )
        assert pipeline.stage_names == ("stub-verification", "client", "internal")
