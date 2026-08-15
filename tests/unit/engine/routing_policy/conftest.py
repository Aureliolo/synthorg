"""The capability ladder every routing-policy test staffs from.

One copy, because the cost table is load-bearing for two separate cost
claims. Three files held byte-for-byte duplicates of it: if one had changed
its rungs or its alias-plus-model-id indexing, both tests would have kept
reading identically while asserting different economics, and nothing would
have said so.
"""

from typing import Final

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.types import CapabilityLevel
from synthorg.engine.routing_policy import (
    CapabilityPolicy,
    CapabilityPolicyConfig,
    ResolvedAgentCapabilityReader,
)
from synthorg.providers.routing.models import ResolvedModel
from synthorg.providers.routing.resolver import ModelResolver
from tests._shared.scripted_provider import make_e2e_identity

PROVIDER: Final[str] = "example-provider"
MODEL_IDS: Final[dict[CapabilityLevel, str]] = {
    "basic": "example-basic-001",
    "capable": "example-capable-001",
    "expert": "example-expert-001",
}
#: total_cost_per_1k = input + output; strictly increasing by rung.
TOTAL_COST: Final[dict[CapabilityLevel, float]] = {
    "basic": 0.2,
    "capable": 1.0,
    "expert": 4.0,
}
#: Weakest first, so the first agent clearing a floor is the cheapest one.
LADDER: Final[tuple[CapabilityLevel, ...]] = ("basic", "capable", "expert")


def build_resolver(
    rungs: tuple[CapabilityLevel, ...] = LADDER,
) -> ModelResolver:
    """Index each rung by both its alias and its model id.

    Both keys, because a caller may name either and the floor policy has to
    grade the pair regardless of which spelling reached it.

    Returns:
        A resolver serving exactly *rungs*.
    """
    index: dict[str, tuple[ResolvedModel, ...]] = {}
    for rung in rungs:
        resolved = ResolvedModel(
            provider_name=PROVIDER,
            model_id=MODEL_IDS[rung],
            alias=rung,
            cost_per_1k_input=TOTAL_COST[rung] / 2,
            cost_per_1k_output=TOTAL_COST[rung] / 2,
            max_context=128000,
            estimated_latency_ms=100,
            capability=rung,
        )
        index[rung] = (resolved,)
        index[MODEL_IDS[rung]] = (resolved,)
    return ModelResolver(index)


def build_policy(
    rungs: tuple[CapabilityLevel, ...] = LADDER,
    config: CapabilityPolicyConfig | None = None,
) -> CapabilityPolicy:
    """Return the capability policy reading *rungs*.

    Returns:
        A policy over *config*, or the shipped ladder when none is given.
    """
    return CapabilityPolicy(
        config=config if config is not None else CapabilityPolicyConfig(),
        reader=ResolvedAgentCapabilityReader(build_resolver(rungs)),
    )


def build_agent(rung: CapabilityLevel) -> AgentIdentity:
    """Return an agent bound to the pair for *rung*.

    Returns:
        An identity whose model is the *rung* pair.
    """
    return make_e2e_identity().model_copy(
        update={
            "model": ModelConfig(
                provider=PROVIDER,
                model_id=MODEL_IDS[rung],
                capability=rung,
            ),
        },
    )
