"""Tests for the authority + dissent log resolution strategy."""

import pytest

from synthorg.communication.conflict_resolution.authority_strategy import (
    AuthorityResolver,
)
from synthorg.communication.conflict_resolution.models import (
    ConflictResolutionOutcome,
)
from synthorg.communication.delegation.hierarchy import (
    HierarchyResolver,
)
from synthorg.communication.enums import ConflictResolutionStrategy
from synthorg.communication.errors import ConflictHierarchyError
from synthorg.core.enums import SeniorityLevel

from .conftest import make_conflict, make_position

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestAuthorityResolverSeniority:
    async def test_higher_seniority_wins(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        resolver = AuthorityResolver(hierarchy=hierarchy)
        conflict = make_conflict(
            positions=(
                make_position(
                    agent_id="sr_dev",
                    level=SeniorityLevel.SENIOR,
                    position="Use microservices",
                ),
                make_position(
                    agent_id="jr_dev",
                    level=SeniorityLevel.JUNIOR,
                    position="Use monolith",
                ),
            ),
        )
        resolution = await resolver.resolve(conflict)
        assert resolution.winning_agent_id == "sr_dev"
        assert resolution.outcome == ConflictResolutionOutcome.RESOLVED_BY_AUTHORITY

    async def test_lower_seniority_loses(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        resolver = AuthorityResolver(hierarchy=hierarchy)
        conflict = make_conflict(
            positions=(
                make_position(
                    agent_id="jr_dev",
                    level=SeniorityLevel.JUNIOR,
                    position="Quick fix",
                ),
                make_position(
                    agent_id="sr_dev",
                    level=SeniorityLevel.SENIOR,
                    position="Proper refactor",
                ),
            ),
        )
        resolution = await resolver.resolve(conflict)
        assert resolution.winning_agent_id == "sr_dev"

    async def test_c_suite_beats_lead(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        resolver = AuthorityResolver(hierarchy=hierarchy)
        conflict = make_conflict(
            positions=(
                make_position(
                    agent_id="cto",
                    level=SeniorityLevel.C_SUITE,
                    position="New architecture",
                ),
                make_position(
                    agent_id="backend_lead",
                    level=SeniorityLevel.LEAD,
                    position="Keep current",
                ),
            ),
        )
        resolution = await resolver.resolve(conflict)
        assert resolution.winning_agent_id == "cto"


@pytest.mark.unit
class TestAuthorityResolverHierarchy:
    async def test_equal_seniority_closer_to_lcm_wins(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        resolver = AuthorityResolver(hierarchy=hierarchy)
        # backend_lead and frontend_lead are both LEADs under cto
        # backend_lead is first in hierarchy, same distance to cto
        conflict = make_conflict(
            positions=(
                make_position(
                    agent_id="backend_lead",
                    level=SeniorityLevel.LEAD,
                    position="Use REST",
                ),
                make_position(
                    agent_id="frontend_lead",
                    level=SeniorityLevel.LEAD,
                    position="Use GraphQL",
                ),
            ),
        )
        resolution = await resolver.resolve(conflict)
        # Both are depth 1 from LCM (cto), so first position wins
        assert resolution.winning_agent_id == "backend_lead"

    async def test_cross_department_no_common_manager_raises(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        resolver = AuthorityResolver(hierarchy=hierarchy)
        # cto and qa_head have no common manager
        conflict = make_conflict(
            positions=(
                make_position(
                    agent_id="cto",
                    level=SeniorityLevel.C_SUITE,
                    position="Ship fast",
                ),
                make_position(
                    agent_id="qa_head",
                    level=SeniorityLevel.C_SUITE,
                    position="More testing",
                    department="qa",
                ),
            ),
        )
        with pytest.raises(ConflictHierarchyError, match="No common manager"):
            await resolver.resolve(conflict)

    async def test_subordinate_vs_supervisor_equal_seniority(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        """When seniority is equal but one is closer to LCM, closer wins."""
        resolver = AuthorityResolver(hierarchy=hierarchy)
        conflict = make_conflict(
            positions=(
                make_position(
                    agent_id="backend_lead",
                    level=SeniorityLevel.SENIOR,
                    position="Approach A",
                ),
                make_position(
                    agent_id="sr_dev",
                    level=SeniorityLevel.SENIOR,
                    position="Approach B",
                ),
            ),
        )
        resolution = await resolver.resolve(conflict)
        # backend_lead is closer to LCM (cto) — depth 0 (IS the LCM) or 1
        assert resolution.winning_agent_id == "backend_lead"


@pytest.mark.unit
class TestAuthorityResolverThreeParticipants:
    async def test_three_participants_highest_seniority_wins(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        """Authority resolution picks highest seniority among 3+ agents."""
        resolver = AuthorityResolver(hierarchy=hierarchy)
        conflict = make_conflict(
            positions=(
                make_position(
                    agent_id="jr_dev",
                    level=SeniorityLevel.JUNIOR,
                    position="Quick hack",
                ),
                make_position(
                    agent_id="sr_dev",
                    level=SeniorityLevel.SENIOR,
                    position="Proper refactor",
                ),
                make_position(
                    agent_id="backend_lead",
                    level=SeniorityLevel.LEAD,
                    position="Full redesign",
                ),
            ),
        )
        resolution = await resolver.resolve(conflict)
        assert resolution.winning_agent_id == "backend_lead"
        assert resolution.outcome == ConflictResolutionOutcome.RESOLVED_BY_AUTHORITY

    async def test_three_participants_produces_two_dissent_records(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        """With 3 participants, the two losers each produce a dissent record."""
        resolver = AuthorityResolver(hierarchy=hierarchy)
        conflict = make_conflict(
            positions=(
                make_position(
                    agent_id="jr_dev",
                    level=SeniorityLevel.JUNIOR,
                    position="Approach A",
                ),
                make_position(
                    agent_id="sr_dev",
                    level=SeniorityLevel.SENIOR,
                    position="Approach B",
                ),
                make_position(
                    agent_id="backend_lead",
                    level=SeniorityLevel.LEAD,
                    position="Approach C",
                ),
            ),
        )
        resolution = await resolver.resolve(conflict)
        records = resolver.build_dissent_records(conflict, resolution)
        assert len(records) == 2
        dissenter_ids = {r.dissenting_agent_id for r in records}
        assert dissenter_ids == {"jr_dev", "sr_dev"}
        for record in records:
            assert record.strategy_used == ConflictResolutionStrategy.AUTHORITY


@pytest.mark.unit
class TestAuthorityResolverDissentRecord:
    async def test_dissent_record_has_loser_info(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        resolver = AuthorityResolver(hierarchy=hierarchy)
        conflict = make_conflict(
            positions=(
                make_position(
                    agent_id="sr_dev",
                    level=SeniorityLevel.SENIOR,
                    position="My approach",
                ),
                make_position(
                    agent_id="jr_dev",
                    level=SeniorityLevel.JUNIOR,
                    position="Other approach",
                ),
            ),
        )
        resolution = await resolver.resolve(conflict)
        records = resolver.build_dissent_records(conflict, resolution)
        record = records[0]
        assert record.dissenting_agent_id == "jr_dev"
        assert record.dissenting_position == "Other approach"
        assert record.strategy_used == ConflictResolutionStrategy.AUTHORITY

    async def test_dissent_record_id_format(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        resolver = AuthorityResolver(hierarchy=hierarchy)
        conflict = make_conflict()
        resolution = await resolver.resolve(conflict)
        records = resolver.build_dissent_records(conflict, resolution)
        record = records[0]
        assert record.id.startswith("dissent-")

    async def test_unreachable_agent_in_hierarchy_raises(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        """Agent not in hierarchy at all raises ConflictHierarchyError."""
        resolver = AuthorityResolver(hierarchy=hierarchy)
        conflict = make_conflict(
            positions=(
                make_position(
                    agent_id="sr_dev",
                    level=SeniorityLevel.SENIOR,
                    position="Approach A",
                ),
                make_position(
                    agent_id="ghost_agent",
                    level=SeniorityLevel.SENIOR,
                    position="Approach B",
                ),
            ),
        )
        with pytest.raises(ConflictHierarchyError):
            await resolver.resolve(conflict)

    async def test_cross_department_logged(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        """Cross-department flag triggers logging but resolution works."""
        resolver = AuthorityResolver(hierarchy=hierarchy)
        # sr_dev (eng, SENIOR) vs qa_eng (qa, JUNIOR) — different seniority
        conflict = make_conflict(
            positions=(
                make_position(
                    agent_id="sr_dev",
                    level=SeniorityLevel.SENIOR,
                    position="Eng approach",
                ),
                make_position(
                    agent_id="qa_eng",
                    level=SeniorityLevel.JUNIOR,
                    position="QA approach",
                    department="qa",
                ),
            ),
        )
        resolution = await resolver.resolve(conflict)
        assert resolution.winning_agent_id == "sr_dev"
