"""Property: stakes-aware never costs more than an all-strong flat policy.

With every agent configured at the strongest tier (the conservative
flat baseline), stakes-aware routing only ever holds or lowers the tier:
it downgrades low/normal-stakes work and keeps high/critical work at the
strong tier. So for any mix of stakes the stakes-aware total cost is
always <= the flat total cost, and never exceeds it.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from synthorg.budget.benchmark_stub import StubBenchmarkScoreProvider
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import Task
from synthorg.core.task_enums import Stakes, TaskType
from synthorg.core.types import ModelTier
from synthorg.engine.routing_policy import StakesAwareStrategy
from synthorg.providers.routing.models import ResolvedModel
from synthorg.providers.routing.resolver import ModelResolver
from tests._shared import as_uuid
from tests._shared.scripted_provider import make_e2e_identity

_PROVIDER = "example-provider"
_TIER_MODEL_IDS: dict[ModelTier, str] = {
    "small": "example-small-001",
    "medium": "example-medium-001",
    "large": "example-large-001",
}
_TIER_TOTAL_COST: dict[ModelTier, float] = {"small": 0.2, "medium": 1.0, "large": 4.0}


def _resolver() -> ModelResolver:
    index: dict[str, tuple[ResolvedModel, ...]] = {
        tier: (
            ResolvedModel(
                provider_name=_PROVIDER,
                model_id=_TIER_MODEL_IDS[tier],
                alias=tier,
                cost_per_1k_input=_TIER_TOTAL_COST[tier] / 2,
                cost_per_1k_output=_TIER_TOTAL_COST[tier] / 2,
                max_context=128000,
                estimated_latency_ms=100,
            ),
        )
        for tier in _TIER_MODEL_IDS
    }
    return ModelResolver(index)


def _agent_large() -> AgentIdentity:
    return make_e2e_identity().model_copy(
        update={
            "model": ModelConfig(
                provider=_PROVIDER,
                model_id=_TIER_MODEL_IDS["large"],
                model_tier="large",
            ),
        },
    )


def _task(stakes: Stakes) -> Task:
    return Task(
        id=as_uuid("t"),
        title="t",
        description="body",
        type=TaskType.DEVELOPMENT,
        project="p",
        created_by="c",
        stakes=stakes,
    )


@pytest.mark.unit
@given(stakes_mix=st.lists(st.sampled_from(list(Stakes)), min_size=1, max_size=12))
async def test_stakes_aware_never_costs_more_than_flat_all_strong(
    stakes_mix: list[Stakes],
) -> None:
    strategy = StakesAwareStrategy(
        benchmark_provider=StubBenchmarkScoreProvider(),
        resolver=_resolver(),
    )
    agent = _agent_large()
    flat_cost = len(stakes_mix) * _TIER_TOTAL_COST["large"]
    aware_cost = 0.0
    for stakes in stakes_mix:
        decision = await strategy.route(task=_task(stakes), identity=agent)
        tier = decision.selected_model.model_tier
        assert tier is not None
        aware_cost += _TIER_TOTAL_COST[tier]
    assert aware_cost <= flat_cost
