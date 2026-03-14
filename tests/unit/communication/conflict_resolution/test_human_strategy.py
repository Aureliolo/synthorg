"""Tests for the human escalation resolution strategy."""

import pytest

from synthorg.communication.conflict_resolution.human_strategy import (
    HumanEscalationResolver,
)
from synthorg.communication.conflict_resolution.models import (
    ConflictResolutionOutcome,
)
from synthorg.communication.enums import ConflictResolutionStrategy

from .conftest import make_conflict

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestHumanEscalationResolver:
    async def test_returns_escalated_outcome(self) -> None:
        resolver = HumanEscalationResolver()
        conflict = make_conflict()
        resolution = await resolver.resolve(conflict)
        assert resolution.outcome == ConflictResolutionOutcome.ESCALATED_TO_HUMAN

    async def test_no_winning_agent(self) -> None:
        resolver = HumanEscalationResolver()
        conflict = make_conflict()
        resolution = await resolver.resolve(conflict)
        assert resolution.winning_agent_id is None
        assert resolution.winning_position is None

    async def test_decided_by_human(self) -> None:
        resolver = HumanEscalationResolver()
        conflict = make_conflict()
        resolution = await resolver.resolve(conflict)
        assert resolution.decided_by == "human"

    async def test_reasoning_mentions_stub(self) -> None:
        resolver = HumanEscalationResolver()
        conflict = make_conflict()
        resolution = await resolver.resolve(conflict)
        assert "#37" in resolution.reasoning

    async def test_dissent_records_strategy(self) -> None:
        resolver = HumanEscalationResolver()
        conflict = make_conflict()
        resolution = await resolver.resolve(conflict)
        records = resolver.build_dissent_records(conflict, resolution)
        # Escalation produces one record per position
        assert len(records) == 2
        assert records[0].strategy_used == ConflictResolutionStrategy.HUMAN
        assert ("escalation_reason", "human_review_required") in records[0].metadata
        dissenter_ids = {r.dissenting_agent_id for r in records}
        assert dissenter_ids == {"agent-a", "agent-b"}
