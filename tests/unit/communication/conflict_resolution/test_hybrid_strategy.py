"""Tests for the hybrid resolution strategy."""

import pytest

from synthorg.communication.conflict_resolution.config import HybridConfig
from synthorg.communication.conflict_resolution.human_strategy import (
    HumanEscalationResolver,
)
from synthorg.communication.conflict_resolution.hybrid_strategy import (
    HybridResolver,
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
from synthorg.core.enums import SeniorityLevel

from .conftest import make_conflict, make_position

pytestmark = pytest.mark.timeout(30)


class FakeReviewEvaluator:
    """Fake review evaluator for testing."""

    def __init__(self, winner_id: str, reasoning: str = "Review decided") -> None:
        self._winner_id = winner_id
        self._reasoning = reasoning

    async def evaluate(
        self,
        conflict: Conflict,
        judge_agent_id: str,
    ) -> JudgeDecision:
        return JudgeDecision(self._winner_id, self._reasoning)


@pytest.mark.unit
class TestHybridResolverAutoResolve:
    async def test_clear_winner_auto_resolves(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        evaluator = FakeReviewEvaluator(winner_id="sr_dev")
        resolver = HybridResolver(
            hierarchy=hierarchy,
            config=HybridConfig(),
            human_resolver=HumanEscalationResolver(),
            review_evaluator=evaluator,
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
        assert resolution.outcome == ConflictResolutionOutcome.RESOLVED_BY_HYBRID


@pytest.mark.unit
class TestHybridResolverAmbiguous:
    async def test_ambiguous_escalates_to_human(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        # Return a winner that is NOT a participant
        evaluator = FakeReviewEvaluator(winner_id="unknown_agent")
        resolver = HybridResolver(
            hierarchy=hierarchy,
            config=HybridConfig(escalate_on_ambiguity=True),
            human_resolver=HumanEscalationResolver(),
            review_evaluator=evaluator,
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
        assert resolution.outcome == ConflictResolutionOutcome.ESCALATED_TO_HUMAN

    async def test_ambiguous_falls_back_to_authority(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        evaluator = FakeReviewEvaluator(winner_id="unknown_agent")
        resolver = HybridResolver(
            hierarchy=hierarchy,
            config=HybridConfig(escalate_on_ambiguity=False),
            human_resolver=HumanEscalationResolver(),
            review_evaluator=evaluator,
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
        assert resolution.outcome == ConflictResolutionOutcome.RESOLVED_BY_HYBRID
        assert resolution.winning_agent_id == "sr_dev"


@pytest.mark.unit
class TestHybridResolverNoEvaluator:
    async def test_no_evaluator_falls_back_to_authority(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        resolver = HybridResolver(
            hierarchy=hierarchy,
            config=HybridConfig(),
            human_resolver=HumanEscalationResolver(),
            review_evaluator=None,
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
        assert resolution.outcome == ConflictResolutionOutcome.RESOLVED_BY_HYBRID
        assert resolution.winning_agent_id == "sr_dev"


@pytest.mark.unit
class TestHybridResolverDissentRecord:
    async def test_dissent_record_for_resolved(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        evaluator = FakeReviewEvaluator(winner_id="sr_dev")
        resolver = HybridResolver(
            hierarchy=hierarchy,
            config=HybridConfig(),
            human_resolver=HumanEscalationResolver(),
            review_evaluator=evaluator,
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
        assert records[0].dissenting_agent_id == "jr_dev"
        assert records[0].strategy_used == ConflictResolutionStrategy.HYBRID

    async def test_dissent_record_for_escalated(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        evaluator = FakeReviewEvaluator(winner_id="unknown")
        resolver = HybridResolver(
            hierarchy=hierarchy,
            config=HybridConfig(escalate_on_ambiguity=True),
            human_resolver=HumanEscalationResolver(),
            review_evaluator=evaluator,
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
        records = resolver.build_dissent_records(conflict, resolution)
        assert len(records) == 2
        for record in records:
            assert record.strategy_used == ConflictResolutionStrategy.HYBRID
            assert ("escalation_reason", "ambiguous_review") in record.metadata
