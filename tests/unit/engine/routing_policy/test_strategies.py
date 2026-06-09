"""Unit tests for stakes-aware routing strategies and factory."""

from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from synthorg.budget.benchmark_protocol import BenchmarkScore
from synthorg.budget.benchmark_stub import StubBenchmarkScoreProvider
from synthorg.budget.coordination_metric_models import (
    CoordinationMetrics,
    ErrorAmplification,
)
from synthorg.budget.coordination_store import (
    CoordinationMetricsRecord,
    CoordinationMetricsStore,
)
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.core.task import Task
from synthorg.core.task_enums import Stakes, TaskType
from synthorg.core.types import ModelTier
from synthorg.engine.routing_policy import (
    FlatStrategy,
    StakesAwareStrategy,
    StakesRoutingConfig,
    build_stakes_router,
)
from synthorg.engine.routing_policy.config import QualityFloors
from synthorg.providers.routing.models import ResolvedModel
from synthorg.providers.routing.resolver import ModelResolver
from tests._shared import as_uuid, coerce_id
from tests._shared.scripted_provider import make_e2e_identity


class _NoScoreProvider:
    """Benchmark provider that has no score for any model.

    Exercises the under-floor fallback: every tier resolves but none can
    clear its quality floor, so routing must pick the strongest tier and
    flag the decision rather than crash or silently downgrade.
    """

    async def get_score(self, model_id: str) -> BenchmarkScore | None:
        return None

    async def list_scores(self) -> Mapping[str, BenchmarkScore]:
        return {}


_PROVIDER = "example-provider"
_TIER_MODEL_IDS: dict[ModelTier, str] = {
    "small": "example-small-001",
    "medium": "example-medium-001",
    "large": "example-large-001",
}
_TIER_COSTS: dict[ModelTier, float] = {"small": 0.1, "medium": 0.5, "large": 2.0}


def _resolver() -> ModelResolver:
    index: dict[str, tuple[ResolvedModel, ...]] = {
        tier: (
            ResolvedModel(
                provider_name=_PROVIDER,
                model_id=_TIER_MODEL_IDS[tier],
                alias=tier,
                cost_per_1k_input=_TIER_COSTS[tier],
                cost_per_1k_output=_TIER_COSTS[tier],
                max_context=128000,
                estimated_latency_ms=100,
            ),
        )
        for tier in _TIER_MODEL_IDS
    }
    return ModelResolver(index)


def _identity(tier: ModelTier = "large") -> AgentIdentity:
    base = make_e2e_identity()
    return base.model_copy(
        update={
            "model": ModelConfig(
                provider=_PROVIDER,
                model_id=_TIER_MODEL_IDS[tier],
                model_tier=tier,
            ),
        },
    )


def _task(stakes: Stakes) -> Task:
    return Task(
        id=as_uuid("task-1"),
        title="A task",
        description="Body",
        type=TaskType.DEVELOPMENT,
        project="proj-1",
        created_by="creator",
        stakes=stakes,
    )


def _strategy(
    *,
    config: StakesRoutingConfig | None = None,
    coordination_store: CoordinationMetricsStore | None = None,
    resolver: ModelResolver | None = None,
) -> StakesAwareStrategy:
    return StakesAwareStrategy(
        benchmark_provider=StubBenchmarkScoreProvider(),
        config=config or StakesRoutingConfig(),
        resolver=resolver if resolver is not None else _resolver(),
        coordination_store=coordination_store,
    )


@pytest.mark.unit
class TestStakesAwareFloorSelection:
    """Cheapest tier clearing the per-stakes quality floor is selected."""

    @pytest.mark.parametrize(
        ("stakes", "expected_tier"),
        [
            (Stakes.LOW, "small"),
            (Stakes.NORMAL, "medium"),
            (Stakes.HIGH, "large"),
            (Stakes.CRITICAL, "large"),
        ],
    )
    async def test_floor_picks_expected_tier(
        self,
        stakes: Stakes,
        expected_tier: ModelTier,
    ) -> None:
        decision = await _strategy().route(
            task=_task(stakes),
            identity=_identity("large"),
        )
        assert decision.selected_model.model_tier == expected_tier
        assert decision.selected_model.model_id == _TIER_MODEL_IDS[expected_tier]

    async def test_low_stakes_downgrades_strong_agent(self) -> None:
        """A large-tier agent on low-stakes work is routed down to small."""
        decision = await _strategy().route(
            task=_task(Stakes.LOW),
            identity=_identity("large"),
        )
        assert decision.selected_model.model_tier == "small"
        assert decision.source == "stakes_aware:floor"

    async def test_high_stakes_upgrades_weak_agent(self) -> None:
        """A small-tier agent on high-stakes work is routed up to large."""
        decision = await _strategy().route(
            task=_task(Stakes.HIGH),
            identity=_identity("small"),
        )
        assert decision.selected_model.model_tier == "large"


@pytest.mark.unit
class TestRedTeamMarking:
    """High/critical stakes set the red-team requirement; lower do not."""

    @pytest.mark.parametrize(
        ("stakes", "expected"),
        [
            (Stakes.LOW, False),
            (Stakes.NORMAL, False),
            (Stakes.HIGH, True),
            (Stakes.CRITICAL, True),
        ],
    )
    async def test_red_team_threshold(self, stakes: Stakes, expected: bool) -> None:
        decision = await _strategy().route(
            task=_task(stakes),
            identity=_identity("medium"),
        )
        assert decision.red_team_required is expected


@pytest.mark.unit
class TestNeverDowngradeBelowConfiguredTier:
    """High/critical work never runs below the agent's own tier."""

    async def test_high_stakes_keeps_large_even_with_low_floor(self) -> None:
        # Floors set so the cheapest-meeting tier would be small, but the
        # agent is configured large and stakes are HIGH: stay at large.
        config = StakesRoutingConfig(
            quality_floors=QualityFloors(low=0, normal=0, high=0, critical=0),
        )
        decision = await _strategy(config=config).route(
            task=_task(Stakes.HIGH),
            identity=_identity("large"),
        )
        assert decision.selected_model.model_tier == "large"


@pytest.mark.unit
class TestCoordinationNudge:
    """Unhealthy coordination metrics bump the tier one step up."""

    def _unhealthy_store(self, task_id: str) -> CoordinationMetricsStore:
        store = CoordinationMetricsStore()
        store.record(
            CoordinationMetricsRecord(
                task_id=coerce_id(task_id),
                computed_at=datetime.now(UTC),
                team_size=3,
                metrics=CoordinationMetrics(
                    error_amplification=ErrorAmplification(
                        error_rate_mas=0.6,
                        error_rate_sas=0.2,
                    ),
                ),
            ),
        )
        return store

    async def test_nudge_bumps_tier(self) -> None:
        store = self._unhealthy_store("task-1")
        decision = await _strategy(coordination_store=store).route(
            task=_task(Stakes.NORMAL),  # floor -> medium
            identity=_identity("small"),
        )
        # medium nudged to large by the amplification breach.
        assert decision.selected_model.model_tier == "large"
        assert decision.source == "stakes_aware:nudge"

    async def test_no_records_no_nudge(self) -> None:
        store = CoordinationMetricsStore()
        decision = await _strategy(coordination_store=store).route(
            task=_task(Stakes.NORMAL),
            identity=_identity("small"),
        )
        assert decision.selected_model.model_tier == "medium"


@pytest.mark.unit
class TestResolverAbsent:
    """Without a resolver the model is unchanged but red-team still marks."""

    async def test_no_resolver_keeps_model_marks_red_team(self) -> None:
        strategy = StakesAwareStrategy(
            benchmark_provider=StubBenchmarkScoreProvider(),
            resolver=None,
        )
        decision = await strategy.route(
            task=_task(Stakes.HIGH),
            identity=_identity("small"),
        )
        assert decision.selected_model.model_tier == "small"
        assert decision.source == "stakes_aware:noop"
        assert decision.red_team_required is True


@pytest.mark.unit
class TestFlatStrategy:
    """Flat routing is a true no-op control arm."""

    async def test_flat_keeps_model_and_never_marks_red_team(self) -> None:
        identity = _identity("large")
        decision = await FlatStrategy().route(
            task=_task(Stakes.CRITICAL),
            identity=identity,
        )
        assert decision.selected_model == identity.model
        assert decision.red_team_required is False
        assert decision.source == "flat"


@pytest.mark.unit
class TestBuildStakesRouter:
    """Factory dispatch on the ``strategy`` discriminator."""

    async def test_default_builds_stakes_aware(self) -> None:
        router = build_stakes_router(
            benchmark_provider=StubBenchmarkScoreProvider(),
            resolver=_resolver(),
        )
        decision = await router.route(
            task=_task(Stakes.LOW),
            identity=_identity("large"),
        )
        assert decision.selected_model.model_tier == "small"

    async def test_flat_strategy_via_discriminator(self) -> None:
        router = build_stakes_router(StakesRoutingConfig(strategy="flat"))
        decision = await router.route(
            task=_task(Stakes.HIGH),
            identity=_identity("large"),
        )
        assert decision.source == "flat"

    def test_unknown_strategy_raises(self) -> None:
        with pytest.raises(StrategyFactoryNotFoundError):
            build_stakes_router(StakesRoutingConfig(strategy="nope"))

    def test_stakes_aware_without_benchmark_raises(self) -> None:
        with pytest.raises(ValueError, match="benchmark"):
            build_stakes_router(StakesRoutingConfig(strategy="stakes_aware"))


@pytest.mark.unit
class TestFloorBoundary:
    """A score exactly equal to the floor clears it (``>=`` boundary).

    Stub scores: small=72, medium=85, large=92. NORMAL stakes avoid the
    red-team tier-floor interaction so the floor selection is observed
    directly on a large-tier agent.
    """

    @pytest.mark.parametrize(
        ("normal_floor", "expected_tier"),
        [
            (72.0, "small"),  # small score == floor: clears
            (72.01, "medium"),  # just above small: small fails, medium clears
            (85.0, "medium"),  # medium score == floor: clears
            (85.01, "large"),  # just above medium: large clears
        ],
    )
    async def test_score_equal_to_floor_clears(
        self,
        normal_floor: float,
        expected_tier: ModelTier,
    ) -> None:
        config = StakesRoutingConfig(
            quality_floors=QualityFloors(
                low=0, normal=normal_floor, high=100, critical=100
            ),
        )
        decision = await _strategy(config=config).route(
            task=_task(Stakes.NORMAL),
            identity=_identity("large"),
        )
        assert decision.selected_model.model_tier == expected_tier


@pytest.mark.unit
class TestCoordinationNudgeBoundary:
    """The nudge fires only when amplification is strictly above threshold."""

    def _store_with_amplification(
        self,
        *,
        error_rate_mas: float,
        error_rate_sas: float,
    ) -> CoordinationMetricsStore:
        store = CoordinationMetricsStore()
        store.record(
            CoordinationMetricsRecord(
                task_id=coerce_id("task-1"),
                computed_at=datetime.now(UTC),
                team_size=3,
                metrics=CoordinationMetrics(
                    error_amplification=ErrorAmplification(
                        error_rate_mas=error_rate_mas,
                        error_rate_sas=error_rate_sas,
                    ),
                ),
            ),
        )
        return store

    async def test_amplification_at_threshold_does_not_nudge(self) -> None:
        # 0.3 / 0.2 == 1.5, exactly the default threshold (strict ">").
        store = self._store_with_amplification(error_rate_mas=0.3, error_rate_sas=0.2)
        decision = await _strategy(coordination_store=store).route(
            task=_task(Stakes.NORMAL),  # floor -> medium
            identity=_identity("small"),
        )
        assert decision.selected_model.model_tier == "medium"
        assert decision.source == "stakes_aware:floor"

    async def test_amplification_above_threshold_nudges(self) -> None:
        # 0.32 / 0.2 == 1.6 > 1.5.
        store = self._store_with_amplification(error_rate_mas=0.32, error_rate_sas=0.2)
        decision = await _strategy(coordination_store=store).route(
            task=_task(Stakes.NORMAL),  # floor -> medium, nudged to large
            identity=_identity("small"),
        )
        assert decision.selected_model.model_tier == "large"
        assert decision.source == "stakes_aware:nudge"


@pytest.mark.unit
class TestBenchmarkUnavailable:
    """No tier clears the floor: fall back to the strongest tier, flagged."""

    async def test_no_score_falls_back_to_strongest_and_flags(self) -> None:
        strategy = StakesAwareStrategy(
            benchmark_provider=_NoScoreProvider(),
            resolver=_resolver(),
        )
        decision = await strategy.route(
            task=_task(Stakes.LOW),
            identity=_identity("small"),
        )
        # No tier clears the floor, so the strongest resolvable tier is
        # chosen and the decision is flagged rather than silently kept.
        assert decision.selected_model.model_tier == "large"
        assert decision.source == "stakes_aware:floor_unmet"
        assert "floor not met" in decision.reason
