"""Position-papers meeting protocol (see Communication design page).

Each participant writes an independent position paper in parallel,
then a synthesizer combines all papers into decisions and action
items.  This is the cheapest protocol — O(n) tokens with no ordering
bias and no quadratic context growth.
"""

import asyncio
from datetime import UTC, datetime

from synthorg.communication.meeting._parsing import (
    parse_action_items,
    parse_decisions,
)
from synthorg.communication.meeting._prompts import build_agenda_prompt
from synthorg.communication.meeting._token_tracker import TokenTracker
from synthorg.communication.meeting.config import PositionPapersConfig  # noqa: TC001
from synthorg.communication.meeting.enums import (
    MeetingPhase,
    MeetingProtocolType,
)
from synthorg.communication.meeting.errors import (
    MeetingBudgetExhaustedError,
)
from synthorg.communication.meeting.models import (
    MeetingAgenda,
    MeetingContribution,
    MeetingMinutes,
)
from synthorg.communication.meeting.protocol import AgentCaller  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.meeting import (
    MEETING_AGENT_CALLED,
    MEETING_AGENT_RESPONDED,
    MEETING_CONTRIBUTION_RECORDED,
    MEETING_INTERNAL_ERROR,
    MEETING_PHASE_COMPLETED,
    MEETING_PHASE_STARTED,
    MEETING_SUMMARY_GENERATED,
    MEETING_SYNTHESIS_SKIPPED,
    MEETING_TOKENS_RECORDED,
)

logger = get_logger(__name__)

# Reserve 20% of budget for the synthesis phase.
_SYNTHESIS_RESERVE_FRACTION = 0.20


def _build_position_prompt(agenda_text: str, agent_id: str) -> str:
    """Build a position paper prompt for an agent."""
    return (
        f"{agenda_text}\n\n"
        f"{agent_id}, please write your position paper on the agenda "
        f"items above. Share your analysis, recommendations, and any "
        f"concerns you have."
    )


def _build_synthesis_prompt(
    agenda_text: str,
    papers: list[tuple[str, str]],
) -> str:
    """Build a synthesis prompt from all position papers."""
    parts = [agenda_text, "", "Position papers submitted:"]
    for agent_id, content in papers:
        parts.append(f"\n--- {agent_id} ---")
        parts.append(content)
    parts.append("")
    parts.append(
        "Please synthesize these position papers. Identify areas of "
        "agreement and conflicts, then produce your output using "
        "exactly these section headers:\n\n"
        "Decisions:\n"
        "1. <decision>\n\n"
        "Action Items:\n"
        "- <action item> (assigned to <agent_id>)"
    )
    return "\n".join(parts)


class PositionPapersProtocol:
    """Position-papers meeting protocol implementation.

    All participants write position papers in parallel, then a
    synthesizer combines them into decisions and action items.

    Args:
        config: Position papers protocol configuration.
    """

    __slots__ = ("_config",)

    def __init__(self, config: PositionPapersConfig) -> None:
        self._config = config

    def get_protocol_type(self) -> MeetingProtocolType:
        """Return the protocol type."""
        return MeetingProtocolType.POSITION_PAPERS

    async def run(  # noqa: PLR0913
        self,
        *,
        meeting_id: str,
        agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        agent_caller: AgentCaller,
        token_budget: int,
    ) -> MeetingMinutes:
        """Execute the position-papers meeting protocol.

        Args:
            meeting_id: Unique meeting identifier.
            agenda: The meeting agenda.
            leader_id: ID of the meeting leader.
            participant_ids: IDs of participating agents.
            agent_caller: Callback to invoke agents.
            token_budget: Maximum tokens for the meeting.

        Returns:
            Complete meeting minutes.
        """
        started_at = datetime.now(UTC)
        tracker = TokenTracker(budget=token_budget)
        agenda_text = build_agenda_prompt(agenda)

        synthesizer_id = (
            leader_id
            if self._config.synthesizer == "meeting_leader"
            else self._config.synthesizer
        )

        papers, paper_contributions = await self._collect_position_papers(
            meeting_id=meeting_id,
            agenda_text=agenda_text,
            participant_ids=participant_ids,
            agent_caller=agent_caller,
            tracker=tracker,
        )

        synthesis_contribution = await self._run_synthesis(
            meeting_id=meeting_id,
            agenda_text=agenda_text,
            papers=papers,
            synthesizer_id=synthesizer_id,
            turn_number=len(participant_ids),
            agent_caller=agent_caller,
            tracker=tracker,
        )

        contributions = (
            *paper_contributions,
            synthesis_contribution,
        )

        synthesis_text = synthesis_contribution.content
        decisions = parse_decisions(synthesis_text)
        raw_action_items = parse_action_items(synthesis_text)
        allowed_assignees = set(participant_ids) | {leader_id}
        action_items = tuple(
            item
            for item in raw_action_items
            if item.assignee_id is None or item.assignee_id in allowed_assignees
        )

        logger.debug(
            MEETING_TOKENS_RECORDED,
            meeting_id=meeting_id,
            input_tokens=tracker.input_tokens,
            output_tokens=tracker.output_tokens,
            total_tokens=tracker.used,
            budget=token_budget,
        )

        ended_at = datetime.now(UTC)
        return MeetingMinutes(
            meeting_id=meeting_id,
            protocol_type=MeetingProtocolType.POSITION_PAPERS,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agenda=agenda,
            contributions=tuple(contributions),
            summary=synthesis_text,
            decisions=decisions,
            action_items=action_items,
            total_input_tokens=tracker.input_tokens,
            total_output_tokens=tracker.output_tokens,
            started_at=started_at,
            ended_at=ended_at,
        )

    async def _collect_position_papers(
        self,
        *,
        meeting_id: str,
        agenda_text: str,
        participant_ids: tuple[str, ...],
        agent_caller: AgentCaller,
        tracker: TokenTracker,
    ) -> tuple[list[tuple[str, str]], list[MeetingContribution]]:
        """Collect position papers from all participants in parallel.

        Args:
            meeting_id: Unique meeting identifier.
            agenda_text: Formatted agenda prompt text.
            participant_ids: IDs of participating agents.
            agent_caller: Callback to invoke agents.
            tracker: Token budget tracker.

        Returns:
            Tuple of (papers, contributions) in deterministic order.
        """
        n = len(participant_ids)
        logger.info(
            MEETING_PHASE_STARTED,
            meeting_id=meeting_id,
            phase=MeetingPhase.POSITION_PAPER,
            participant_count=n,
        )

        # Pre-allocate slots for deterministic ordering
        results: list[tuple[str, str] | None] = [None] * n
        contrib_results: list[MeetingContribution | None] = [None] * n

        # Reserve 20% of budget for the synthesis phase, divide rest
        # evenly across parallel agents (mirrors RoundRobinProtocol).
        synthesis_reserve = int(tracker.remaining * _SYNTHESIS_RESERVE_FRACTION)
        paper_budget = tracker.remaining - synthesis_reserve
        tokens_per_agent = max(1, paper_budget // max(1, n))

        async def _collect_paper(
            participant_id: str,
            turn: int,
            budget_slice: int,
        ) -> None:
            prompt = _build_position_prompt(agenda_text, participant_id)
            max_tokens = min(
                self._config.max_tokens_per_position,
                budget_slice,
            )

            logger.debug(
                MEETING_AGENT_CALLED,
                meeting_id=meeting_id,
                agent_id=participant_id,
                phase=MeetingPhase.POSITION_PAPER,
            )

            response = await agent_caller(
                participant_id,
                prompt,
                max_tokens,
            )
            tracker.record(response.input_tokens, response.output_tokens)

            logger.debug(
                MEETING_AGENT_RESPONDED,
                meeting_id=meeting_id,
                agent_id=participant_id,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
            )

            now = datetime.now(UTC)
            contribution = MeetingContribution(
                agent_id=participant_id,
                content=response.content,
                phase=MeetingPhase.POSITION_PAPER,
                turn_number=turn,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                timestamp=now,
            )
            results[turn] = (participant_id, response.content)
            contrib_results[turn] = contribution

            logger.debug(
                MEETING_CONTRIBUTION_RECORDED,
                meeting_id=meeting_id,
                agent_id=participant_id,
            )

        async with asyncio.TaskGroup() as tg:
            for idx, pid in enumerate(participant_ids):
                tg.create_task(
                    _collect_paper(pid, idx, tokens_per_agent),
                )

        # All slots must be filled — TaskGroup propagates ExceptionGroup
        # on any task failure, so reaching this point means all succeeded.
        if not all(r is not None for r in results):
            msg = f"Expected {n} position papers but some slots are None"
            logger.error(
                MEETING_INTERNAL_ERROR,
                error=msg,
                meeting_id=meeting_id,
            )
            raise RuntimeError(msg)
        if not all(c is not None for c in contrib_results):
            msg = f"Expected {n} contributions but some slots are None"
            logger.error(
                MEETING_INTERNAL_ERROR,
                error=msg,
                meeting_id=meeting_id,
            )
            raise RuntimeError(msg)
        papers: list[tuple[str, str]] = list(results)  # type: ignore[arg-type]
        paper_contributions: list[MeetingContribution] = list(contrib_results)  # type: ignore[arg-type]

        logger.info(
            MEETING_PHASE_COMPLETED,
            meeting_id=meeting_id,
            phase=MeetingPhase.POSITION_PAPER,
            papers_collected=len(papers),
        )

        return papers, paper_contributions

    async def _run_synthesis(  # noqa: PLR0913
        self,
        *,
        meeting_id: str,
        agenda_text: str,
        papers: list[tuple[str, str]],
        synthesizer_id: str,
        turn_number: int,
        agent_caller: AgentCaller,
        tracker: TokenTracker,
    ) -> MeetingContribution:
        """Run the synthesis phase to combine position papers.

        Args:
            meeting_id: Unique meeting identifier.
            agenda_text: Formatted agenda prompt text.
            papers: Collected position papers as (agent_id, content).
            synthesizer_id: ID of the synthesizer agent.
            turn_number: Turn number for the synthesis contribution.
            agent_caller: Callback to invoke agents.
            tracker: Token budget tracker.

        Returns:
            The synthesis contribution.

        Raises:
            MeetingBudgetExhaustedError: If the token budget is exhausted
                before synthesis can begin.
        """
        logger.info(
            MEETING_PHASE_STARTED,
            meeting_id=meeting_id,
            phase=MeetingPhase.SYNTHESIS,
            synthesizer=synthesizer_id,
        )

        if tracker.is_exhausted:
            logger.warning(
                MEETING_SYNTHESIS_SKIPPED,
                meeting_id=meeting_id,
                synthesizer_id=synthesizer_id,
                reason="token_budget_exhausted",
            )
            msg = "Token budget exhausted before synthesis phase"
            raise MeetingBudgetExhaustedError(
                msg,
                context={
                    "meeting_id": meeting_id,
                    "synthesizer_id": synthesizer_id,
                    "budget": tracker.budget,
                    "used": tracker.used,
                },
            )

        synthesis_prompt = _build_synthesis_prompt(agenda_text, papers)
        synthesis_response = await agent_caller(
            synthesizer_id,
            synthesis_prompt,
            tracker.remaining,
        )
        tracker.record(
            synthesis_response.input_tokens,
            synthesis_response.output_tokens,
        )

        synthesis_contribution = MeetingContribution(
            agent_id=synthesizer_id,
            content=synthesis_response.content,
            phase=MeetingPhase.SYNTHESIS,
            turn_number=turn_number,
            input_tokens=synthesis_response.input_tokens,
            output_tokens=synthesis_response.output_tokens,
            timestamp=datetime.now(UTC),
        )

        logger.info(
            MEETING_SUMMARY_GENERATED,
            meeting_id=meeting_id,
            leader_id=synthesizer_id,
        )
        logger.info(
            MEETING_PHASE_COMPLETED,
            meeting_id=meeting_id,
            phase=MeetingPhase.SYNTHESIS,
        )

        return synthesis_contribution
