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
from synthorg.core.completion_enums import ReasoningEffort
from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.core.task import Task
from synthorg.core.task_enums import Stakes, TaskType
from synthorg.core.types import CapabilityLevel
from synthorg.engine.routing_policy import (
    FlatStrategy,
    StakesAwareStrategy,
    StakesCapabilityFloor,
    StakesModelUnavailableError,
    StakesRoutingConfig,
    build_stakes_router,
)
from synthorg.engine.routing_policy.config import StakesReasoning
from synthorg.providers.routing.models import ResolvedModel
from synthorg.providers.routing.resolver import ModelResolver
from tests._shared import as_uuid, coerce_id
from tests._shared.scripted_provider import make_e2e_identity

_PROVIDER = "example-provider"
_TIER_MODEL_IDS: dict[CapabilityLevel, str] = {
    "basic": "example-basic-001",
    "capable": "example-capable-001",
    "expert": "example-expert-001",
}
_TIER_COSTS: dict[CapabilityLevel, float] = {
    "basic": 0.1,
    "capable": 0.5,
    "expert": 2.0,
}


def _model(
    capability: CapabilityLevel,
    *,
    tool_capable: bool = True,
    reported_capability: CapabilityLevel | None = None,
) -> ResolvedModel:
    return ResolvedModel(
        provider_name=_PROVIDER,
        model_id=_TIER_MODEL_IDS[capability],
        alias=capability,
        cost_per_1k_input=_TIER_COSTS[capability],
        cost_per_1k_output=_TIER_COSTS[capability],
        max_context=128000,
        estimated_latency_ms=100,
        capability=(
            reported_capability if reported_capability is not None else capability
        ),
        tool_capable=tool_capable,
    )


def _resolver(
    capabilities: tuple[CapabilityLevel, ...] = ("basic", "capable", "expert"),
    *,
    non_tool_capable: frozenset[CapabilityLevel] = frozenset(),
    capability_overrides: dict[CapabilityLevel, CapabilityLevel] | None = None,
) -> ModelResolver:
    """Build a resolver indexed by model id and alias, as ``from_config`` is.

    Indexing both matters: the strategy resolves the agent's own bound pair by
    ``(provider, model_id)``, so an alias-only index would answer "not in the
    catalogue" for every agent and route every task as if the operator had
    chosen nothing.

    Args:
        capabilities: The rungs to stock the catalogue with.
        non_tool_capable: Rungs whose model cannot execute tool-bearing work.
        capability_overrides: Rung the resolver reports for a model, when it
            differs from the rung its id is named for (the registry
            disagreeing with the roster).

    Returns:
        A resolver over one model per requested rung.
    """
    overrides = capability_overrides or {}
    index: dict[str, tuple[ResolvedModel, ...]] = {}
    for capability in capabilities:
        resolved = _model(
            capability,
            tool_capable=capability not in non_tool_capable,
            reported_capability=overrides.get(capability, capability),
        )
        index[capability] = (resolved,)
        index[_TIER_MODEL_IDS[capability]] = (resolved,)
    return ModelResolver(index)


def _identity(
    capability: CapabilityLevel = "expert",
    *,
    roster_capability: CapabilityLevel | None = None,
) -> AgentIdentity:
    base = make_e2e_identity()
    return base.model_copy(
        update={
            "model": ModelConfig(
                provider=_PROVIDER,
                model_id=_TIER_MODEL_IDS[capability],
                capability=(
                    roster_capability if roster_capability is not None else capability
                ),
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
    """The agent's own model is kept unless the stakes outrank it."""

    @pytest.mark.parametrize(
        "stakes",
        [Stakes.LOW, Stakes.NORMAL, Stakes.HIGH, Stakes.CRITICAL],
    )
    async def test_adequate_agent_is_kept_at_every_stakes_level(
        self,
        stakes: Stakes,
    ) -> None:
        # A large agent meets every requirement, so nothing re-points it. The
        # operator chose that pair for the role; routing may only raise it.
        decision = await _strategy().route(
            task=_task(stakes),
            identity=_identity("expert"),
        )
        assert decision.selected_model.model_id == _TIER_MODEL_IDS["expert"]
        assert decision.source == "stakes_aware:kept"

    async def test_low_stakes_never_downgrades_a_strong_agent(self) -> None:
        # Cheapest-within-tier is what pointed every agent at one model: with a
        # gateway pricing everything the same it is an arbitrary tie whose
        # winner takes every task in the org.
        decision = await _strategy().route(
            task=_task(Stakes.LOW),
            identity=_identity("expert"),
        )
        assert decision.selected_model.capability == "expert"
        assert decision.source == "stakes_aware:kept"

    async def test_high_stakes_upgrades_weak_agent(self) -> None:
        decision = await _strategy().route(
            task=_task(Stakes.HIGH),
            identity=_identity("basic"),
        )
        assert decision.selected_model.capability == "expert"
        assert decision.source == "stakes_aware:routed"

    async def test_already_satisfying_model_is_kept(self) -> None:
        decision = await _strategy().route(
            task=_task(Stakes.LOW),
            identity=_identity("basic"),
        )
        assert decision.selected_model.model_id == _TIER_MODEL_IDS["basic"]
        assert decision.source == "stakes_aware:kept"

    async def test_agent_outside_the_catalogue_routes_by_tier(self) -> None:
        # Its bound pair resolves to nothing, so there is no tier to trust and
        # the requirement decides.
        strategy = _strategy(resolver=_resolver(("basic", "capable", "expert")))
        identity = _identity("expert").model_copy(
            update={
                "model": ModelConfig(
                    provider=_PROVIDER,
                    model_id="retired-model-001",
                    capability="expert",
                ),
            },
        )
        decision = await strategy.route(task=_task(Stakes.NORMAL), identity=identity)
        assert decision.selected_model.model_id == _TIER_MODEL_IDS["capable"]
        assert decision.source == "stakes_aware:routed"


@pytest.mark.unit
class TestTierRegistryIsAuthoritative:
    """A stale roster ``model_tier`` never decides routing."""

    async def test_registry_tier_wins_and_is_written_back(self) -> None:
        # The roster says medium, the tier registry says large. The registry is
        # recomputed from live capability metadata and carries the operator's
        # overrides, so it decides; the stale roster value is corrected onto
        # the returned model so the prompt profile reads the real tier.
        strategy = _strategy(
            resolver=_resolver(capability_overrides={"capable": "expert"}),
        )
        decision = await strategy.route(
            task=_task(Stakes.HIGH),  # requires large
            identity=_identity("capable", roster_capability="capable"),
        )
        assert decision.selected_model.model_id == _TIER_MODEL_IDS["capable"]
        assert decision.selected_model.capability == "expert"
        assert decision.source == "stakes_aware:kept"

    async def test_optimistic_roster_tier_does_not_hold_a_weak_model(self) -> None:
        # The roster claims large, the registry says small: the agent is routed
        # up to the model the registry does rate large, rather than trusted.
        strategy = _strategy(
            resolver=_resolver(
                capability_overrides={"expert": "basic", "capable": "expert"},
            ),
        )
        decision = await strategy.route(
            task=_task(Stakes.HIGH),
            identity=_identity("expert", roster_capability="expert"),
        )
        assert decision.source == "stakes_aware:routed"
        assert decision.selected_model.capability == "expert"
        assert decision.selected_model.model_id == _TIER_MODEL_IDS["capable"]


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
            identity=_identity("capable"),
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
            stakes_capability_floors=StakesCapabilityFloor(
                low="basic",
                normal="basic",
                high="basic",
                critical="basic",
            ),
        )
        decision = await _strategy(config=config).route(
            task=_task(Stakes.HIGH),
            identity=_identity("expert"),
        )
        assert decision.selected_model.capability == "expert"


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
            identity=_identity("basic"),
        )
        # medium nudged to large by the amplification breach.
        assert decision.selected_model.capability == "expert"
        assert decision.source == "stakes_aware:nudge"

    async def test_no_records_no_nudge(self) -> None:
        store = CoordinationMetricsStore()
        decision = await _strategy(coordination_store=store).route(
            task=_task(Stakes.NORMAL),
            identity=_identity("basic"),
        )
        assert decision.selected_model.capability == "capable"


@pytest.mark.unit
class TestEscalateWhenNoModelMeetsTier:
    """No qualifying / tool-capable model raises, never a silent downgrade."""

    async def test_missing_tier_raises(self) -> None:
        # HIGH stakes require large, but the catalogue only has small + medium.
        strategy = _strategy(resolver=_resolver(("basic", "capable")))
        with pytest.raises(StakesModelUnavailableError) as excinfo:
            await strategy.route(
                task=_task(Stakes.HIGH),
                identity=_identity("basic"),
            )
        assert excinfo.value.stakes == Stakes.HIGH
        assert excinfo.value.required_capability == "expert"

    async def test_non_tool_capable_model_is_skipped_and_escalates(self) -> None:
        # The only large model cannot call tools, so high-stakes agentic work
        # escalates rather than running a tool-incapable model.
        strategy = _strategy(
            resolver=_resolver(non_tool_capable=frozenset({"expert"})),
        )
        with pytest.raises(StakesModelUnavailableError):
            await strategy.route(
                task=_task(Stakes.HIGH),
                identity=_identity("basic"),
            )

    async def test_tool_incapable_cheapest_is_skipped_for_capable_higher(
        self,
    ) -> None:
        # NORMAL requires medium, but the medium model cannot call tools, so
        # the cheapest in-range capable model (large) is selected instead.
        strategy = _strategy(
            resolver=_resolver(non_tool_capable=frozenset({"capable"})),
        )
        decision = await strategy.route(
            task=_task(Stakes.NORMAL),
            identity=_identity("basic"),
        )
        assert decision.selected_model.capability == "expert"


@pytest.mark.unit
class TestFlatStrategy:
    """Flat routing is a true no-op control arm."""

    async def test_flat_keeps_model_and_never_marks_red_team(self) -> None:
        identity = _identity("expert")
        decision = await FlatStrategy().route(
            task=_task(Stakes.CRITICAL),
            identity=identity,
        )
        assert decision.selected_model == identity.model
        assert decision.red_team_required is False
        assert decision.source == "flat"

    async def test_flat_leaves_reasoning_effort_unset(self) -> None:
        decision = await FlatStrategy().route(
            task=_task(Stakes.CRITICAL),
            identity=_identity("expert"),
        )
        assert decision.reasoning_effort is None


@pytest.mark.unit
class TestStakesReasoning:
    """Stakes drives the reasoning-effort dial on the decision."""

    @pytest.mark.parametrize(
        ("stakes", "expected"),
        [
            (Stakes.LOW, None),
            (Stakes.NORMAL, ReasoningEffort.LOW),
            (Stakes.HIGH, ReasoningEffort.MEDIUM),
            (Stakes.CRITICAL, ReasoningEffort.HIGH),
        ],
    )
    async def test_decision_carries_stakes_reasoning(
        self,
        stakes: Stakes,
        expected: ReasoningEffort | None,
    ) -> None:
        decision = await _strategy().route(
            task=_task(stakes),
            identity=_identity("expert"),
        )
        assert decision.reasoning_effort == expected

    async def test_for_stakes_honours_config_override(self) -> None:
        reasoning = StakesReasoning(
            low=ReasoningEffort.MINIMAL,
            normal=ReasoningEffort.MINIMAL,
            high=ReasoningEffort.HIGH,
            critical=ReasoningEffort.HIGH,
        )
        assert reasoning.for_stakes(Stakes.LOW) is ReasoningEffort.MINIMAL
        assert reasoning.for_stakes(Stakes.CRITICAL) is ReasoningEffort.HIGH

    async def test_override_flows_through_strategy(self) -> None:
        config = StakesRoutingConfig(
            stakes_reasoning=StakesReasoning(high=ReasoningEffort.HIGH)
        )
        decision = await _strategy(config=config).route(
            task=_task(Stakes.HIGH),
            identity=_identity("expert"),
        )
        assert decision.reasoning_effort is ReasoningEffort.HIGH


@pytest.mark.unit
class TestBuildStakesRouter:
    """Factory dispatch on the ``strategy`` discriminator."""

    async def test_default_builds_stakes_aware(self) -> None:
        router = build_stakes_router(resolver=_resolver())
        decision = await router.route(
            task=_task(Stakes.HIGH),
            identity=_identity("basic"),
        )
        assert decision.selected_model.capability == "expert"
        assert decision.source == "stakes_aware:routed"

    async def test_flat_strategy_via_discriminator(self) -> None:
        router = build_stakes_router(StakesRoutingConfig(strategy="flat"))
        decision = await router.route(
            task=_task(Stakes.HIGH),
            identity=_identity("expert"),
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
            identity=_identity("basic"),
        )
        assert decision.selected_model.capability == "capable"
        assert decision.source == "stakes_aware:routed"

    async def test_amplification_above_threshold_nudges(self) -> None:
        # 0.32 / 0.2 == 1.6 > 1.5.
        store = self._store_with_amplification(error_rate_mas=0.32, error_rate_sas=0.2)
        decision = await _strategy(coordination_store=store).route(
            task=_task(Stakes.NORMAL),  # required -> medium, nudged to large
            identity=_identity("basic"),
        )
        assert decision.selected_model.capability == "expert"
        assert decision.source == "stakes_aware:nudge"
