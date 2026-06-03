"""Unit tests for the cost-dial benchmark-provider discriminator."""

import pytest

from synthorg.api._benchmark_wiring import select_benchmark_provider
from synthorg.budget.benchmark_measured import MeasuredBenchmarkScoreProvider
from synthorg.budget.benchmark_models import BenchmarkScoreRecord
from synthorg.budget.benchmark_stub import StubBenchmarkScoreProvider
from synthorg.budget.errors import UnknownBenchmarkProviderError
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit


class _StubRepo:
    """Minimal ``BenchmarkScoreRepository`` for selection tests."""

    async def save(self, entity: BenchmarkScoreRecord) -> None:
        return None

    async def get(self, entity_id: NotBlankStr) -> BenchmarkScoreRecord | None:
        return None

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return False

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[BenchmarkScoreRecord, ...]:
        return ()


class TestSelectBenchmarkProvider:
    def test_stub_arm(self) -> None:
        provider = select_benchmark_provider("stub", repo=_StubRepo())
        assert isinstance(provider, StubBenchmarkScoreProvider)

    def test_measured_arm(self) -> None:
        provider = select_benchmark_provider("measured", repo=_StubRepo())
        assert isinstance(provider, MeasuredBenchmarkScoreProvider)

    def test_unknown_fails_loudly(self) -> None:
        with pytest.raises(UnknownBenchmarkProviderError):
            select_benchmark_provider("bogus", repo=_StubRepo())
