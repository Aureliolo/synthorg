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

from synthorg.core.task import Task
from synthorg.core.task_enums import Stakes, TaskType
from tests._shared import as_uuid

from .conftest import LADDER as _LADDER
from .conftest import TOTAL_COST as _TOTAL_COST
from .conftest import build_agent as _agent
from .conftest import build_policy as _policy


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
        task = _task(stakes)
        picked = next(
            rung
            for agent, rung in roster
            if policy.judge(
                model=agent.model,
                stakes=task.stakes,
                complexity=task.estimated_complexity,
            ).fit
            != "lower"
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

    if policy.judge(model=_agent(_LADDER[weaker]).model, stakes=stakes).sanctioned:
        assert policy.judge(
            model=_agent(_LADDER[stronger]).model, stakes=stakes
        ).sanctioned
