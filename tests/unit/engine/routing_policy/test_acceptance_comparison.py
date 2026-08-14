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

from synthorg.core.agent import AgentIdentity
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
    StakesAwareStrategy,
    StakesRoutingConfig,
)
from tests._shared import as_uuid, sid

from .conftest import LADDER, MODEL_IDS, TOTAL_COST, build_agent, build_policy

_MODEL_IDS: Final[dict[CapabilityLevel, str]] = MODEL_IDS
_TOTAL_COST: Final[dict[CapabilityLevel, float]] = TOTAL_COST
_LADDER: Final[tuple[CapabilityLevel, ...]] = LADDER


def _policy() -> CapabilityFloorPolicy:
    return build_policy()


def _agent(rung: CapabilityLevel) -> AgentIdentity:
    return build_agent(rung)


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

        flat_cost = 0.0
        graded_cost = 0.0
        staffed: list[CapabilityLevel] = []
        for task in tasks:
            # Conservative baseline: with nothing grading the work, every
            # subtask goes to the strongest agent.
            flat_cost += _TOTAL_COST["expert"]

            # `_cheapest_clearing` raises unless its pick clears the floor,
            # so reaching here IS the adequacy claim. Counting `clears` again
            # afterwards could only ever add up to the number of tasks.
            picked = _cheapest_clearing(task, roster, policy)
            rung = picked.model.capability
            assert rung is not None
            graded_cost += _TOTAL_COST[rung]
            staffed.append(rung)

        assert graded_cost < flat_cost
        # The saving comes from staffing a weaker AGENT, never from lowering
        # the expert's own model: at least one task went to someone else. A
        # re-pointing implementation would staff the expert every time and
        # still be cheaper, which is exactly what this has to rule out.
        assert any(rung != "expert" for rung in staffed)

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
