"""Unit tests for work routing policies and their factory."""

import pytest

from synthorg.core.enums import Priority, TaskStatus, TaskType
from synthorg.core.task import Task
from synthorg.engine.pipeline.errors import WorkRoutingUndecidableError
from synthorg.engine.pipeline.models import RoutingVerdict
from synthorg.engine.pipeline.policy import (
    ROUTING_POLICY_ALWAYS_TEAM,
    ROUTING_POLICY_LEAF_THRESHOLD,
    ROUTING_POLICY_LLM_JUDGED,
    AlwaysTeamRoutingPolicy,
    LeafThresholdRoutingPolicy,
    LlmJudgedRoutingPolicy,
    build_work_routing_policy,
)
from tests._shared.scripted_provider import ScriptedProvider, make_text_response

pytestmark = pytest.mark.unit

_THRESHOLD = 1


def _task(*, title: str, description: str) -> Task:
    return Task(
        id="task-1",
        title=title,
        description=description,
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-1",
        created_by="operator-1",
        status=TaskStatus.CREATED,
    )


_LEAF_TASK = _task(
    title="Add health endpoint",
    description="First add the route, then return a JSON status body.",
)
_TEAM_TASK = _task(
    title="Build the platform",
    description="Implement auth and billing independently, in parallel.",
)


class TestLeafThresholdRoutingPolicy:
    async def test_sequential_small_is_leaf(self) -> None:
        policy = LeafThresholdRoutingPolicy(threshold=_THRESHOLD)
        verdict = await policy.decide(task=_LEAF_TASK, available_agents=())
        assert verdict is RoutingVerdict.LEAF

    async def test_parallel_is_splittable(self) -> None:
        policy = LeafThresholdRoutingPolicy(threshold=_THRESHOLD)
        verdict = await policy.decide(task=_TEAM_TASK, available_agents=())
        assert verdict is RoutingVerdict.SPLITTABLE

    @pytest.mark.parametrize("bad_threshold", [0, -1])
    def test_non_positive_threshold_rejected(self, bad_threshold: int) -> None:
        with pytest.raises(ValueError, match="threshold must be positive"):
            LeafThresholdRoutingPolicy(threshold=bad_threshold)


class TestAlwaysTeamRoutingPolicy:
    async def test_always_splittable(self) -> None:
        policy = AlwaysTeamRoutingPolicy()
        verdict = await policy.decide(task=_LEAF_TASK, available_agents=())
        assert verdict is RoutingVerdict.SPLITTABLE


class TestLlmJudgedRoutingPolicy:
    async def test_parses_splittable(self) -> None:
        provider = ScriptedProvider(response=make_text_response("SPLITTABLE"))
        policy = LlmJudgedRoutingPolicy(
            provider=provider,
            model="test-model-001",
            fallback=LeafThresholdRoutingPolicy(threshold=_THRESHOLD),
        )
        verdict = await policy.decide(task=_LEAF_TASK, available_agents=())
        assert verdict is RoutingVerdict.SPLITTABLE

    async def test_parses_leaf(self) -> None:
        provider = ScriptedProvider(response=make_text_response("LEAF"))
        policy = LlmJudgedRoutingPolicy(
            provider=provider,
            model="test-model-001",
            fallback=LeafThresholdRoutingPolicy(threshold=_THRESHOLD),
        )
        verdict = await policy.decide(task=_TEAM_TASK, available_agents=())
        assert verdict is RoutingVerdict.LEAF

    async def test_unparseable_falls_back_to_deterministic(self) -> None:
        provider = ScriptedProvider(
            response=make_text_response("I cannot determine that.")
        )
        policy = LlmJudgedRoutingPolicy(
            provider=provider,
            model="test-model-001",
            fallback=LeafThresholdRoutingPolicy(threshold=_THRESHOLD),
        )
        # Fallback classifies the leaf task as LEAF deterministically.
        verdict = await policy.decide(task=_LEAF_TASK, available_agents=())
        assert verdict is RoutingVerdict.LEAF

    async def test_negated_verdict_falls_back_to_deterministic(self) -> None:
        provider = ScriptedProvider(
            response=make_text_response("This task is not splittable.")
        )
        policy = LlmJudgedRoutingPolicy(
            provider=provider,
            model="test-model-001",
            fallback=LeafThresholdRoutingPolicy(threshold=_THRESHOLD),
        )
        # "not splittable" must not be read as SPLITTABLE; the leaf
        # task falls back to the deterministic LEAF verdict.
        verdict = await policy.decide(task=_LEAF_TASK, available_agents=())
        assert verdict is RoutingVerdict.LEAF

    async def test_both_verdict_words_falls_back_to_deterministic(self) -> None:
        provider = ScriptedProvider(
            response=make_text_response("Splittable, though it could be a leaf.")
        )
        policy = LlmJudgedRoutingPolicy(
            provider=provider,
            model="test-model-001",
            fallback=LeafThresholdRoutingPolicy(threshold=_THRESHOLD),
        )
        # Mentioning both words is ambiguous, not an implicit SPLITTABLE;
        # the leaf task falls back to the deterministic LEAF verdict.
        verdict = await policy.decide(task=_LEAF_TASK, available_agents=())
        assert verdict is RoutingVerdict.LEAF


class TestBuildWorkRoutingPolicy:
    def test_leaf_threshold(self) -> None:
        policy = build_work_routing_policy(
            ROUTING_POLICY_LEAF_THRESHOLD,
            threshold=_THRESHOLD,
        )
        assert isinstance(policy, LeafThresholdRoutingPolicy)

    def test_always_team(self) -> None:
        policy = build_work_routing_policy(
            ROUTING_POLICY_ALWAYS_TEAM,
            threshold=_THRESHOLD,
        )
        assert isinstance(policy, AlwaysTeamRoutingPolicy)

    def test_llm_judged_requires_provider_and_model(self) -> None:
        with pytest.raises(WorkRoutingUndecidableError):
            build_work_routing_policy(
                ROUTING_POLICY_LLM_JUDGED,
                threshold=_THRESHOLD,
            )

    def test_llm_judged_built_with_provider(self) -> None:
        provider = ScriptedProvider(response=make_text_response("LEAF"))
        policy = build_work_routing_policy(
            ROUTING_POLICY_LLM_JUDGED,
            threshold=_THRESHOLD,
            provider=provider,
            model="test-model-001",
        )
        assert isinstance(policy, LlmJudgedRoutingPolicy)

    def test_unknown_discriminator_raises(self) -> None:
        with pytest.raises(WorkRoutingUndecidableError, match="Unknown routing"):
            build_work_routing_policy("nope", threshold=_THRESHOLD)
