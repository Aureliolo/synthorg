"""Acceptance: stakes-aware routing beats flat on a mixed brief.

Encodes the acceptance criterion as a deterministic
simulation: a mixed brief is decomposed (so each subtask carries an
assessed stakes level), then routed under both the flat control arm and
the stakes-aware arm. With a conservative flat baseline (every subtask
on the strong tier), stakes-aware:

* routes low-stakes subtasks to cheap models and high/critical subtasks
  to the strong model plus the red-team mark, and
* drops total cost while every selection still meets its stakes tier
  requirement (equal-or-better tier adequacy).

The selection is a pure function of the resolver catalogue and the
per-stakes tier requirements, so the comparison is fully reproducible
without any LLM spend.
"""

from typing import Final

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import Task
from synthorg.core.task_enums import Complexity, Stakes, TaskType
from synthorg.core.types import ModelTier
from synthorg.engine.decomposition.classifier import TaskStructureClassifier
from synthorg.engine.decomposition.models import (
    DecompositionContext,
    DecompositionPlan,
    SubtaskDefinition,
)
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.engine.routing_policy import (
    FlatStrategy,
    StakesAwareStrategy,
    StakesRoutingConfig,
)
from synthorg.engine.routing_policy.tiers import meets_required
from synthorg.providers.routing.models import ResolvedModel
from synthorg.providers.routing.resolver import ModelResolver
from tests._shared import as_uuid, sid
from tests._shared.scripted_provider import make_e2e_identity

_PROVIDER: Final[str] = "example-provider"
_TIER_MODEL_IDS: Final[dict[ModelTier, str]] = {
    "small": "example-small-001",
    "medium": "example-medium-001",
    "large": "example-large-001",
}
# total_cost_per_1k = input + output; strictly increasing by tier.
_TIER_TOTAL_COST: Final[dict[ModelTier, float]] = {
    "small": 0.2,
    "medium": 1.0,
    "large": 4.0,
}


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
                tier=tier,
            ),
        )
        for tier in _TIER_MODEL_IDS
    }
    return ModelResolver(index)


def _agent(tier: ModelTier) -> AgentIdentity:
    return make_e2e_identity().model_copy(
        update={
            "model": ModelConfig(
                provider=_PROVIDER,
                model_id=_TIER_MODEL_IDS[tier],
                model_tier=tier,
            ),
        },
    )


def _parent() -> Task:
    return Task(
        id=as_uuid("brief-1"),
        title="Mixed brief",
        description="Several subtasks of varying stakes",
        type=TaskType.DEVELOPMENT,
        project="proj-1",
        created_by="founder",
    )


def _mixed_plan() -> DecompositionPlan:
    """Mostly low/normal stakes with a couple of high/critical subtasks."""
    # Subtasks deliberately omit an explicit ``stakes=``: this acceptance
    # test exercises the assessment end-to-end, so ``DecompositionService``
    # derives each stakes level from ``estimated_complexity`` plus the
    # description keywords ("architecture", "production", "irreversible").
    # Keep those keyword cues in sync with the heuristic's inputs if they
    # change, or the per-subtask tier assertions below will drift.
    subtasks = (
        SubtaskDefinition(
            id=sid("st-doc"),
            title="Update the changelog",
            description="Tidy wording in the docs",
            estimated_complexity=Complexity.SIMPLE,
            expected_artifacts=("CHANGELOG.md",),
        ),
        SubtaskDefinition(
            id=sid("st-format"),
            title="Reformat helper module",
            description="Run the formatter over a utility file",
            estimated_complexity=Complexity.SIMPLE,
            expected_artifacts=("src/helpers.py",),
        ),
        SubtaskDefinition(
            id=sid("st-feature"),
            title="Add a list endpoint",
            description="Implement a straightforward read endpoint",
            estimated_complexity=Complexity.MEDIUM,
            expected_artifacts=("src/api/list_endpoint.py",),
        ),
        SubtaskDefinition(
            id=sid("st-arch"),
            title="Design the sharding architecture",
            description="Make the core architecture decision for sharding",
            estimated_complexity=Complexity.COMPLEX,
            expected_artifacts=("docs/sharding.md",),
        ),
        SubtaskDefinition(
            id=sid("st-migrate"),
            title="Production data migration",
            description="Run an irreversible production migration",
            estimated_complexity=Complexity.MEDIUM,
            expected_artifacts=("migrations/0001_shard.sql",),
        ),
    )
    return DecompositionPlan(parent_task_id=sid("brief-1"), subtasks=subtasks)


class _StaticStrategy:
    def __init__(self, plan: DecompositionPlan) -> None:
        self._plan = plan

    async def decompose(
        self,
        task: Task,
        context: DecompositionContext,
    ) -> DecompositionPlan:
        del task, context
        return self._plan

    def get_strategy_name(self) -> str:
        return "static-test"


async def _decomposed_tasks() -> tuple[Task, ...]:
    service = DecompositionService(
        _StaticStrategy(_mixed_plan()),
        TaskStructureClassifier(),
    )
    result = await service.decompose_task(_parent(), DecompositionContext())
    return result.created_tasks


@pytest.mark.unit
class TestStakesAwareBeatsFlatOnMixedBrief:
    """The core acceptance comparison for stakes-aware routing."""

    async def test_cost_drops_at_equal_or_better_tier_adequacy(self) -> None:
        tasks = await _decomposed_tasks()
        # Conservative flat baseline: every subtask on the strong tier.
        flat_agent = _agent("large")
        config = StakesRoutingConfig()
        stakes_aware = StakesAwareStrategy(config=config, resolver=_resolver())
        flat = FlatStrategy()

        flat_cost = 0.0
        aware_cost = 0.0
        aware_adequate = 0
        for task in tasks:
            required = config.stakes_tiers.for_stakes(task.stakes)

            flat_decision = await flat.route(task=task, identity=flat_agent)
            flat_tier = flat_decision.selected_model.model_tier
            assert flat_tier is not None
            flat_cost += _TIER_TOTAL_COST[flat_tier]

            aware_decision = await stakes_aware.route(task=task, identity=flat_agent)
            aware_tier = aware_decision.selected_model.model_tier
            assert aware_tier is not None
            aware_cost += _TIER_TOTAL_COST[aware_tier]
            # Every stakes-aware selection meets its stakes tier requirement.
            aware_adequate += int(meets_required(aware_tier, required))

        assert aware_adequate == len(tasks)
        # Total cost strictly drops versus the all-strong flat baseline.
        assert aware_cost < flat_cost

    async def test_low_stakes_cheap_high_stakes_strong_with_red_team(self) -> None:
        tasks = {t.id: t for t in await _decomposed_tasks()}
        stakes_aware = StakesAwareStrategy(resolver=_resolver())
        agent = _agent("large")

        doc = await stakes_aware.route(task=tasks[as_uuid("st-doc")], identity=agent)
        assert tasks[as_uuid("st-doc")].stakes is Stakes.LOW
        assert doc.selected_model.model_tier == "small"
        assert doc.red_team_required is False

        arch = await stakes_aware.route(task=tasks[as_uuid("st-arch")], identity=agent)
        assert tasks[as_uuid("st-arch")].stakes is Stakes.HIGH
        assert arch.selected_model.model_tier == "large"
        assert arch.red_team_required is True

        migrate = await stakes_aware.route(
            task=tasks[as_uuid("st-migrate")], identity=agent
        )
        assert tasks[as_uuid("st-migrate")].stakes is Stakes.CRITICAL
        assert migrate.selected_model.model_tier == "large"
        assert migrate.red_team_required is True
