"""Unit tests for MeasuredBenchmarkScoreProvider."""

from datetime import UTC, datetime

import pytest

from synthorg.budget.benchmark_measured import MeasuredBenchmarkScoreProvider
from synthorg.budget.benchmark_models import BenchmarkScoreRecord
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 20, tzinfo=UTC)


class _InMemoryBenchmarkScoreRepository:
    """Minimal in-memory ``BenchmarkScoreRepository`` for unit tests."""

    def __init__(self) -> None:
        self._rows: dict[str, BenchmarkScoreRecord] = {}

    async def save(self, entity: BenchmarkScoreRecord) -> None:
        self._rows[entity.model_id] = entity

    async def get(self, entity_id: NotBlankStr) -> BenchmarkScoreRecord | None:
        return self._rows.get(entity_id)

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return self._rows.pop(entity_id, None) is not None

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[BenchmarkScoreRecord, ...]:
        ordered = sorted(self._rows.values(), key=lambda r: r.model_id)
        return tuple(ordered[offset : offset + limit])


def _record(model_id: str, score: float) -> BenchmarkScoreRecord:
    return BenchmarkScoreRecord(
        model_id=NotBlankStr(model_id),
        score=score,
        confidence_lower=max(0.0, score - 4.0),
        confidence_upper=min(100.0, score + 3.0),
        source=NotBlankStr("benchmark:measured-v1"),
        suite_version=NotBlankStr("sha256:abc123"),
        cassette_sha256=NotBlankStr("deadbeef"),
        last_updated=_NOW,
    )


class TestMeasuredBenchmarkScoreProvider:
    async def test_measured_hit_returns_benchmark_source(self) -> None:
        repo = _InMemoryBenchmarkScoreRepository()
        await repo.save(_record("example-expert-001", 90.0))
        provider = MeasuredBenchmarkScoreProvider(repo)

        score = await provider.get_score(NotBlankStr("example-expert-001"))
        assert score is not None
        assert score.score == pytest.approx(90.0)
        assert score.source == "benchmark:measured-v1"

    async def test_unmeasured_model_returns_none(self) -> None:
        repo = _InMemoryBenchmarkScoreRepository()
        provider = MeasuredBenchmarkScoreProvider(repo)
        # No measured row -> absent, never a fabricated score.
        assert await provider.get_score(NotBlankStr("example-capable-001")) is None

    async def test_list_scores_returns_measured_rows_only(self) -> None:
        repo = _InMemoryBenchmarkScoreRepository()
        await repo.save(_record("example-expert-001", 99.0))
        await repo.save(_record("example-basic-001", 60.0))
        provider = MeasuredBenchmarkScoreProvider(repo)

        scores = await provider.list_scores()
        assert set(scores) == {
            NotBlankStr("example-expert-001"),
            NotBlankStr("example-basic-001"),
        }
        assert scores[NotBlankStr("example-expert-001")].score == pytest.approx(99.0)
        assert (
            scores[NotBlankStr("example-expert-001")].source == "benchmark:measured-v1"
        )
