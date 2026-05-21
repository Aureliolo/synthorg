"""AgentEngine stakes-routing integration (the ``_route_stakes`` seam)."""

import pytest
from tests._shared.scripted_provider import ScriptedProvider, make_e2e_identity

from synthorg.budget.benchmark_stub import StubBenchmarkScoreProvider
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.enums import Stakes, TaskType
from synthorg.core.task import Task
from synthorg.core.types import ModelTier
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.routing_policy import StakesRoutingConfig, build_stakes_router
from synthorg.providers.routing.models import ResolvedModel
from synthorg.providers.routing.resolver import ModelResolver

_PROVIDER = "example-provider"
_TIER_MODEL_IDS: dict[ModelTier, str] = {
    "small": "example-small-001",
    "medium": "example-medium-001",
    "large": "example-large-001",
}


def _resolver() -> ModelResolver:
    index: dict[str, tuple[ResolvedModel, ...]] = {
        tier: (
            ResolvedModel(
                provider_name=_PROVIDER,
                model_id=model_id,
                alias=tier,
                cost_per_1k_input=cost,
                cost_per_1k_output=cost,
                max_context=128000,
                estimated_latency_ms=100,
            ),
        )
        for tier, model_id, cost in (
            ("small", _TIER_MODEL_IDS["small"], 0.1),
            ("medium", _TIER_MODEL_IDS["medium"], 0.5),
            ("large", _TIER_MODEL_IDS["large"], 2.0),
        )
    }
    return ModelResolver(index)


def _engine(*, stakes: bool) -> AgentEngine:
    router = (
        build_stakes_router(
            StakesRoutingConfig(),
            benchmark_provider=StubBenchmarkScoreProvider(),
            resolver=_resolver(),
        )
        if stakes
        else None
    )
    return AgentEngine(provider=ScriptedProvider([]), stakes_router=router)


def _identity(tier: ModelTier) -> AgentIdentity:
    return make_e2e_identity().model_copy(
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
        id="task-1",
        title="A task",
        description="Body",
        type=TaskType.DEVELOPMENT,
        project="proj-1",
        created_by="creator",
        stakes=stakes,
    )


@pytest.mark.unit
class TestRouteStakesSeam:
    """``_route_stakes`` adjusts the identity's model from task stakes."""

    async def test_low_stakes_downgrades(self) -> None:
        engine = _engine(stakes=True)
        adjusted = await engine._route_stakes(
            _identity("large"),
            _task(Stakes.LOW),
        )
        assert adjusted.model.model_tier == "small"

    async def test_high_stakes_upgrades(self) -> None:
        engine = _engine(stakes=True)
        adjusted = await engine._route_stakes(
            _identity("small"),
            _task(Stakes.HIGH),
        )
        assert adjusted.model.model_tier == "large"

    async def test_normal_stakes_keeps_medium(self) -> None:
        engine = _engine(stakes=True)
        identity = _identity("medium")
        adjusted = await engine._route_stakes(
            identity,
            _task(Stakes.NORMAL),
        )
        assert adjusted.model == identity.model

    def test_engine_accepts_no_router(self) -> None:
        engine = _engine(stakes=False)
        assert engine._stakes_router is None
