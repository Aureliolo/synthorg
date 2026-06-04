"""Unit tests for MeasuredBenchmarkScoreProvider (both arms)."""

from datetime import UTC, datetime

import pytest

from synthorg.budget.benchmark_measured import MeasuredBenchmarkScoreProvider
from synthorg.budget.benchmark_models import BenchmarkScoreRecord
from synthorg.budget.benchmark_stub import StubBenchmarkScoreProvider
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
        await repo.save(_record("example-large-001", 90.0))
        provider = MeasuredBenchmarkScoreProvider(
            repo, fallback=StubBenchmarkScoreProvider()
        )

        score = await provider.get_score(NotBlankStr("example-large-001"))
        assert score is not None
        assert score.score == pytest.approx(90.0)
        assert score.source == "benchmark:measured-v1"

    async def test_miss_falls_through_to_stub(self) -> None:
        repo = _InMemoryBenchmarkScoreRepository()
        provider = MeasuredBenchmarkScoreProvider(
            repo, fallback=StubBenchmarkScoreProvider()
        )

        # No measured row for medium -> stub calibrated constant.
        score = await provider.get_score(NotBlankStr("example-medium-001"))
        assert score is not None
        assert score.score == pytest.approx(85.0)
        assert score.source == "stub:calibrated-v1"

    async def test_miss_without_fallback_returns_none(self) -> None:
        repo = _InMemoryBenchmarkScoreRepository()
        provider = MeasuredBenchmarkScoreProvider(repo)
        assert await provider.get_score(NotBlankStr("example-medium-001")) is None

    async def test_list_scores_measured_overrides_stub(self) -> None:
        repo = _InMemoryBenchmarkScoreRepository()
        await repo.save(_record("example-large-001", 99.0))
        provider = MeasuredBenchmarkScoreProvider(
            repo, fallback=StubBenchmarkScoreProvider()
        )

        scores = await provider.list_scores()
        # Stub contributes all four tier representatives; the measured
        # row overrides the large one.
        assert scores[NotBlankStr("example-large-001")].score == pytest.approx(99.0)
        assert (
            scores[NotBlankStr("example-large-001")].source == "benchmark:measured-v1"
        )
        assert scores[NotBlankStr("example-medium-001")].source == "stub:calibrated-v1"
