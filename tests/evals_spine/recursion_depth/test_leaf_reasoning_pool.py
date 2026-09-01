# module-kind: tests
"""Units may build at their own reasoning depth, via a second pool of agents.

The one published harness ablation with numbers behind it puts the win in the
SCHEDULE rather than the level: reasoning at the deepest setting throughout
scored worse than reasoning moderately throughout, and reasoning deeply while
planning and verifying but moderately while building beat both. Everything here
protects the two properties that makes safe to test: an agent is still a fixed
pair, and a matrix that asks for nothing gets exactly what it got before.
"""

import zlib

import pytest

from evals.recursion_depth.manifest import ModelPair
from evals.recursion_depth.runner import _owner_for
from evals.recursion_depth.staffing import BUILDER_COUNT, SweepRoster, build_roster
from synthorg.core.completion_enums import ReasoningEffort
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_EXECUTOR = ModelPair(
    provider="example-provider",
    model_id="example-capable-001",
    capability="capable",
    family="example-family-a",
    temperature=0.7,
    top_p=1.0,
    reasoning_effort=ReasoningEffort.HIGH,
    max_tokens=131_072,
)
_REVIEWER = ModelPair(
    provider="example-provider",
    model_id="example-expert-001",
    capability="expert",
    family="example-family-b",
    reasoning_effort=ReasoningEffort.HIGH,
    max_tokens=262_144,
)


async def _roster(*, leaf_effort: ReasoningEffort | None) -> SweepRoster:
    """Build a roster with the capability policy stubbed out.

    Returns:
        The roster.
    """
    from synthorg.engine.routing_policy.capability_policy import CapabilityPolicy

    return await build_roster(
        executor=_EXECUTOR,
        reviewer=_REVIEWER,
        capability=mock_of[CapabilityPolicy](),
        leaf_effort=leaf_effort,
    )


class TestAMatrixThatAsksForNothingIsUnchanged:
    """The default has to be the OLD behaviour exactly, not merely similar.

    This landed while cells were recording, so a default that differed by any
    observable would have separated arms of a live experiment by a treatment
    nobody declared.
    """

    async def test_no_second_pool_is_registered(self) -> None:
        roster = await _roster(leaf_effort=None)

        assert roster.unit_builders == ()
        assert roster.leaf_builders == roster.builders

    async def test_every_builder_keeps_the_executors_own_depth(self) -> None:
        roster = await _roster(leaf_effort=None)

        assert {agent.model.reasoning_effort for agent in roster.builders} == {
            ReasoningEffort.HIGH
        }

    async def test_a_unit_reaches_the_agent_the_digest_names(self) -> None:
        """Pinned against the derivation, not against the other spelling.

        Comparing ``building=True`` with ``building=False`` proves nothing
        while the two pools are the same object: both sides read one list at
        one index, so the assertion holds for ANY selection rule applied
        consistently, including a rule that changed. What has to stay fixed
        is WHICH agent a unit id reaches, so the expectation is computed here
        from the documented digest rather than from the code under test.
        """
        roster = await _roster(leaf_effort=None)

        for unit in ("unit-a", "unit-b", "unit-c", "unit-d", "unit-e"):
            wanted = roster.builders[
                zlib.crc32(unit.encode("utf-8")) % len(roster.builders)
            ]

            assert _owner_for(roster, unit) == wanted
            assert _owner_for(roster, unit, building=True) == wanted

    async def test_the_pools_are_indexed_alike_when_a_second_one_exists(self) -> None:
        # The complement, and the reason the digest is taken over the unit id
        # ALONE: a seed folding in the pool would re-shuffle every ownership
        # the moment a second pool appeared, moving assignments as a side
        # effect of a reasoning change and leaving the arms differing by more
        # than the treatment.
        roster = await _roster(leaf_effort=ReasoningEffort.LOW)

        for unit in ("unit-a", "unit-b", "unit-c", "unit-d", "unit-e"):
            index = zlib.crc32(unit.encode("utf-8")) % len(roster.builders)

            assert _owner_for(roster, unit) == roster.builders[index]
            built_by = _owner_for(roster, unit, building=True)
            assert built_by == roster.leaf_builders[index]


class TestAskingForItBindsASecondPool:
    """Different depth means a different AGENT, never a re-pointed pair."""

    async def test_units_build_on_the_depth_that_was_asked_for(self) -> None:
        roster = await _roster(leaf_effort=ReasoningEffort.LOW)

        assert {agent.model.reasoning_effort for agent in roster.leaf_builders} == {
            ReasoningEffort.LOW
        }

    async def test_planning_and_assembly_keep_the_executors_depth(self) -> None:
        # The sandwich is only a sandwich if the outer phases stay deep. A
        # change that lowered every builder would be the losing arm of the
        # published ablation wearing the winning arm's name.
        roster = await _roster(leaf_effort=ReasoningEffort.LOW)

        assert roster.lead.model.reasoning_effort is ReasoningEffort.HIGH
        assert {agent.model.reasoning_effort for agent in roster.builders} == {
            ReasoningEffort.HIGH
        }

    async def test_the_two_pools_are_different_agents(self) -> None:
        # Not one agent re-pointed: the product's own rule is that work needing
        # a different binding goes to a different agent, and an id reused
        # across two bindings is that rule broken in the registry.
        roster = await _roster(leaf_effort=ReasoningEffort.LOW)

        ordinary = {agent.id for agent in roster.builders}
        units = {agent.id for agent in roster.leaf_builders}

        assert ordinary.isdisjoint(units)
        assert len(units) == BUILDER_COUNT

    async def test_both_pools_are_registered_so_a_reviewer_can_resolve_them(
        self,
    ) -> None:
        # A dispatched identity the registry does not hold is the defect the
        # roster-held-gate-roles rule exists for: it would be absent from every
        # roster read and its verdicts comparable to nothing.
        roster = await _roster(leaf_effort=ReasoningEffort.LOW)

        held = {agent.id for agent in await roster.registry.list_active()}

        assert {agent.id for agent in roster.leaf_builders} <= held
        assert {agent.id for agent in roster.builders} <= held

    async def test_units_still_spread_across_the_pool(self) -> None:
        roster = await _roster(leaf_effort=ReasoningEffort.LOW)

        reached = {
            _owner_for(roster, f"unit-{index}", building=True).id for index in range(40)
        }

        assert len(reached) > 1

    async def test_the_role_is_unchanged_so_no_new_role_becomes_assignable(
        self,
    ) -> None:
        # The planner is offered roles, not agents. A second pool under a new
        # role name would silently widen what a plan item may be owned by.
        roster = await _roster(leaf_effort=ReasoningEffort.LOW)

        assert {agent.role for agent in roster.leaf_builders} == {
            agent.role for agent in roster.builders
        }
