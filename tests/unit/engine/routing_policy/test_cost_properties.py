"""Property: staffing by capability floor never costs more than all-strong.

With a roster carrying one agent per rung, the weakest agent clearing a
task's floor is by construction never more expensive than the strongest
agent, whatever the mix of stakes. The saving comes from picking the
agent, so it holds without any agent ever running a model other than the
one it was configured with.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import Task
from synthorg.core.task_enums import Stakes, TaskType
from synthorg.core.types import CapabilityLevel
from synthorg.engine.routing_policy import (
    CapabilityFloorPolicy,
    ResolvedAgentCapabilityReader,
    StakesCapabilityFloor,
)
from synthorg.providers.routing.models import ResolvedModel
from synthorg.providers.routing.resolver import ModelResolver
from tests._shared import as_uuid
from tests._shared.scripted_provider import make_e2e_identity

_PROVIDER = "example-provider"
_MODEL_IDS: dict[CapabilityLevel, str] = {
    "basic": "example-basic-001",
    "capable": "example-capable-001",
    "expert": "example-expert-001",
}
_TOTAL_COST: dict[CapabilityLevel, float] = {
    "basic": 0.2,
    "capable": 1.0,
    "expert": 4.0,
}
_LADDER: tuple[CapabilityLevel, ...] = ("basic", "capable", "expert")


def _resolver() -> ModelResolver:
    index: dict[str, tuple[ResolvedModel, ...]] = {}
    for rung in _LADDER:
        resolved = ResolvedModel(
            provider_name=_PROVIDER,
            model_id=_MODEL_IDS[rung],
            alias=rung,
            cost_per_1k_input=_TOTAL_COST[rung] / 2,
            cost_per_1k_output=_TOTAL_COST[rung] / 2,
            max_context=128000,
            estimated_latency_ms=100,
            capability=rung,
        )
        index[rung] = (resolved,)
        index[_MODEL_IDS[rung]] = (resolved,)
    return ModelResolver(index)


def _policy() -> CapabilityFloorPolicy:
    return CapabilityFloorPolicy(
        floors=StakesCapabilityFloor(),
        reader=ResolvedAgentCapabilityReader(_resolver()),
    )


def _agent(rung: CapabilityLevel) -> AgentIdentity:
    return make_e2e_identity().model_copy(
        update={
            "model": ModelConfig(
                provider=_PROVIDER,
                model_id=_MODEL_IDS[rung],
                capability=rung,
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
async def test_floor_staffing_never_costs_more_than_all_strong(
    stakes_mix: list[Stakes],
) -> None:
    policy = _policy()
    roster = [(_agent(rung), rung) for rung in _LADDER]
    flat_cost = len(stakes_mix) * _TOTAL_COST["expert"]

    graded_cost = 0.0
    for stakes in stakes_mix:
        required = policy.required_for(_task(stakes).stakes)
        picked = next(
            rung for agent, rung in roster if policy.clears(agent.model, required)
        )
        graded_cost += _TOTAL_COST[picked]

    assert graded_cost <= flat_cost


@pytest.mark.unit
@given(
    stakes=st.sampled_from(list(Stakes)),
    weaker=st.integers(min_value=0, max_value=len(_LADDER) - 1),
    stronger=st.integers(min_value=0, max_value=len(_LADDER) - 1),
)
async def test_a_stronger_agent_never_fails_a_floor_a_weaker_one_cleared(
    stakes: Stakes,
    weaker: int,
    stronger: int,
) -> None:
    """Monotonicity: capability only ever helps.

    A ladder where a stronger rung could be refused work a weaker rung takes
    would make the floor unpredictable, and an operator adding a better model
    would see work stop routing to it.
    """
    if stronger < weaker:
        weaker, stronger = stronger, weaker
    policy = _policy()
    required = policy.required_for(stakes)

    if policy.clears(_agent(_LADDER[weaker]).model, required):
        assert policy.clears(_agent(_LADDER[stronger]).model, required)
