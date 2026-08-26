"""Unit tests for work routing policies and their factory."""

from unittest.mock import MagicMock

import pytest

from synthorg.core.artifact import ArtifactType, ExpectedArtifact
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
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
from synthorg.providers.errors import ModelNotFoundError
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared import as_uuid, mock_of
from tests._shared.scripted_provider import ScriptedProvider, make_text_response

pytestmark = pytest.mark.unit

_THRESHOLD = 1

#: Wide enough that nothing here trips it, so a case asserting a SPLITTABLE
#: verdict is unambiguously about the operator's value rather than this one.
_A_PERMISSIVE_THRESHOLD = 50


def _task(*, title: str, description: str) -> Task:
    return Task(
        id=as_uuid("task-1"),
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
_TWO_DELIVERABLES = _LEAF_TASK.model_copy(
    update={
        "artifacts_expected": (
            ExpectedArtifact(type=ArtifactType.CODE, path="src/health.py"),
            ExpectedArtifact(type=ArtifactType.CODE, path="tests/test_health.py"),
        )
    }
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

    async def test_the_threshold_is_read_per_decision(self) -> None:
        # It answers "is this objective a team's work", which an operator
        # revises against the objectives they are actually filing. A value
        # captured at wiring time is a knob they can turn with no effect
        # until the next restart.
        resolver: MagicMock = mock_of[ConfigResolverProtocol]()
        resolver.get_int.return_value = _THRESHOLD
        policy = LeafThresholdRoutingPolicy(
            threshold=_THRESHOLD, config_resolver=resolver
        )

        await policy.decide(task=_LEAF_TASK, available_agents=())
        await policy.decide(task=_LEAF_TASK, available_agents=())

        assert resolver.get_int.await_count == 2

    async def test_the_operators_value_is_what_decides(self) -> None:
        # Wired at one deliverable, but the operator has since widened it, and
        # a two-deliverable objective is a leaf under the new value.
        resolver: MagicMock = mock_of[ConfigResolverProtocol]()
        resolver.get_int.return_value = _A_PERMISSIVE_THRESHOLD
        policy = LeafThresholdRoutingPolicy(
            threshold=_THRESHOLD, config_resolver=resolver
        )

        verdict = await policy.decide(task=_TWO_DELIVERABLES, available_agents=())

        assert verdict is RoutingVerdict.LEAF

    async def test_the_wired_value_would_have_decided_otherwise(self) -> None:
        # The other half of the pair: without the operator's write, the same
        # objective routes to a team. Asserted so the case above cannot pass
        # on a task that was a leaf either way.
        policy = LeafThresholdRoutingPolicy(threshold=_THRESHOLD)

        verdict = await policy.decide(task=_TWO_DELIVERABLES, available_agents=())

        assert verdict is RoutingVerdict.SPLITTABLE

    async def test_an_unreadable_threshold_still_routes(self) -> None:
        # A bound nobody can read must not stop the whole work spine: the
        # wired value still decides, and only the latest revision is missed.
        resolver: MagicMock = mock_of[ConfigResolverProtocol]()
        resolver.get_int.side_effect = RuntimeError("settings store unreachable")
        policy = LeafThresholdRoutingPolicy(
            threshold=_THRESHOLD, config_resolver=resolver
        )

        verdict = await policy.decide(task=_LEAF_TASK, available_agents=())

        assert verdict is RoutingVerdict.LEAF

    async def test_no_resolver_at_all_uses_the_wired_value(self) -> None:
        policy = LeafThresholdRoutingPolicy(threshold=_THRESHOLD)

        assert (
            await policy.decide(task=_LEAF_TASK, available_agents=())
            is RoutingVerdict.LEAF
        )


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

    async def test_model_call_failure_falls_back_to_deterministic(self) -> None:
        # A model that is unavailable or misconfigured (e.g. the
        # decomposition model not resolvable on its provider) must degrade
        # to the deterministic policy, never hard-fail the run: otherwise the
        # llm-judged default turns every approve into a routing failure.
        provider = ScriptedProvider(
            error=ModelNotFoundError(
                "Model not found",
                context={"provider": "test-provider", "model": "test-model-001"},
            )
        )
        policy = LlmJudgedRoutingPolicy(
            provider=provider,
            model="test-model-001",
            fallback=LeafThresholdRoutingPolicy(threshold=_THRESHOLD),
        )
        # The fallback runs for real: the leaf task -> LEAF, the parallel
        # task -> SPLITTABLE (proves delegation, not a fixed verdict).
        assert (
            await policy.decide(task=_LEAF_TASK, available_agents=())
            is RoutingVerdict.LEAF
        )
        assert (
            await policy.decide(task=_TEAM_TASK, available_agents=())
            is RoutingVerdict.SPLITTABLE
        )


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
