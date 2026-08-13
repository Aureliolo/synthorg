"""AgentEngine stakes-routing integration (the ``_route_stakes`` seam)."""

from unittest.mock import AsyncMock

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.completion_enums import ReasoningEffort
from synthorg.core.task import Task
from synthorg.core.task_enums import Stakes, TaskType
from synthorg.core.types import CapabilityLevel
from synthorg.engine._agent_engine_run import AgentEngineRunMixin
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.routing_policy import (
    StakesModelUnavailableError,
    StakesRoutingConfig,
    build_stakes_router,
)
from synthorg.providers.models import CompletionConfig
from synthorg.providers.routing.models import ResolvedModel
from synthorg.providers.routing.resolver import ModelResolver
from synthorg.settings.resolver import ConfigResolver
from tests._shared import as_uuid, mock_of
from tests._shared.scripted_provider import ScriptedProvider, make_e2e_identity

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
                capability=tier,
            ),
        )
        for tier in _TIER_MODEL_IDS
    }
    return ModelResolver(index)


def _engine(*, stakes: bool) -> AgentEngine:
    router = (
        build_stakes_router(StakesRoutingConfig(), resolver=_resolver())
        if stakes
        else None
    )
    return AgentEngine(provider=ScriptedProvider([]), stakes_router=router)


def _identity(tier: CapabilityLevel) -> AgentIdentity:
    return make_e2e_identity().model_copy(
        update={
            "model": ModelConfig(
                provider=_PROVIDER,
                model_id=_TIER_MODEL_IDS[tier],
                capability=tier,
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


@pytest.mark.unit
class TestRouteStakesSeam:
    """``_route_stakes`` adjusts the identity's model from task stakes."""

    async def test_low_stakes_downgrades(self) -> None:
        engine = _engine(stakes=True)
        adjusted, _effort = await engine._route_stakes(
            _identity("expert"),
            _task(Stakes.LOW),
        )
        assert adjusted.model.capability == "basic"

    async def test_high_stakes_upgrades(self) -> None:
        engine = _engine(stakes=True)
        adjusted, effort = await engine._route_stakes(
            _identity("basic"),
            _task(Stakes.HIGH),
        )
        assert adjusted.model.capability == "expert"
        assert effort is ReasoningEffort.MEDIUM

    async def test_normal_stakes_keeps_medium(self) -> None:
        engine = _engine(stakes=True)
        identity = _identity("capable")
        adjusted, effort = await engine._route_stakes(
            identity,
            _task(Stakes.NORMAL),
        )
        assert adjusted.model == identity.model
        assert effort is ReasoningEffort.LOW

    async def test_low_stakes_leaves_reasoning_unset(self) -> None:
        engine = _engine(stakes=True)
        _adjusted, effort = await engine._route_stakes(
            _identity("basic"),
            _task(Stakes.LOW),
        )
        assert effort is None

    def test_engine_accepts_no_router(self) -> None:
        engine = _engine(stakes=False)
        assert engine._stakes_router is None

    async def test_no_qualifying_model_propagates_escalation(self) -> None:
        # A catalogue missing the large tier cannot serve HIGH-stakes work, so
        # the strategy raises and the engine seam propagates it to the run
        # loop (which parks or fails visibly) rather than silently keeping the
        # sub-tier model.
        small_only: dict[str, tuple[ResolvedModel, ...]] = {
            "basic": (
                ResolvedModel(
                    provider_name=_PROVIDER,
                    model_id=_TIER_MODEL_IDS["basic"],
                    alias="basic",
                    cost_per_1k_input=0.1,
                    cost_per_1k_output=0.1,
                    max_context=128000,
                    capability="basic",
                ),
            ),
        }
        engine = AgentEngine(
            provider=ScriptedProvider([]),
            stakes_router=build_stakes_router(
                StakesRoutingConfig(),
                resolver=ModelResolver(small_only),
            ),
        )
        with pytest.raises(StakesModelUnavailableError):
            await engine._route_stakes(_identity("basic"), _task(Stakes.HIGH))


@pytest.mark.unit
class TestFoldStakesReasoning:
    """The stakes-driven reasoning effort folds into the run config."""

    def test_none_effort_leaves_config_unchanged(self) -> None:
        assert (
            AgentEngineRunMixin._fold_stakes_reasoning(None, _identity("expert"), None)
            is None
        )
        existing = CompletionConfig(temperature=0.4)
        assert (
            AgentEngineRunMixin._fold_stakes_reasoning(
                existing, _identity("expert"), None
            )
            is existing
        )

    def test_builds_config_preserving_model_sampling(self) -> None:
        identity = _identity("expert").model_copy(
            update={
                "model": ModelConfig(
                    provider=_PROVIDER,
                    model_id=_TIER_MODEL_IDS["expert"],
                    capability="expert",
                    temperature=0.9,
                    max_tokens=2048,
                ),
            },
        )
        folded = AgentEngineRunMixin._fold_stakes_reasoning(
            None, identity, ReasoningEffort.HIGH
        )
        assert folded is not None
        assert folded.reasoning_effort is ReasoningEffort.HIGH
        assert folded.temperature == 0.9
        assert folded.max_tokens == 2048

    def test_preserves_existing_config_fields(self) -> None:
        existing = CompletionConfig(temperature=0.2, max_tokens=99, top_p=0.5)
        folded = AgentEngineRunMixin._fold_stakes_reasoning(
            existing, _identity("expert"), ReasoningEffort.MEDIUM
        )
        assert folded is not None
        assert folded.reasoning_effort is ReasoningEffort.MEDIUM
        assert folded.temperature == 0.2
        assert folded.max_tokens == 99
        assert folded.top_p == 0.5


@pytest.mark.unit
class TestFoldPromptCaching:
    """Prompt caching is turned on for the run per the operator setting."""

    async def test_enabled_without_resolver_defaults_on(self) -> None:
        engine = _engine(stakes=False)
        folded = await engine._fold_prompt_caching(None, _identity("expert"))
        assert folded is not None
        assert folded.prompt_caching is True

    async def test_disabled_leaves_config_unchanged(self) -> None:
        engine = _engine(stakes=False)
        engine._config_resolver = mock_of[ConfigResolver](
            get_bool=AsyncMock(return_value=False)
        )
        folded = await engine._fold_prompt_caching(None, _identity("expert"))
        assert folded is None

    async def test_enabled_preserves_existing_config(self) -> None:
        engine = _engine(stakes=False)
        engine._config_resolver = mock_of[ConfigResolver](
            get_bool=AsyncMock(return_value=True)
        )
        existing = CompletionConfig(temperature=0.3, max_tokens=64)
        folded = await engine._fold_prompt_caching(existing, _identity("expert"))
        assert folded is not None
        assert folded.prompt_caching is True
        assert folded.temperature == 0.3
        assert folded.max_tokens == 64
