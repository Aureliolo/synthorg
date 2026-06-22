"""Tests for the structured-phases premortem / consensus dispatch hooks.

Proves the two strategy dispatch points fire: the premortem hook folds
its section into the synthesis summary, and the consensus-velocity hook
forces the discussion round even when the leader's conflict check finds
none and the protocol is configured to skip discussion otherwise.
"""

import pytest

from synthorg.communication.meeting.config import StructuredPhasesConfig
from synthorg.communication.meeting.enums import MeetingPhase
from synthorg.communication.meeting.models import MeetingAgenda
from synthorg.communication.meeting.protocol import AgentCaller
from synthorg.communication.meeting.structured_phases import (
    StructuredPhasesProtocol,
)
from tests.unit.communication.meeting.conftest import make_mock_agent_caller

_NO_CONFLICT_CONFIG = StructuredPhasesConfig(skip_discussion_if_no_conflicts=True)


def _no_conflict_caller() -> AgentCaller:
    # The leader's conflict-check response must contain no conflict
    # markers so discussion is skipped unless the consensus hook forces
    # it; participant inputs are deliberately near-identical so a real
    # detector would also flag premature consensus.
    return make_mock_agent_caller(default_content="We all agree, no conflicts here.")


@pytest.mark.unit
class TestPremortemDispatch:
    async def test_premortem_section_folded_into_summary(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        meeting_id: str,
    ) -> None:
        async def _premortem_hook(
            *,
            synthesis_text: str,
            participant_ids: tuple[str, ...],
            agent_caller: AgentCaller,
            token_budget: int,
            context_id: str,
        ) -> str:
            del synthesis_text, participant_ids, agent_caller
            del token_budget, context_id
            return "### Failure modes\n- the rollout stalls"

        protocol = StructuredPhasesProtocol(
            config=StructuredPhasesConfig(),
            premortem_hook=_premortem_hook,
        )

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agent_caller=make_mock_agent_caller(),
            token_budget=10000,
        )

        assert "## Premortem Analysis" in minutes.summary
        assert "the rollout stalls" in minutes.summary

    async def test_empty_premortem_leaves_summary_unchanged(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        meeting_id: str,
    ) -> None:
        async def _empty_hook(
            *,
            synthesis_text: str,
            participant_ids: tuple[str, ...],
            agent_caller: AgentCaller,
            token_budget: int,
            context_id: str,
        ) -> str:
            del synthesis_text, participant_ids, agent_caller
            del token_budget, context_id
            return ""

        protocol = StructuredPhasesProtocol(
            config=StructuredPhasesConfig(),
            premortem_hook=_empty_hook,
        )

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agent_caller=make_mock_agent_caller(),
            token_budget=10000,
        )

        assert "## Premortem Analysis" not in minutes.summary

    async def test_no_hook_means_no_premortem_section(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        meeting_id: str,
    ) -> None:
        protocol = StructuredPhasesProtocol(config=StructuredPhasesConfig())

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agent_caller=make_mock_agent_caller(),
            token_budget=10000,
        )

        assert "## Premortem Analysis" not in minutes.summary


@pytest.mark.unit
class TestConsensusVelocityDispatch:
    async def test_premature_consensus_forces_discussion(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        meeting_id: str,
    ) -> None:
        seen: list[tuple[str, ...]] = []

        def _consensus_hook(positions: tuple[str, ...]) -> bool:
            seen.append(positions)
            return True

        protocol = StructuredPhasesProtocol(
            config=_NO_CONFLICT_CONFIG,
            consensus_hook=_consensus_hook,
        )

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agent_caller=_no_conflict_caller(),
            token_budget=10000,
        )

        # The hook saw the gathered input positions.
        assert seen
        assert len(seen[0]) == len(participant_ids)
        # Discussion ran despite no conflicts + skip-if-no-conflicts.
        discussion = [
            c for c in minutes.contributions if c.phase == MeetingPhase.DISCUSSION
        ]
        participant_discussion = [
            c for c in discussion if c.agent_id in set(participant_ids)
        ]
        assert participant_discussion

    async def test_no_premature_consensus_skips_discussion(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        meeting_id: str,
    ) -> None:
        def _never(positions: tuple[str, ...]) -> bool:
            del positions
            return False

        protocol = StructuredPhasesProtocol(
            config=_NO_CONFLICT_CONFIG,
            consensus_hook=_never,
        )

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agent_caller=_no_conflict_caller(),
            token_budget=10000,
        )

        participant_discussion = [
            c
            for c in minutes.contributions
            if c.phase == MeetingPhase.DISCUSSION and c.agent_id in set(participant_ids)
        ]
        assert not participant_discussion
