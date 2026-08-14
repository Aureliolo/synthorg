"""Unit tests for the stakes capability gate and its factory."""

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
    CapabilityFloorPolicy,
    FlatStrategy,
    ResolvedAgentCapabilityReader,
    StakesAwareStrategy,
    StakesCapabilityFloor,
    StakesModelUnavailableError,
    StakesRoutingConfig,
    StakesRoutingConfigError,
    build_stakes_router,
)
from synthorg.engine.routing_policy.config import StakesReasoning
from synthorg.providers.routing.models import ResolvedModel
from synthorg.providers.routing.resolver import ModelResolver
from tests._shared import as_uuid, coerce_id
from tests._shared.scripted_provider import make_e2e_identity

_PROVIDER = "example-provider"
_MODEL_IDS: dict[CapabilityLevel, str] = {
    "basic": "example-basic-001",
    "capable": "example-capable-001",
    "expert": "example-expert-001",
}
_COSTS: dict[CapabilityLevel, float] = {
    "basic": 0.1,
    "capable": 0.5,
    "expert": 2.0,
}


def _model(
    capability: CapabilityLevel,
    *,
    reported_capability: CapabilityLevel | None = None,
) -> ResolvedModel:
    return ResolvedModel(
        provider_name=_PROVIDER,
        model_id=_MODEL_IDS[capability],
        alias=capability,
        cost_per_1k_input=_COSTS[capability],
        cost_per_1k_output=_COSTS[capability],
        max_context=128000,
        estimated_latency_ms=100,
        capability=(
            reported_capability if reported_capability is not None else capability
        ),
    )


def _resolver(
    capabilities: tuple[CapabilityLevel, ...] = ("basic", "capable", "expert"),
    *,
    capability_overrides: dict[CapabilityLevel, CapabilityLevel] | None = None,
) -> ModelResolver:
    """Build a resolver indexed by model id and alias, as ``from_config`` is.

    Indexing both matters: the gate resolves the agent's own bound pair by
    ``(provider, model_id)``, so an alias-only index would answer "not in the
    catalogue" for every agent and fall back to the roster every time.

    Args:
        capabilities: The rungs to stock the catalogue with.
        capability_overrides: Rung the registry reports for a model, when it
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
            reported_capability=overrides.get(capability, capability),
        )
        index[capability] = (resolved,)
        index[_MODEL_IDS[capability]] = (resolved,)
    return ModelResolver(index)


def _policy(
    resolver: ModelResolver | None = None,
    *,
    floors: StakesCapabilityFloor | None = None,
) -> CapabilityFloorPolicy:
    return CapabilityFloorPolicy(
        floors=floors or StakesCapabilityFloor(),
        reader=ResolvedAgentCapabilityReader(
            resolver if resolver is not None else _resolver()
        ),
    )


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
                model_id=_MODEL_IDS[capability],
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
    floor_policy: CapabilityFloorPolicy | None = None,
) -> StakesAwareStrategy:
    return StakesAwareStrategy(
        config=config or StakesRoutingConfig(),
        floor_policy=floor_policy if floor_policy is not None else _policy(),
        coordination_store=coordination_store,
    )


@pytest.mark.unit
class TestCapabilityGate:
    """An adequate agent runs its own model; an inadequate one is refused."""

    @pytest.mark.parametrize(
        "stakes",
        [Stakes.LOW, Stakes.NORMAL, Stakes.HIGH, Stakes.CRITICAL],
    )
    async def test_an_expert_agent_clears_every_stakes_level(
        self,
        stakes: Stakes,
    ) -> None:
        decision = await _strategy().route(
            task=_task(stakes),
            identity=_identity("expert"),
        )
        assert decision.agent_capability == "expert"
        assert decision.source == "stakes_aware:cleared"

    async def test_low_stakes_leave_a_strong_agent_alone(self) -> None:
        """Cheap work does not move an expert agent onto a cheaper model.

        The agent is the unit being measured, so borrowing a cheaper model
        for its low-stakes turns would make its own history incomparable.
        """
        decision = await _strategy().route(
            task=_task(Stakes.LOW),
            identity=_identity("expert"),
        )
        assert decision.agent_capability == "expert"
        assert decision.required_capability == "basic"

    async def test_the_decision_names_no_model(self) -> None:
        """Routing picks the agent, never the horsepower behind its name."""
        decision = await _strategy().route(
            task=_task(Stakes.HIGH),
            identity=_identity("expert"),
        )
        assert not hasattr(decision, "selected_model")

    async def test_an_under_capable_agent_is_refused(self) -> None:
        with pytest.raises(StakesModelUnavailableError) as excinfo:
            await _strategy().route(
                task=_task(Stakes.HIGH),
                identity=_identity("basic"),
            )
        assert excinfo.value.stakes == Stakes.HIGH
        assert excinfo.value.required_capability == "expert"

    async def test_an_agent_exactly_at_the_floor_clears_it(self) -> None:
        decision = await _strategy().route(
            task=_task(Stakes.NORMAL),
            identity=_identity("capable"),
        )
        assert decision.required_capability == "capable"
        assert decision.agent_capability == "capable"


@pytest.mark.unit
class TestRegistryIsAuthoritative:
    """A stale roster rung never decides whether an agent may run."""

    async def test_the_registry_rung_clears_a_pessimistic_roster(self) -> None:
        """The roster says capable, the registry says expert; expert wins.

        The registry is recomputed from live capability metadata and carries
        published evidence plus the operator's overrides, so it is the one
        that moved.
        """
        strategy = _strategy(
            floor_policy=_policy(_resolver(capability_overrides={"capable": "expert"})),
        )

        decision = await strategy.route(
            task=_task(Stakes.HIGH),
            identity=_identity("capable", roster_capability="capable"),
        )

        assert decision.agent_capability == "expert"

    async def test_an_optimistic_roster_rung_does_not_carry_a_weak_model(self) -> None:
        """The defect this exists to stop, in one test.

        The roster claims expert while the registry rates the model basic.
        Trusting the roster is what let high-stakes work run on the weaker of
        two models for an hour without anything saying so.
        """
        strategy = _strategy(
            floor_policy=_policy(_resolver(capability_overrides={"expert": "basic"})),
        )

        with pytest.raises(StakesModelUnavailableError):
            await strategy.route(
                task=_task(Stakes.HIGH),
                identity=_identity("expert", roster_capability="expert"),
            )

    async def test_a_pair_outside_the_catalogue_falls_back_to_the_roster(self) -> None:
        identity = _identity("expert").model_copy(
            update={
                "model": ModelConfig(
                    provider=_PROVIDER,
                    model_id="retired-model-001",
                    capability="expert",
                ),
            },
        )

        decision = await _strategy().route(task=_task(Stakes.HIGH), identity=identity)

        assert decision.agent_capability == "expert"

    async def test_a_pair_nothing_grades_is_refused(self) -> None:
        """Unknown is not a rung, and dispatch could not resolve it either."""
        identity = _identity("expert").model_copy(
            update={
                "model": ModelConfig(
                    provider=_PROVIDER,
                    model_id="retired-model-001",
                ),
            },
        )

        with pytest.raises(StakesModelUnavailableError):
            await _strategy().route(task=_task(Stakes.LOW), identity=identity)


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
            identity=_identity("expert"),
        )
        assert decision.red_team_required is expected

    async def test_the_red_team_mark_does_not_raise_the_floor(self) -> None:
        """Marking work for review is not a claim about what it needs.

        The old floor existed to stop a downward model swap on red-team work.
        With no swap left to stop, a floor pinned at the agent's own rung is
        a requirement the agent trivially satisfies, so it is gone; the mark
        itself still fires and the review gate still runs.
        """
        flat_floors = StakesCapabilityFloor(
            low="basic",
            normal="basic",
            high="basic",
            critical="basic",
        )

        decision = await _strategy(floor_policy=_policy(floors=flat_floors)).route(
            task=_task(Stakes.HIGH),
            identity=_identity("basic"),
        )

        assert decision.required_capability == "basic"
        assert decision.red_team_required is True


@pytest.mark.unit
class TestCoordinationNudge:
    """Unhealthy coordination metrics bump the required rung one step up."""

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

    async def test_the_nudge_raises_the_requirement(self) -> None:
        store = self._unhealthy_store("task-1")

        decision = await _strategy(coordination_store=store).route(
            task=_task(Stakes.NORMAL),
            identity=_identity("expert"),
        )

        assert decision.required_capability == "expert"
        assert decision.source == "stakes_aware:nudge"

    async def test_the_nudge_can_refuse_an_agent_the_base_floor_allowed(self) -> None:
        """A signal arriving after assignment, not a disagreement with it.

        Coordination health only exists once the task has started running, so
        an agent legitimately assigned at the base floor can be refused later
        at a bumped one. Parking says so; downgrading the requirement would
        not.
        """
        store = self._unhealthy_store("task-1")

        with pytest.raises(StakesModelUnavailableError):
            await _strategy(coordination_store=store).route(
                task=_task(Stakes.NORMAL),
                identity=_identity("capable"),
            )

    async def test_no_records_no_nudge(self) -> None:
        store = CoordinationMetricsStore()

        decision = await _strategy(coordination_store=store).route(
            task=_task(Stakes.NORMAL),
            identity=_identity("capable"),
        )

        assert decision.required_capability == "capable"
        assert decision.source == "stakes_aware:cleared"


@pytest.mark.unit
class TestFlatStrategy:
    """Flat routing is a true no-op control arm."""

    async def test_flat_imposes_no_requirement_and_never_marks_red_team(self) -> None:
        decision = await FlatStrategy().route(
            task=_task(Stakes.CRITICAL),
            identity=_identity("basic"),
        )
        assert decision.required_capability is None
        assert decision.red_team_required is False
        assert decision.source == "flat"

    async def test_flat_never_refuses_an_ungraded_agent(self) -> None:
        """The opt-out must not park work the gate would have parked."""
        identity = _identity("basic").model_copy(
            update={
                "model": ModelConfig(provider=_PROVIDER, model_id="unknown-001"),
            },
        )

        decision = await FlatStrategy().route(
            task=_task(Stakes.CRITICAL),
            identity=identity,
        )

        assert decision.agent_capability is None

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
        router = build_stakes_router(floor_policy=_policy())

        decision = await router.route(
            task=_task(Stakes.HIGH),
            identity=_identity("expert"),
        )

        assert decision.source == "stakes_aware:cleared"

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

    def test_stakes_aware_without_a_floor_policy_raises(self) -> None:
        """A typed fault, matched by type rather than by message wording.

        Both branches of this factory refuse the same category of mistake:
        the config names a strategy the collaborators cannot build. Matching
        a substring instead would have broken on any rewording.
        """
        with pytest.raises(StakesRoutingConfigError):
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
            task=_task(Stakes.NORMAL),
            identity=_identity("capable"),
        )

        assert decision.required_capability == "capable"
        assert decision.source == "stakes_aware:cleared"

    async def test_amplification_above_threshold_nudges(self) -> None:
        # 0.32 / 0.2 == 1.6 > 1.5.
        store = self._store_with_amplification(error_rate_mas=0.32, error_rate_sas=0.2)

        decision = await _strategy(coordination_store=store).route(
            task=_task(Stakes.NORMAL),
            identity=_identity("expert"),
        )

        assert decision.required_capability == "expert"
        assert decision.source == "stakes_aware:nudge"
