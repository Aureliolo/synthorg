"""Structured-phases meeting protocol (DESIGN_SPEC Section 5.7).

A phased approach: agenda broadcast, parallel input gathering,
optional conflict-driven discussion, and leader synthesis. The most
structured protocol, suitable for design reviews and decision
meetings.
"""

import asyncio
from datetime import UTC, datetime

from ai_company.communication.meeting._prompts import build_agenda_prompt
from ai_company.communication.meeting._token_tracker import TokenTracker
from ai_company.communication.meeting.config import (
    StructuredPhasesConfig,  # noqa: TC001
)
from ai_company.communication.meeting.enums import (
    MeetingPhase,
    MeetingProtocolType,
)
from ai_company.communication.meeting.errors import (
    MeetingBudgetExhaustedError,
)
from ai_company.communication.meeting.models import (
    MeetingAgenda,
    MeetingContribution,
    MeetingMinutes,
)
from ai_company.communication.meeting.protocol import AgentCaller  # noqa: TC001
from ai_company.observability import get_logger
from ai_company.observability.events.meeting import (
    MEETING_AGENT_CALLED,
    MEETING_AGENT_RESPONDED,
    MEETING_BUDGET_EXHAUSTED,
    MEETING_CONFLICT_DETECTED,
    MEETING_CONTRIBUTION_RECORDED,
    MEETING_PHASE_COMPLETED,
    MEETING_PHASE_STARTED,
    MEETING_SUMMARY_GENERATED,
    MEETING_SYNTHESIS_SKIPPED,
    MEETING_TOKENS_RECORDED,
)

logger = get_logger(__name__)


def _build_input_prompt(agenda_text: str, agent_id: str) -> str:
    """Build an input-gathering prompt for an agent."""
    return (
        f"{agenda_text}\n\n"
        f"{agent_id}, please provide your input on each agenda item. "
        f"Share your perspective, concerns, and recommendations."
    )


def _build_conflict_check_prompt(
    agenda_text: str,
    inputs: list[tuple[str, str]],
) -> str:
    """Build a prompt for the leader to check for conflicts."""
    parts = [agenda_text, "", "Participant inputs:"]
    for agent_id, content in inputs:
        parts.append(f"\n--- {agent_id} ---")
        parts.append(content)
    parts.append("")
    parts.append(
        "As the meeting leader, review the inputs above. "
        "Are there any conflicts or disagreements between participants? "
        "Reply with 'CONFLICTS: YES' or 'CONFLICTS: NO' on the first "
        "line, followed by your analysis."
    )
    return "\n".join(parts)


def _build_discussion_prompt(
    agenda_text: str,
    inputs: list[tuple[str, str]],
    conflict_analysis: str,
    agent_id: str,
) -> str:
    """Build a discussion prompt for a participant."""
    parts = [agenda_text, "", "Previous inputs:"]
    for aid, content in inputs:
        parts.append(f"\n--- {aid} ---")
        parts.append(content)
    parts.append(f"\nConflict analysis: {conflict_analysis}")
    parts.append("")
    parts.append(
        f"{agent_id}, please respond to the conflicts identified. "
        f"Provide your counter-arguments or revised position."
    )
    return "\n".join(parts)


def _build_synthesis_prompt(
    agenda_text: str,
    inputs: list[tuple[str, str]],
    discussion: list[tuple[str, str]] | None = None,
) -> str:
    """Build a synthesis prompt for the leader."""
    parts = [agenda_text, "", "Participant inputs:"]
    for agent_id, content in inputs:
        parts.append(f"\n--- {agent_id} ---")
        parts.append(content)
    if discussion:
        parts.append("\nDiscussion contributions:")
        for agent_id, content in discussion:
            parts.append(f"\n--- {agent_id} ---")
            parts.append(content)
    parts.append("")
    parts.append(
        "As the meeting leader, synthesize all inputs and discussion "
        "into final decisions and action items. List decisions as a "
        "numbered list and action items as a bulleted list with "
        "assignees."
    )
    return "\n".join(parts)


class StructuredPhasesProtocol:
    """Structured-phases meeting protocol implementation.

    Executes a meeting in distinct phases: agenda broadcast, parallel
    input gathering, optional discussion (if conflicts detected), and
    leader synthesis.

    Args:
        config: Structured phases protocol configuration.
    """

    __slots__ = ("_config",)

    def __init__(self, config: StructuredPhasesConfig) -> None:
        self._config = config

    def get_protocol_type(self) -> MeetingProtocolType:
        """Return the protocol type."""
        return MeetingProtocolType.STRUCTURED_PHASES

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
        """Execute the structured-phases meeting protocol.

        Args:
            meeting_id: Unique meeting identifier.
            agenda: The meeting agenda.
            leader_id: ID of the meeting leader.
            participant_ids: IDs of participating agents.
            agent_caller: Callback to invoke agents.
            token_budget: Maximum tokens for the meeting.

        Returns:
            Complete meeting minutes.

        Raises:
            MeetingBudgetExhaustedError: If the token budget is
                exhausted before synthesis can begin.
        """
        started_at = datetime.now(UTC)
        tracker = TokenTracker(budget=token_budget)
        contributions: list[MeetingContribution] = []
        agenda_text = build_agenda_prompt(agenda)
        turn_number = 0
        conflicts_detected = False

        # Phase 1: Agenda broadcast (data only, no LLM call)
        logger.info(
            MEETING_PHASE_STARTED,
            meeting_id=meeting_id,
            phase=MeetingPhase.AGENDA_BROADCAST,
        )
        logger.info(
            MEETING_PHASE_COMPLETED,
            meeting_id=meeting_id,
            phase=MeetingPhase.AGENDA_BROADCAST,
        )

        # Phase 2: Input gathering (parallel)
        inputs, input_contributions = await self._run_input_gathering(
            meeting_id=meeting_id,
            agenda_text=agenda_text,
            participant_ids=participant_ids,
            agent_caller=agent_caller,
            tracker=tracker,
        )
        contributions.extend(input_contributions)
        turn_number = len(participant_ids)

        # Phase 3: Discussion (conditional on conflicts)
        discussion: list[tuple[str, str]] = []

        if not tracker.is_exhausted:
            conflicts_detected, turn_number = await self._run_discussion(
                meeting_id=meeting_id,
                agenda_text=agenda_text,
                leader_id=leader_id,
                participant_ids=participant_ids,
                agent_caller=agent_caller,
                tracker=tracker,
                token_budget=token_budget,
                inputs=inputs,
                contributions=contributions,
                discussion=discussion,
                turn_number=turn_number,
            )

        # Phase 4: Synthesis
        summary = await self._run_synthesis(
            meeting_id=meeting_id,
            agenda_text=agenda_text,
            leader_id=leader_id,
            agent_caller=agent_caller,
            tracker=tracker,
            inputs=inputs,
            discussion=discussion,
            contributions=contributions,
            turn_number=turn_number,
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
            protocol_type=MeetingProtocolType.STRUCTURED_PHASES,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agenda=agenda,
            contributions=tuple(contributions),
            summary=summary,
            conflicts_detected=conflicts_detected,
            total_input_tokens=tracker.input_tokens,
            total_output_tokens=tracker.output_tokens,
            started_at=started_at,
            ended_at=ended_at,
        )

    async def _run_input_gathering(
        self,
        *,
        meeting_id: str,
        agenda_text: str,
        participant_ids: tuple[str, ...],
        agent_caller: AgentCaller,
        tracker: TokenTracker,
    ) -> tuple[list[tuple[str, str]], list[MeetingContribution]]:
        """Run parallel input gathering from all participants.

        Pre-divides the remaining token budget equally among
        participants and collects results into deterministically
        ordered lists (indexed by turn number).

        Returns:
            Tuple of (inputs, contributions) in participant order.
        """
        logger.info(
            MEETING_PHASE_STARTED,
            meeting_id=meeting_id,
            phase=MeetingPhase.INPUT_GATHERING,
            participant_count=len(participant_ids),
        )

        num_participants = len(participant_ids)
        tokens_per_agent = max(1, tracker.remaining // max(1, num_participants))

        # Pre-allocate result slots for deterministic ordering
        result_inputs: list[tuple[str, str] | None] = [None] * num_participants
        result_contributions: list[MeetingContribution | None] = [
            None
        ] * num_participants

        async def _collect_input(
            participant_id: str,
            turn: int,
            budget: int,
        ) -> None:
            prompt = _build_input_prompt(agenda_text, participant_id)

            logger.debug(
                MEETING_AGENT_CALLED,
                meeting_id=meeting_id,
                agent_id=participant_id,
                phase=MeetingPhase.INPUT_GATHERING,
            )

            response = await agent_caller(
                participant_id,
                prompt,
                budget,
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
                phase=MeetingPhase.INPUT_GATHERING,
                turn_number=turn,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                timestamp=now,
            )
            result_inputs[turn] = (participant_id, response.content)
            result_contributions[turn] = contribution

            logger.debug(
                MEETING_CONTRIBUTION_RECORDED,
                meeting_id=meeting_id,
                agent_id=participant_id,
            )

        async with asyncio.TaskGroup() as tg:
            for idx, pid in enumerate(participant_ids):
                tg.create_task(_collect_input(pid, idx, tokens_per_agent))

        # Build final lists in deterministic order (by turn number)
        inputs: list[tuple[str, str]] = [
            item for item in result_inputs if item is not None
        ]
        input_contributions: list[MeetingContribution] = [
            c for c in result_contributions if c is not None
        ]

        logger.info(
            MEETING_PHASE_COMPLETED,
            meeting_id=meeting_id,
            phase=MeetingPhase.INPUT_GATHERING,
            inputs_collected=len(inputs),
        )

        return inputs, input_contributions

    async def _run_discussion(  # noqa: PLR0913
        self,
        *,
        meeting_id: str,
        agenda_text: str,
        leader_id: str,
        participant_ids: tuple[str, ...],
        agent_caller: AgentCaller,
        tracker: TokenTracker,
        token_budget: int,
        inputs: list[tuple[str, str]],
        contributions: list[MeetingContribution],
        discussion: list[tuple[str, str]],
        turn_number: int,
    ) -> tuple[bool, int]:
        """Run conflict detection and optional discussion phase.

        Returns:
            Tuple of (conflicts_detected, updated_turn_number).
        """
        conflict_prompt = _build_conflict_check_prompt(
            agenda_text,
            inputs,
        )

        logger.debug(
            MEETING_AGENT_CALLED,
            meeting_id=meeting_id,
            agent_id=leader_id,
            phase=MeetingPhase.DISCUSSION,
        )

        conflict_response = await agent_caller(
            leader_id,
            conflict_prompt,
            tracker.remaining,
        )
        tracker.record(
            conflict_response.input_tokens,
            conflict_response.output_tokens,
        )

        conflict_contribution = MeetingContribution(
            agent_id=leader_id,
            content=conflict_response.content,
            phase=MeetingPhase.DISCUSSION,
            turn_number=turn_number,
            input_tokens=conflict_response.input_tokens,
            output_tokens=conflict_response.output_tokens,
            timestamp=datetime.now(UTC),
        )
        contributions.append(conflict_contribution)
        turn_number += 1

        conflicts_detected = "CONFLICTS: YES" in conflict_response.content.upper()

        logger.info(
            MEETING_CONFLICT_DETECTED,
            meeting_id=meeting_id,
            conflicts_found=conflicts_detected,
            raw_response=conflict_response.content,
        )

        should_discuss = conflicts_detected or (
            not self._config.skip_discussion_if_no_conflicts
        )

        if should_discuss and not tracker.is_exhausted:
            turn_number = await self._run_discussion_round(
                meeting_id=meeting_id,
                agenda_text=agenda_text,
                participant_ids=participant_ids,
                agent_caller=agent_caller,
                tracker=tracker,
                token_budget=token_budget,
                inputs=inputs,
                conflict_analysis=conflict_response.content,
                contributions=contributions,
                discussion=discussion,
                turn_number=turn_number,
            )

        return conflicts_detected, turn_number

    async def _run_discussion_round(  # noqa: PLR0913
        self,
        *,
        meeting_id: str,
        agenda_text: str,
        participant_ids: tuple[str, ...],
        agent_caller: AgentCaller,
        tracker: TokenTracker,
        token_budget: int,
        inputs: list[tuple[str, str]],
        conflict_analysis: str,
        contributions: list[MeetingContribution],
        discussion: list[tuple[str, str]],
        turn_number: int,
    ) -> int:
        """Run the discussion round with participants.

        Returns:
            Updated turn number.
        """
        logger.info(
            MEETING_PHASE_STARTED,
            meeting_id=meeting_id,
            phase=MeetingPhase.DISCUSSION,
        )

        discussion_budget = min(
            self._config.max_discussion_tokens,
            tracker.remaining,
        )
        tokens_per_agent = max(
            1,
            discussion_budget // max(1, len(participant_ids)),
        )

        for pid in participant_ids:
            if tracker.is_exhausted:
                logger.warning(
                    MEETING_BUDGET_EXHAUSTED,
                    meeting_id=meeting_id,
                    tokens_used=tracker.used,
                    token_budget=token_budget,
                )
                break

            disc_prompt = _build_discussion_prompt(
                agenda_text,
                inputs,
                conflict_analysis,
                pid,
            )

            logger.debug(
                MEETING_AGENT_CALLED,
                meeting_id=meeting_id,
                agent_id=pid,
                phase=MeetingPhase.DISCUSSION,
            )

            disc_response = await agent_caller(
                pid,
                disc_prompt,
                min(tokens_per_agent, tracker.remaining),
            )
            tracker.record(
                disc_response.input_tokens,
                disc_response.output_tokens,
            )

            disc_contribution = MeetingContribution(
                agent_id=pid,
                content=disc_response.content,
                phase=MeetingPhase.DISCUSSION,
                turn_number=turn_number,
                input_tokens=disc_response.input_tokens,
                output_tokens=disc_response.output_tokens,
                timestamp=datetime.now(UTC),
            )
            contributions.append(disc_contribution)
            discussion.append((pid, disc_response.content))

            logger.debug(
                MEETING_CONTRIBUTION_RECORDED,
                meeting_id=meeting_id,
                agent_id=pid,
            )
            turn_number += 1

        logger.info(
            MEETING_PHASE_COMPLETED,
            meeting_id=meeting_id,
            phase=MeetingPhase.DISCUSSION,
            discussion_contributions=len(discussion),
        )

        return turn_number

    async def _run_synthesis(  # noqa: PLR0913
        self,
        *,
        meeting_id: str,
        agenda_text: str,
        leader_id: str,
        agent_caller: AgentCaller,
        tracker: TokenTracker,
        inputs: list[tuple[str, str]],
        discussion: list[tuple[str, str]],
        contributions: list[MeetingContribution],
        turn_number: int,
    ) -> str:
        """Run the synthesis phase.

        Returns:
            Summary text.

        Raises:
            MeetingBudgetExhaustedError: If the token budget is
                exhausted before synthesis can begin.
        """
        if tracker.is_exhausted:
            logger.warning(
                MEETING_SYNTHESIS_SKIPPED,
                meeting_id=meeting_id,
                tokens_used=tracker.used,
                token_budget=tracker.budget,
            )
            msg = "Token budget exhausted before synthesis phase"
            raise MeetingBudgetExhaustedError(
                msg,
                context={
                    "meeting_id": meeting_id,
                    "tokens_used": tracker.used,
                    "token_budget": tracker.budget,
                },
            )

        logger.info(
            MEETING_PHASE_STARTED,
            meeting_id=meeting_id,
            phase=MeetingPhase.SYNTHESIS,
        )

        synthesis_prompt = _build_synthesis_prompt(
            agenda_text,
            inputs,
            discussion or None,
        )
        synthesis_response = await agent_caller(
            leader_id,
            synthesis_prompt,
            tracker.remaining,
        )
        tracker.record(
            synthesis_response.input_tokens,
            synthesis_response.output_tokens,
        )
        summary = synthesis_response.content

        synthesis_contribution = MeetingContribution(
            agent_id=leader_id,
            content=summary,
            phase=MeetingPhase.SYNTHESIS,
            turn_number=turn_number,
            input_tokens=synthesis_response.input_tokens,
            output_tokens=synthesis_response.output_tokens,
            timestamp=datetime.now(UTC),
        )
        contributions.append(synthesis_contribution)

        logger.info(
            MEETING_SUMMARY_GENERATED,
            meeting_id=meeting_id,
            leader_id=leader_id,
        )
        logger.info(
            MEETING_PHASE_COMPLETED,
            meeting_id=meeting_id,
            phase=MeetingPhase.SYNTHESIS,
        )

        return summary
