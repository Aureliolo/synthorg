"""Direct unit tests for the quality facade services."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.task_enums import Complexity, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.quality.mcp_services import (
    EvaluationVersionService,
    QualityFacadeService,
    ReviewFacadeService,
)
from synthorg.hr.performance.models import TaskMetricRecord

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _record(
    *,
    agent_id: str,
    quality_score: float | None,
    completed_at: datetime = _NOW,
) -> TaskMetricRecord:
    """Build a scored task-metric record for the quality facade tests.

    Returns:
        The constructed task-metric record.
    """
    return TaskMetricRecord(
        agent_id=NotBlankStr(agent_id),
        task_id=NotBlankStr(f"task-{agent_id}-{completed_at.isoformat()}"),
        task_type=TaskType.DEVELOPMENT,
        completed_at=completed_at,
        is_success=True,
        duration_seconds=10.0,
        cost=0.01,
        currency="EUR",
        turns_used=2,
        tokens_used=150,
        quality_score=quality_score,
        complexity=Complexity.SIMPLE,
    )


class _FakeTracker:
    """Minimal tracker exposing only ``get_task_metrics`` for the facade."""

    def __init__(self, records: tuple[TaskMetricRecord, ...]) -> None:
        self._records = records

    def get_task_metrics(
        self,
        *,
        agent_id: NotBlankStr | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[TaskMetricRecord, ...]:
        if agent_id is None:
            return self._records
        return tuple(r for r in self._records if r.agent_id == agent_id)


# ── QualityFacadeService ──────────────────────────────────────────


class TestQualityFacadeService:
    async def test_get_summary_capability_gap_without_tracker_method(self) -> None:
        service = QualityFacadeService(tracker=SimpleNamespace())  # type: ignore[arg-type]
        with pytest.raises(CapabilityNotSupportedError):
            await service.get_summary()

    async def test_get_agent_quality_capability_gap(self) -> None:
        service = QualityFacadeService(tracker=SimpleNamespace())  # type: ignore[arg-type]
        with pytest.raises(CapabilityNotSupportedError):
            await service.get_agent_quality(NotBlankStr("agent-1"))

    async def test_list_scores_capability_gap(self) -> None:
        service = QualityFacadeService(tracker=SimpleNamespace())  # type: ignore[arg-type]
        with pytest.raises(CapabilityNotSupportedError):
            await service.list_scores()

    async def test_get_summary_aggregates_real_data(self) -> None:
        tracker = _FakeTracker(
            (
                _record(agent_id="a", quality_score=8.0),
                _record(agent_id="a", quality_score=6.0),
                _record(agent_id="b", quality_score=10.0),
                _record(agent_id="b", quality_score=None),
            )
        )
        service = QualityFacadeService(tracker=tracker)  # type: ignore[arg-type]
        summary = await service.get_summary()
        assert summary["agent_count"] == 2
        assert summary["scored_task_count"] == 3
        assert summary["overall_quality_score"] == pytest.approx(8.0)
        agent_rows = cast(Sequence[Mapping[str, object]], summary["agents"])
        agents = {row["agent_id"]: row for row in agent_rows}
        assert agents["a"]["average_quality_score"] == pytest.approx(7.0)
        assert agents["b"]["scored_task_count"] == 1

    async def test_list_scores_filters_sorts_and_paginates(self) -> None:
        tracker = _FakeTracker(
            (
                _record(agent_id="a", quality_score=5.0, completed_at=_NOW),
                _record(
                    agent_id="a",
                    quality_score=9.0,
                    completed_at=_NOW + timedelta(hours=1),
                ),
                _record(agent_id="b", quality_score=7.0),
            )
        )
        service = QualityFacadeService(tracker=tracker)  # type: ignore[arg-type]
        page, total = await service.list_scores(
            agent_id=NotBlankStr("a"), offset=0, limit=1
        )
        assert total == 2
        assert len(page) == 1
        # Newest-first ordering: the 9.0 score (later completed_at) comes first.
        assert page[0].quality_score == pytest.approx(9.0)


# ── ReviewFacadeService ───────────────────────────────────────────


class TestReviewFacadeService:
    async def test_create_then_get(self) -> None:
        service = ReviewFacadeService()
        created = await service.create_review(
            task_id=NotBlankStr("task-1"),
            reviewer_id=NotBlankStr("bob"),
            verdict=NotBlankStr("approve"),
        )
        fetched = await service.get_review(NotBlankStr(str(created.id)))
        assert fetched is not None
        assert fetched.verdict == "approve"

    async def test_list_is_newest_first(self) -> None:
        service = ReviewFacadeService()
        first = await service.create_review(
            task_id=NotBlankStr("t1"),
            reviewer_id=NotBlankStr("r"),
            verdict=NotBlankStr("approve"),
        )
        second = await service.create_review(
            task_id=NotBlankStr("t2"),
            reviewer_id=NotBlankStr("r"),
            verdict=NotBlankStr("reject"),
        )
        page, total = await service.list_reviews()
        assert total == 2
        assert page[0].id == second.id
        assert page[1].id == first.id

    async def test_update_patches_fields(self) -> None:
        service = ReviewFacadeService()
        created = await service.create_review(
            task_id=NotBlankStr("t"),
            reviewer_id=NotBlankStr("r"),
            verdict=NotBlankStr("pending"),
        )
        updated = await service.update_review(
            review_id=NotBlankStr(str(created.id)),
            verdict=NotBlankStr("approve"),
            comments="looks good",
            actor_id=NotBlankStr("r"),
        )
        assert updated is not None
        assert updated.verdict == "approve"
        assert updated.comments == "looks good"

    async def test_update_missing_returns_none(self) -> None:
        service = ReviewFacadeService()
        result = await service.update_review(
            review_id=NotBlankStr(str(uuid4())),
            actor_id=NotBlankStr("r"),
        )
        assert result is None

    async def test_update_invalid_uuid_returns_none(self) -> None:
        service = ReviewFacadeService()
        result = await service.update_review(
            review_id=NotBlankStr("bad"),
            actor_id=NotBlankStr("r"),
        )
        assert result is None

    async def test_get_invalid_uuid_returns_none(self) -> None:
        service = ReviewFacadeService()
        assert await service.get_review(NotBlankStr("bad")) is None


# ── EvaluationVersionService ──────────────────────────────────────


class TestEvaluationVersionService:
    async def test_list_versions_capability_gap_when_unwired(self) -> None:
        service = EvaluationVersionService(persistence=None)
        with pytest.raises(CapabilityNotSupportedError):
            await service.list_versions()

    async def test_get_version_capability_gap_when_unwired(self) -> None:
        service = EvaluationVersionService(persistence=None)
        with pytest.raises(CapabilityNotSupportedError):
            await service.get_version(NotBlankStr("v1"))

    async def test_list_versions_capability_gap_without_accessor(self) -> None:
        service = EvaluationVersionService(
            persistence=SimpleNamespace(),
        )
        with pytest.raises(CapabilityNotSupportedError):
            await service.list_versions()

    async def test_get_version_capability_gap_without_accessor(self) -> None:
        service = EvaluationVersionService(
            persistence=SimpleNamespace(),
        )
        with pytest.raises(CapabilityNotSupportedError):
            await service.get_version(NotBlankStr("v1"))

    async def test_list_versions_delegates_when_available(self) -> None:
        class _Repo:
            async def list_versions(self) -> tuple[object, ...]:
                return ("v1", "v2")

        persistence = SimpleNamespace(evaluation_config_versions=_Repo())
        service = EvaluationVersionService(persistence=persistence)
        assert await service.list_versions() == ("v1", "v2")

    async def test_get_version_delegates_when_available(self) -> None:
        class _Repo:
            async def get_version(self, vid: str) -> object | None:
                return {"id": vid}

        persistence = SimpleNamespace(evaluation_config_versions=_Repo())
        service = EvaluationVersionService(persistence=persistence)
        assert await service.get_version(NotBlankStr("v1")) == {"id": "v1"}
