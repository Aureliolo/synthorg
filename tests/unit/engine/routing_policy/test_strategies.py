"""Unit tests for stakes-aware routing strategies and factory."""

from datetime import UTC, datetime

import pytest

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
    StakesModelUnavailableError,
    StakesRoutingConfig,
    StakesTierRequirement,
    build_stakes_router,
)
from synthorg.providers.routing.models import ResolvedModel
from synthorg.providers.routing.resolver import ModelResolver
from tests._shared import as_uuid, coerce_id
from tests._shared.scripted_provider import make_e2e_identity

_PROVIDER = "example-provider"
_TIER_MODEL_IDS: dict[ModelTier, str] = {
    "small": "example-small-001",
    "medium": "example-medium-001",
    "large": "example-large-001",
}
_TIER_COSTS: dict[ModelTier, float] = {"small": 0.1, "medium": 0.5, "large": 2.0}


def _model(
    tier: ModelTier,
    *,
    tool_capable: bool = True,
) -> ResolvedModel:
    return ResolvedModel(
        provider_name=_PROVIDER,
        model_id=_TIER_MODEL_IDS[tier],
        alias=tier,
        cost_per_1k_input=_TIER_COSTS[tier],
        cost_per_1k_output=_TIER_COSTS[tier],
        max_context=128000,
        estimated_latency_ms=100,
        tier=tier,
        tool_capable=tool_capable,
    )


def _resolver(
    tiers: tuple[ModelTier, ...] = ("small", "medium", "large"),
    *,
    non_tool_capable: frozenset[ModelTier] = frozenset(),
) -> ModelResolver:
    index: dict[str, tuple[ResolvedModel, ...]] = {
        tier: (_model(tier, tool_capable=tier not in non_tool_capable),)
        for tier in tiers
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
        config=config or StakesRoutingConfig(),
        resolver=resolver if resolver is not None else _resolver(),
        coordination_store=coordination_store,
    )


@pytest.mark.unit
class TestStakesTierSelection:
    """The cheapest tool-capable model meeting the stakes tier is selected."""

    @pytest.mark.parametrize(
        ("stakes", "expected_tier"),
        [
            (Stakes.LOW, "small"),
            (Stakes.NORMAL, "medium"),
            (Stakes.HIGH, "large"),
            (Stakes.CRITICAL, "large"),
        ],
    )
    async def test_required_tier_picks_cheapest_qualifying(
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
        decision = await _strategy().route(
            task=_task(Stakes.LOW),
            identity=_identity("large"),
        )
        assert decision.selected_model.model_tier == "small"
        assert decision.source == "stakes_aware:routed"

    async def test_high_stakes_upgrades_weak_agent(self) -> None:
        decision = await _strategy().route(
            task=_task(Stakes.HIGH),
            identity=_identity("small"),
        )
        assert decision.selected_model.model_tier == "large"

    async def test_already_satisfying_model_is_kept(self) -> None:
        # A small agent on LOW stakes: the cheapest qualifying model is its own,
        # so the decision keeps it rather than pointlessly re-pointing.
        decision = await _strategy().route(
            task=_task(Stakes.LOW),
            identity=_identity("small"),
        )
        assert decision.selected_model.model_id == _TIER_MODEL_IDS["small"]
        assert decision.source == "stakes_aware:kept"


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

    async def test_high_stakes_keeps_large_even_with_low_requirement(self) -> None:
        # Tier requirements set so the stakes requirement alone would allow
        # small, but the agent is configured large and stakes are HIGH: the
        # red-team floor lifts the requirement back to large.
        config = StakesRoutingConfig(
            stakes_tiers=StakesTierRequirement(
                low="small",
                normal="small",
                high="small",
                critical="small",
            ),
        )
        decision = await _strategy(config=config).route(
            task=_task(Stakes.HIGH),
            identity=_identity("large"),
        )
        assert decision.selected_model.model_tier == "large"


@pytest.mark.unit
class TestCoordinationNudge:
    """Unhealthy coordination metrics bump the required tier one step up."""

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
            task=_task(Stakes.NORMAL),  # required -> medium
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
class TestEscalateWhenNoModelMeetsTier:
    """No qualifying / tool-capable model raises, never a silent downgrade."""

    async def test_missing_tier_raises(self) -> None:
        # HIGH stakes require large, but the catalogue only has small + medium.
        strategy = _strategy(resolver=_resolver(("small", "medium")))
        with pytest.raises(StakesModelUnavailableError) as excinfo:
            await strategy.route(
                task=_task(Stakes.HIGH),
                identity=_identity("small"),
            )
        assert excinfo.value.stakes == Stakes.HIGH
        assert excinfo.value.required_tier == "large"

    async def test_non_tool_capable_model_is_skipped_and_escalates(self) -> None:
        # The only large model cannot call tools, so high-stakes agentic work
        # escalates rather than running a tool-incapable model.
        strategy = _strategy(
            resolver=_resolver(non_tool_capable=frozenset({"large"})),
        )
        with pytest.raises(StakesModelUnavailableError):
            await strategy.route(
                task=_task(Stakes.HIGH),
                identity=_identity("small"),
            )

    async def test_tool_incapable_cheapest_is_skipped_for_capable_higher(
        self,
    ) -> None:
        # small is not tool-capable; NORMAL requires medium anyway, so a
        # tool-capable medium is selected.
        strategy = _strategy(
            resolver=_resolver(non_tool_capable=frozenset({"small"})),
        )
        decision = await strategy.route(
            task=_task(Stakes.NORMAL),
            identity=_identity("small"),
        )
        assert decision.selected_model.model_tier == "medium"


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
        router = build_stakes_router(resolver=_resolver())
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

    def test_stakes_aware_without_resolver_raises(self) -> None:
        with pytest.raises(ValueError, match="resolver"):
            build_stakes_router(StakesRoutingConfig(strategy="stakes_aware"))


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
            task=_task(Stakes.NORMAL),  # required -> medium
            identity=_identity("small"),
        )
        assert decision.selected_model.model_tier == "medium"
        assert decision.source == "stakes_aware:routed"

    async def test_amplification_above_threshold_nudges(self) -> None:
        # 0.32 / 0.2 == 1.6 > 1.5.
        store = self._store_with_amplification(error_rate_mas=0.32, error_rate_sas=0.2)
        decision = await _strategy(coordination_store=store).route(
            task=_task(Stakes.NORMAL),  # required -> medium, nudged to large
            identity=_identity("small"),
        )
        assert decision.selected_model.model_tier == "large"
        assert decision.source == "stakes_aware:nudge"
