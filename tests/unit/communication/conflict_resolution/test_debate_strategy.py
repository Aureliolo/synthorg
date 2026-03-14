"""Tests for the structured debate + judge resolution strategy."""

import pytest

from synthorg.communication.conflict_resolution.config import DebateConfig
from synthorg.communication.conflict_resolution.debate_strategy import (
    DebateResolver,
)
from synthorg.communication.conflict_resolution.models import (
    Conflict,
    ConflictResolutionOutcome,
)
from synthorg.communication.conflict_resolution.protocol import JudgeDecision
from synthorg.communication.delegation.hierarchy import (
    HierarchyResolver,
)
from synthorg.communication.enums import ConflictResolutionStrategy
from synthorg.communication.errors import (
    ConflictHierarchyError,
    ConflictStrategyError,
)
from synthorg.core.enums import SeniorityLevel

from .conftest import make_conflict, make_position

pytestmark = pytest.mark.timeout(30)


class FakeJudgeEvaluator:
    """Fake judge evaluator for testing."""

    def __init__(self, winner_id: str, reasoning: str = "Judge decided") -> None:
        self._winner_id = winner_id
        self._reasoning = reasoning
        self.calls: list[tuple[Conflict, str]] = []

    async def evaluate(
        self,
        conflict: Conflict,
        judge_agent_id: str,
    ) -> JudgeDecision:
        self.calls.append((conflict, judge_agent_id))
        return JudgeDecision(self._winner_id, self._reasoning)


@pytest.mark.unit
class TestDebateResolverWithJudge:
    async def test_judge_evaluator_picks_winner(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        judge = FakeJudgeEvaluator(winner_id="sr_dev")
        resolver = DebateResolver(
            hierarchy=hierarchy,
            config=DebateConfig(judge="shared_manager"),
            judge_evaluator=judge,
        )
        conflict = make_conflict(
            positions=(
                make_position(agent_id="sr_dev", level=SeniorityLevel.SENIOR),
                make_position(
                    agent_id="jr_dev",
                    level=SeniorityLevel.JUNIOR,
                    position="Other",
                ),
            ),
        )
        resolution = await resolver.resolve(conflict)
        assert resolution.winning_agent_id == "sr_dev"
        assert resolution.outcome == ConflictResolutionOutcome.RESOLVED_BY_DEBATE
        assert len(judge.calls) == 1

    async def test_judge_receives_correct_agent_id(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        judge = FakeJudgeEvaluator(winner_id="sr_dev")
        resolver = DebateResolver(
            hierarchy=hierarchy,
            config=DebateConfig(judge="shared_manager"),
            judge_evaluator=judge,
        )
        conflict = make_conflict(
            positions=(
                make_position(agent_id="sr_dev", level=SeniorityLevel.SENIOR),
                make_position(
                    agent_id="jr_dev",
                    level=SeniorityLevel.JUNIOR,
                    position="Other",
                ),
            ),
        )
        await resolver.resolve(conflict)
        # shared_manager of sr_dev and jr_dev is backend_lead
        _, judge_id = judge.calls[0]
        assert judge_id == "backend_lead"


@pytest.mark.unit
class TestDebateResolverFallback:
    async def test_no_evaluator_falls_back_to_authority(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        resolver = DebateResolver(
            hierarchy=hierarchy,
            config=DebateConfig(),
            judge_evaluator=None,
        )
        conflict = make_conflict(
            positions=(
                make_position(
                    agent_id="sr_dev",
                    level=SeniorityLevel.SENIOR,
                    position="Approach A",
                ),
                make_position(
                    agent_id="jr_dev",
                    level=SeniorityLevel.JUNIOR,
                    position="Approach B",
                ),
            ),
        )
        resolution = await resolver.resolve(conflict)
        assert resolution.winning_agent_id == "sr_dev"
        assert "fallback" in resolution.reasoning.lower()


@pytest.mark.unit
class TestDebateJudgeSelection:
    async def test_ceo_judge(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        judge = FakeJudgeEvaluator(winner_id="sr_dev")
        resolver = DebateResolver(
            hierarchy=hierarchy,
            config=DebateConfig(judge="ceo"),
            judge_evaluator=judge,
        )
        conflict = make_conflict(
            positions=(
                make_position(agent_id="sr_dev", level=SeniorityLevel.SENIOR),
                make_position(
                    agent_id="jr_dev",
                    level=SeniorityLevel.JUNIOR,
                    position="Other",
                ),
            ),
        )
        await resolver.resolve(conflict)
        _, judge_id = judge.calls[0]
        # Root of sr_dev's hierarchy is cto
        assert judge_id == "cto"

    async def test_named_judge(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        judge = FakeJudgeEvaluator(winner_id="sr_dev")
        resolver = DebateResolver(
            hierarchy=hierarchy,
            config=DebateConfig(judge="external_reviewer"),
            judge_evaluator=judge,
        )
        conflict = make_conflict(
            positions=(
                make_position(agent_id="sr_dev", level=SeniorityLevel.SENIOR),
                make_position(
                    agent_id="jr_dev",
                    level=SeniorityLevel.JUNIOR,
                    position="Other",
                ),
            ),
        )
        await resolver.resolve(conflict)
        _, judge_id = judge.calls[0]
        assert judge_id == "external_reviewer"

    async def test_shared_manager_no_lcm_raises(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        resolver = DebateResolver(
            hierarchy=hierarchy,
            config=DebateConfig(judge="shared_manager"),
        )
        conflict = make_conflict(
            positions=(
                make_position(
                    agent_id="cto",
                    level=SeniorityLevel.C_SUITE,
                ),
                make_position(
                    agent_id="qa_head",
                    level=SeniorityLevel.C_SUITE,
                    position="Other",
                    department="qa",
                ),
            ),
        )
        with pytest.raises(ConflictHierarchyError):
            await resolver.resolve(conflict)


@pytest.mark.unit
class TestDebateResolverDissentRecord:
    async def test_dissent_record_includes_judge(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        judge = FakeJudgeEvaluator(winner_id="sr_dev")
        resolver = DebateResolver(
            hierarchy=hierarchy,
            config=DebateConfig(judge="shared_manager"),
            judge_evaluator=judge,
        )
        conflict = make_conflict(
            positions=(
                make_position(agent_id="sr_dev", level=SeniorityLevel.SENIOR),
                make_position(
                    agent_id="jr_dev",
                    level=SeniorityLevel.JUNIOR,
                    position="Other approach",
                ),
            ),
        )
        resolution = await resolver.resolve(conflict)
        records = resolver.build_dissent_records(conflict, resolution)
        assert len(records) == 1
        assert records[0].dissenting_agent_id == "jr_dev"
        assert records[0].strategy_used == ConflictResolutionStrategy.DEBATE
        assert ("judge", resolution.decided_by) in records[0].metadata


@pytest.mark.unit
class TestDebateResolverInvalidWinner:
    async def test_judge_returns_unknown_agent_raises(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        judge = FakeJudgeEvaluator(winner_id="nonexistent_agent")
        resolver = DebateResolver(
            hierarchy=hierarchy,
            config=DebateConfig(judge="shared_manager"),
            judge_evaluator=judge,
        )
        conflict = make_conflict(
            positions=(
                make_position(agent_id="sr_dev", level=SeniorityLevel.SENIOR),
                make_position(
                    agent_id="jr_dev",
                    level=SeniorityLevel.JUNIOR,
                    position="Other",
                ),
            ),
        )
        with pytest.raises(ConflictStrategyError, match="not found"):
            await resolver.resolve(conflict)


@pytest.mark.unit
class TestDebateResolverThreeParty:
    async def test_three_party_shared_manager_judge(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        """3-party conflict across teams uses iterative LCM for judge."""
        judge = FakeJudgeEvaluator(winner_id="sr_dev")
        resolver = DebateResolver(
            hierarchy=hierarchy,
            config=DebateConfig(judge="shared_manager"),
            judge_evaluator=judge,
        )
        # sr_dev, jr_dev are under backend_lead; ui_dev is under
        # frontend_lead.  LCM of agents from different teams = cto.
        conflict = make_conflict(
            positions=(
                make_position(agent_id="sr_dev", level=SeniorityLevel.SENIOR),
                make_position(
                    agent_id="jr_dev",
                    level=SeniorityLevel.JUNIOR,
                    position="Approach B",
                ),
                make_position(
                    agent_id="ui_dev",
                    level=SeniorityLevel.JUNIOR,
                    position="Approach C",
                    reasoning="Frontend perspective",
                ),
            ),
        )
        resolution = await resolver.resolve(conflict)
        assert resolution.winning_agent_id == "sr_dev"
        # Judge should be LCM of all three — cto (cross-team)
        _, judge_id = judge.calls[0]
        assert judge_id == "cto"

    async def test_three_party_produces_two_dissent_records(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        judge = FakeJudgeEvaluator(winner_id="sr_dev")
        resolver = DebateResolver(
            hierarchy=hierarchy,
            config=DebateConfig(judge="shared_manager"),
            judge_evaluator=judge,
        )
        conflict = make_conflict(
            positions=(
                make_position(agent_id="sr_dev", level=SeniorityLevel.SENIOR),
                make_position(
                    agent_id="jr_dev",
                    level=SeniorityLevel.JUNIOR,
                    position="Approach B",
                ),
                make_position(
                    agent_id="backend_lead",
                    level=SeniorityLevel.LEAD,
                    position="Approach C",
                    reasoning="Lead perspective",
                ),
            ),
        )
        resolution = await resolver.resolve(conflict)
        records = resolver.build_dissent_records(conflict, resolution)
        assert len(records) == 2
        dissenter_ids = {r.dissenting_agent_id for r in records}
        assert dissenter_ids == {"jr_dev", "backend_lead"}


@pytest.mark.unit
class TestDebateResolverCEORootAgent:
    async def test_ceo_judge_root_agent_no_ancestors(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        """When first position IS the hierarchy root (no ancestors), use it as judge."""
        judge = FakeJudgeEvaluator(winner_id="cto")
        resolver = DebateResolver(
            hierarchy=hierarchy,
            config=DebateConfig(judge="ceo"),
            judge_evaluator=judge,
        )
        # cto is the root of the Engineering hierarchy — has no ancestors
        conflict = make_conflict(
            positions=(
                make_position(
                    agent_id="cto",
                    level=SeniorityLevel.C_SUITE,
                    position="Approach A",
                ),
                make_position(
                    agent_id="backend_lead",
                    level=SeniorityLevel.LEAD,
                    position="Approach B",
                ),
            ),
        )
        await resolver.resolve(conflict)
        _, judge_id = judge.calls[0]
        assert judge_id == "cto"
