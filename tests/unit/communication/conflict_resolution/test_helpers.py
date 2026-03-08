"""Tests for conflict resolution shared helpers."""

import pytest

from ai_company.communication.conflict_resolution._helpers import (
    find_losers,
    find_position,
    find_position_or_raise,
    pick_highest_seniority,
)
from ai_company.communication.errors import ConflictStrategyError
from ai_company.core.enums import SeniorityLevel

from .conftest import make_conflict, make_position, make_resolution

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestFindLosers:
    def test_returns_losing_positions(self) -> None:
        conflict = make_conflict()
        resolution = make_resolution(winning_agent_id="agent-a")
        losers = find_losers(conflict, resolution)
        assert len(losers) == 1
        assert losers[0].agent_id == "agent-b"

    def test_three_party_returns_two_losers(self) -> None:
        conflict = make_conflict(
            positions=(
                make_position(agent_id="a1", level=SeniorityLevel.SENIOR),
                make_position(
                    agent_id="a2",
                    level=SeniorityLevel.MID,
                    position="B",
                ),
                make_position(
                    agent_id="a3",
                    level=SeniorityLevel.JUNIOR,
                    position="C",
                ),
            ),
        )
        resolution = make_resolution(winning_agent_id="a1")
        losers = find_losers(conflict, resolution)
        assert len(losers) == 2
        loser_ids = {p.agent_id for p in losers}
        assert loser_ids == {"a2", "a3"}

    def test_finds_loser_with_distinct_agents(self) -> None:
        pos_a = make_position(agent_id="only-agent")
        pos_b = make_position(agent_id="other-agent", position="Other")
        conflict = make_conflict(positions=(pos_a, pos_b))
        resolution = make_resolution(winning_agent_id="only-agent")
        losers = find_losers(conflict, resolution)
        assert losers[0].agent_id == "other-agent"


@pytest.mark.unit
class TestFindPosition:
    def test_found(self) -> None:
        conflict = make_conflict()
        pos = find_position(conflict, "agent-a")
        assert pos is not None
        assert pos.agent_id == "agent-a"

    def test_not_found(self) -> None:
        conflict = make_conflict()
        pos = find_position(conflict, "nonexistent")
        assert pos is None


@pytest.mark.unit
class TestFindPositionOrRaise:
    def test_found(self) -> None:
        conflict = make_conflict()
        pos = find_position_or_raise(conflict, "agent-a")
        assert pos.agent_id == "agent-a"

    def test_not_found_raises(self) -> None:
        conflict = make_conflict()
        with pytest.raises(ConflictStrategyError, match="not found"):
            find_position_or_raise(conflict, "nonexistent")


@pytest.mark.unit
class TestPickHighestSeniority:
    def test_picks_senior_over_junior(self) -> None:
        conflict = make_conflict(
            positions=(
                make_position(agent_id="jr", level=SeniorityLevel.JUNIOR),
                make_position(
                    agent_id="sr",
                    level=SeniorityLevel.SENIOR,
                    position="Other",
                ),
            ),
        )
        best = pick_highest_seniority(conflict)
        assert best.agent_id == "sr"

    def test_picks_c_suite_over_lead(self) -> None:
        conflict = make_conflict(
            positions=(
                make_position(agent_id="lead", level=SeniorityLevel.LEAD),
                make_position(
                    agent_id="cto",
                    level=SeniorityLevel.C_SUITE,
                    position="Other",
                ),
            ),
        )
        best = pick_highest_seniority(conflict)
        assert best.agent_id == "cto"

    def test_equal_seniority_first_wins(self) -> None:
        conflict = make_conflict(
            positions=(
                make_position(agent_id="first", level=SeniorityLevel.SENIOR),
                make_position(
                    agent_id="second",
                    level=SeniorityLevel.SENIOR,
                    position="Other",
                ),
            ),
        )
        best = pick_highest_seniority(conflict)
        assert best.agent_id == "first"
