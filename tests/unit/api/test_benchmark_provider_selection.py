"""Unit tests for the cost-dial benchmark-provider discriminator."""

import pytest

from synthorg.api._benchmark_wiring import select_benchmark_provider
from synthorg.budget.benchmark_measured import MeasuredBenchmarkScoreProvider
from synthorg.budget.benchmark_models import BenchmarkScoreRecord
from synthorg.budget.benchmark_stub import StubBenchmarkScoreProvider
from synthorg.budget.errors import UnknownBenchmarkProviderError
from synthorg.budget.model_tier import ModelTierMap
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

    async def test_stub_arm_honours_tier_map_overrides(self) -> None:
        # An override-only id (no archetype match) resolves its calibrated
        # cold-start score through the threaded tier map instead of None.
        tier_map = ModelTierMap(overrides={NotBlankStr("acme-frontier-x"): "large"})
        provider = select_benchmark_provider(
            "stub", repo=_StubRepo(), tier_map=tier_map
        )
        score = await provider.get_score(NotBlankStr("acme-frontier-x"))
        assert score is not None
        assert score.source == "stub:calibrated-v1"

    async def test_measured_fallback_honours_tier_map_overrides(self) -> None:
        # The measured arm's stub fallback resolves an override-only id on a
        # repository miss rather than dropping it.
        tier_map = ModelTierMap(overrides={NotBlankStr("acme-frontier-x"): "small"})
        provider = select_benchmark_provider(
            "measured", repo=_StubRepo(), tier_map=tier_map
        )
        score = await provider.get_score(NotBlankStr("acme-frontier-x"))
        assert score is not None
        assert score.source == "stub:calibrated-v1"
