"""Acceptance: a capability-graded roster beats a uniformly strong one.

Encodes the acceptance criterion as a deterministic simulation: a mixed
brief is decomposed (so each subtask carries an assessed stakes level),
then staffed two ways from the same roster of one agent per rung. Without
a capability floor an operator has no basis for putting cheap work on a
cheap agent, so the safe arm runs everything on the expert. With one:

* low-stakes subtasks are staffable by the basic agent and high/critical
  subtasks require the expert plus the red-team mark, and
* total cost drops while every pick still clears its stakes requirement.

The saving comes from choosing the agent, never from swapping the model
behind one agent's name: each agent runs the pair it was configured with
throughout, which is what keeps its own history comparable.

The selection is a pure function of the capability registry and the
per-stakes requirements, so the comparison is fully reproducible without
any LLM spend.
"""

from typing import Final

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import Task
from synthorg.core.task_enums import Complexity, Stakes, TaskType
from synthorg.core.types import CapabilityLevel
from synthorg.engine.decomposition.classifier import TaskStructureClassifier
from synthorg.engine.decomposition.models import (
    DecompositionContext,
    DecompositionPlan,
    SubtaskDefinition,
)
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.engine.routing_policy import (
    CapabilityFloorPolicy,
    FlatStrategy,
    ResolvedAgentCapabilityReader,
    StakesAwareStrategy,
    StakesCapabilityFloor,
    StakesRoutingConfig,
)
from synthorg.providers.routing.models import ResolvedModel
from synthorg.providers.routing.resolver import ModelResolver
from tests._shared import as_uuid, sid
from tests._shared.scripted_provider import make_e2e_identity

_PROVIDER: Final[str] = "example-provider"
_MODEL_IDS: Final[dict[CapabilityLevel, str]] = {
    "basic": "example-basic-001",
    "capable": "example-capable-001",
    "expert": "example-expert-001",
}
# total_cost_per_1k = input + output; strictly increasing by rung.
_TOTAL_COST: Final[dict[CapabilityLevel, float]] = {
    "basic": 0.2,
    "capable": 1.0,
    "expert": 4.0,
}
# Weakest first, so the first agent clearing a floor is the cheapest one.
_LADDER: Final[tuple[CapabilityLevel, ...]] = ("basic", "capable", "expert")


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


def _roster() -> tuple[AgentIdentity, ...]:
    return tuple(_agent(rung) for rung in _LADDER)


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
    # change, or the per-subtask assertions below will drift.
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


def _cheapest_clearing(
    task: Task,
    roster: tuple[AgentIdentity, ...],
    policy: CapabilityFloorPolicy,
) -> AgentIdentity:
    """Return the weakest roster agent that clears the task's floor.

    Returns:
        The agent an organisation staffing by capability would pick.

    Raises:
        AssertionError: When the roster cannot staff the task at all, which
            would make the comparison below vacuous rather than failing.
    """
    required = policy.required_for(task.stakes)
    for agent in roster:
        if policy.clears(agent.model, required):
            return agent
    msg = f"roster cannot staff {task.stakes.value}-stakes work at {required}"
    raise AssertionError(msg)


@pytest.mark.unit
class TestCapabilityGradedRosterBeatsUniformlyStrong:
    """The core acceptance comparison for capability-graded staffing."""

    async def test_cost_drops_at_equal_or_better_capability_adequacy(self) -> None:
        tasks = await _decomposed_tasks()
        policy = _policy()
        roster = _roster()
        # Conservative baseline: with nothing grading the work, every subtask
        # goes to the strongest agent.
        strongest = _agent("expert")

        flat_cost = 0.0
        graded_cost = 0.0
        adequate = 0
        for task in tasks:
            required = policy.required_for(task.stakes)
            flat_cost += _TOTAL_COST["expert"]

            picked = _cheapest_clearing(task, roster, policy)
            rung = picked.model.capability
            assert rung is not None
            graded_cost += _TOTAL_COST[rung]
            adequate += int(policy.clears(picked.model, required))

        assert adequate == len(tasks)
        assert graded_cost < flat_cost
        # The strongest agent is never re-pointed at a cheaper model to make
        # that saving; it simply is not the one staffed.
        assert strongest.model.model_id == _MODEL_IDS["expert"]

    async def test_low_stakes_cheap_high_stakes_strong_with_red_team(self) -> None:
        tasks = {t.id: t for t in await _decomposed_tasks()}
        policy = _policy()
        roster = _roster()
        gate = StakesAwareStrategy(config=StakesRoutingConfig(), floor_policy=policy)

        doc_task = tasks[as_uuid("st-doc")]
        assert doc_task.stakes is Stakes.LOW
        doc_agent = _cheapest_clearing(doc_task, roster, policy)
        assert doc_agent.model.capability == "basic"
        doc = await gate.route(task=doc_task, identity=doc_agent)
        assert doc.red_team_required is False

        arch_task = tasks[as_uuid("st-arch")]
        assert arch_task.stakes is Stakes.HIGH
        arch_agent = _cheapest_clearing(arch_task, roster, policy)
        assert arch_agent.model.capability == "expert"
        arch = await gate.route(task=arch_task, identity=arch_agent)
        assert arch.red_team_required is True

        migrate_task = tasks[as_uuid("st-migrate")]
        assert migrate_task.stakes is Stakes.CRITICAL
        migrate_agent = _cheapest_clearing(migrate_task, roster, policy)
        assert migrate_agent.model.capability == "expert"
        migrate = await gate.route(task=migrate_task, identity=migrate_agent)
        assert migrate.red_team_required is True

    async def test_the_flat_arm_stays_a_true_no_op(self) -> None:
        """The control arm must impose nothing, or it is not a control."""
        tasks = await _decomposed_tasks()
        flat = FlatStrategy()

        for task in tasks:
            decision = await flat.route(task=task, identity=_agent("basic"))
            assert decision.required_capability is None
            assert decision.red_team_required is False
