"""Structured-phases meeting protocol (see Communication design page).

A phased approach: agenda broadcast, parallel input gathering,
optional conflict-driven discussion, and leader synthesis. The most
structured protocol, suitable for design reviews and decision
meetings.
"""

import asyncio
from collections.abc import Mapping  # noqa: TC003
from datetime import UTC, datetime

from synthorg.communication.meeting._parsing import (
    parse_action_items,
    parse_decisions,
)
from synthorg.communication.meeting._prompts import (
    build_agenda_prompt,
    inject_lens_perspective,
)
from synthorg.communication.meeting._token_tracker import TokenTracker
from synthorg.communication.meeting.config import (
    StructuredPhasesConfig,  # noqa: TC001
)
from synthorg.communication.meeting.conflict_detection import (
    KeywordConflictDetector,
)
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
from synthorg.communication.meeting.protocol import (
    AgentCaller,  # noqa: TC001
    ConflictDetector,  # noqa: TC001
)
from synthorg.engine.prompt_safety import (
    TAG_PEER_CONTRIBUTION,
    wrap_untrusted,
)
from synthorg.observability import get_logger
from synthorg.observability.events.meeting import (
    MEETING_AGENT_CALLED,
    MEETING_AGENT_RESPONDED,
    MEETING_BUDGET_EXHAUSTED,
    MEETING_CONFLICT_DETECTED,
    MEETING_CONTRIBUTION_RECORDED,
    MEETING_INTERNAL_ERROR,
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
    """Build a prompt for the leader to check for conflicts.

    Each participant input is wrapped in its own ``<peer-contribution>``
    fence (SEC-1 / #1596) so a literal closing tag in any input cannot
    inject instructions into the leader's reasoning.
    """
    parts = [agenda_text, "", "Participant inputs:"]
    for agent_id, content in inputs:
        parts.append(f"\n--- {agent_id} ---")
        parts.append(wrap_untrusted(TAG_PEER_CONTRIBUTION, content))
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
    """Build a discussion prompt for a participant.

    Inputs and the conflict analysis (the leader's prior turn output,
    which may itself contain prompt-injected content from upstream
    inputs) are wrapped in ``<peer-contribution>`` fences so a
    compromised upstream turn cannot escape and inject into this
    participant's reasoning (#1596).
    """
    parts = [agenda_text, "", "Previous inputs:"]
    for aid, content in inputs:
        parts.append(f"\n--- {aid} ---")
        parts.append(wrap_untrusted(TAG_PEER_CONTRIBUTION, content))
    parts.append(
        "\nConflict analysis:\n"
        + wrap_untrusted(TAG_PEER_CONTRIBUTION, conflict_analysis),
    )
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
    """Build a synthesis prompt for the leader.

    Each input and discussion contribution is wrapped in its own
    ``<peer-contribution>`` fence so a compromised participant cannot
    inject into the synthesis decision (#1596).
    """
    parts = [agenda_text, "", "Participant inputs:"]
    for agent_id, content in inputs:
        parts.append(f"\n--- {agent_id} ---")
        parts.append(wrap_untrusted(TAG_PEER_CONTRIBUTION, content))
    if discussion:
        parts.append("\nDiscussion contributions:")
        for agent_id, content in discussion:
            parts.append(f"\n--- {agent_id} ---")
            parts.append(wrap_untrusted(TAG_PEER_CONTRIBUTION, content))
    parts.append("")
    parts.append(
        "As the meeting leader, synthesize all inputs and discussion "
        "into your output using exactly these section headers:\n\n"
        "Decisions:\n"
        "1. <decision>\n\n"
        "Action Items:\n"
        "- <action item> (assigned to <agent_id>)"
    )
    return "\n".join(parts)


class StructuredPhasesProtocol:
    """Structured-phases meeting protocol implementation.

    Executes a meeting in distinct phases: agenda broadcast, parallel
    input gathering, optional discussion (if conflicts detected), and
    leader synthesis.

    Args:
        config: Structured phases protocol configuration.
        conflict_detector: Strategy for detecting conflicts in agent
            responses.  Defaults to ``KeywordConflictDetector``.
    """

    __slots__ = ("_config", "_conflict_detector")

    def __init__(
        self,
        config: StructuredPhasesConfig,
        *,
        conflict_detector: ConflictDetector | None = None,
    ) -> None:
        self._config = config
        self._conflict_detector: ConflictDetector = (
            conflict_detector or KeywordConflictDetector()
        )

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
        lens_assignments: Mapping[str, str] | None = None,
    ) -> MeetingMinutes:
        """Execute the structured-phases meeting protocol.

        Each sub-method returns its own contributions rather than
        mutating a shared list, keeping data flow explicit.

        Args:
            meeting_id: Unique meeting identifier.
            agenda: The meeting agenda.
            leader_id: ID of the meeting leader.
            participant_ids: IDs of participating agents.
            agent_caller: Callback to invoke agents.
            token_budget: Maximum tokens for the meeting.
            lens_assignments: Optional lens assignments per participant.

        Returns:
            Complete meeting minutes.

        Raises:
            MeetingBudgetExhaustedError: If the token budget is
                exhausted before synthesis can begin.
        """
        started_at = datetime.now(UTC)
        tracker = TokenTracker(budget=token_budget)
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
            lens_assignments=lens_assignments,
        )
        turn_number = len(participant_ids)

        # Phase 3: Discussion (conditional on conflicts)
        discussion_contributions: list[MeetingContribution] = []
        discussion_pairs: list[tuple[str, str]] = []

        if not tracker.is_exhausted:
            (
                conflicts_detected,
                turn_number,
                discussion_contributions,
                discussion_pairs,
            ) = await self._run_discussion(
                meeting_id=meeting_id,
                agenda_text=agenda_text,
                leader_id=leader_id,
                participant_ids=participant_ids,
                agent_caller=agent_caller,
                tracker=tracker,
                token_budget=token_budget,
                inputs=inputs,
                turn_number=turn_number,
                lens_assignments=lens_assignments,
            )
        else:
            logger.warning(
                MEETING_BUDGET_EXHAUSTED,
                meeting_id=meeting_id,
                tokens_used=tracker.used,
                token_budget=token_budget,
                skipped_phase=MeetingPhase.DISCUSSION,
            )

        # Phase 4: Synthesis
        summary, synthesis_contribution = await self._run_synthesis(
            meeting_id=meeting_id,
            agenda_text=agenda_text,
            leader_id=leader_id,
            agent_caller=agent_caller,
            tracker=tracker,
            inputs=inputs,
            discussion=discussion_pairs,
            turn_number=turn_number,
        )

        contributions = (
            *input_contributions,
            *discussion_contributions,
            synthesis_contribution,
        )

        decisions = parse_decisions(summary)
        raw_action_items = parse_action_items(summary)
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
            protocol_type=MeetingProtocolType.STRUCTURED_PHASES,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agenda=agenda,
            contributions=contributions,
            summary=summary,
            decisions=decisions,
            action_items=action_items,
            conflicts_detected=conflicts_detected,
            total_input_tokens=tracker.input_tokens,
            total_output_tokens=tracker.output_tokens,
            started_at=started_at,
            ended_at=ended_at,
        )

    async def _run_input_gathering(  # noqa: PLR0913
        self,
        *,
        meeting_id: str,
        agenda_text: str,
        participant_ids: tuple[str, ...],
        agent_caller: AgentCaller,
        tracker: TokenTracker,
        lens_assignments: Mapping[str, str] | None = None,
    ) -> tuple[list[tuple[str, str]], list[MeetingContribution]]:
        """Run parallel input gathering from all participants.

        Pre-divides the remaining token budget equally among
        participants and collects results into deterministically
        ordered lists (indexed by turn number).

        Args:
            meeting_id: Unique meeting identifier.
            agenda_text: Formatted agenda prompt text.
            participant_ids: IDs of participating agents.
            agent_caller: Callback to invoke agents.
            tracker: Token budget tracker.
            lens_assignments: Optional lens assignments for participants.

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
        # Reserve budget for conflict check, discussion, and synthesis
        # phases that follow input gathering (mirrors RoundRobinProtocol).
        later_reserve = int(tracker.remaining * self._config.synthesis_reserve_fraction)
        input_budget = tracker.remaining - later_reserve
        tokens_per_agent = max(1, input_budget // max(1, num_participants))

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
            prompt = inject_lens_perspective(
                prompt,
                participant_id,
                lens_assignments,
            )

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

        # All slots must be filled -- TaskGroup propagates ExceptionGroup
        # on any task failure, so reaching this point means all succeeded.
        if not all(r is not None for r in result_inputs):
            msg = f"Expected {num_participants} inputs but some slots are None"
            logger.error(
                MEETING_INTERNAL_ERROR,
                error=msg,
                meeting_id=meeting_id,
            )
            raise RuntimeError(msg)
        if not all(c is not None for c in result_contributions):
            msg = f"Expected {num_participants} contributions but some slots are None"
            logger.error(
                MEETING_INTERNAL_ERROR,
                error=msg,
                meeting_id=meeting_id,
            )
            raise RuntimeError(msg)
        inputs: list[tuple[str, str]] = list(result_inputs)  # type: ignore[arg-type]
        input_contributions: list[MeetingContribution] = list(
            result_contributions,  # type: ignore[arg-type]
        )

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
        turn_number: int,
        lens_assignments: Mapping[str, str] | None = None,
    ) -> tuple[
        bool,
        int,
        list[MeetingContribution],
        list[tuple[str, str]],
    ]:
        """Run conflict detection and optional discussion phase.

        Returns:
            Tuple of (conflicts_detected, updated_turn_number,
            contributions, discussion_pairs).
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
        discussion_contributions = [conflict_contribution]
        turn_number += 1

        conflicts_detected = self._conflict_detector.detect(
            conflict_response.content,
        )

        logger.info(
            MEETING_CONFLICT_DETECTED,
            meeting_id=meeting_id,
            conflicts_found=conflicts_detected,
        )

        should_discuss = conflicts_detected or (
            not self._config.skip_discussion_if_no_conflicts
        )

        discussion_pairs: list[tuple[str, str]] = []

        if should_discuss and not tracker.is_exhausted:
            (
                turn_number,
                round_contributions,
                round_pairs,
            ) = await self._run_discussion_round(
                meeting_id=meeting_id,
                agenda_text=agenda_text,
                participant_ids=participant_ids,
                agent_caller=agent_caller,
                tracker=tracker,
                token_budget=token_budget,
                inputs=inputs,
                conflict_analysis=conflict_response.content,
                turn_number=turn_number,
                lens_assignments=lens_assignments,
            )
            discussion_contributions.extend(round_contributions)
            discussion_pairs = round_pairs

        return (
            conflicts_detected,
            turn_number,
            discussion_contributions,
            discussion_pairs,
        )

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
        turn_number: int,
        lens_assignments: Mapping[str, str] | None = None,
    ) -> tuple[int, list[MeetingContribution], list[tuple[str, str]]]:
        """Run the discussion round with participants.

        Returns:
            Tuple of (updated_turn_number, contributions,
            discussion_pairs).
        """
        logger.info(
            MEETING_PHASE_STARTED,
            meeting_id=meeting_id,
            phase=MeetingPhase.DISCUSSION,
        )

        # Reserve tokens for the synthesis phase that follows
        # discussion so that discussion cannot exhaust the budget.
        synthesis_reserve = int(
            tracker.remaining * self._config.synthesis_reserve_fraction
        )
        available_for_discussion = max(0, tracker.remaining - synthesis_reserve)
        discussion_budget = min(
            self._config.max_discussion_tokens,
            available_for_discussion,
        )
        tokens_per_agent = max(
            1,
            discussion_budget // max(1, len(participant_ids)),
        )

        round_contributions: list[MeetingContribution] = []
        round_discussion: list[tuple[str, str]] = []
        discussion_used = 0

        for pid in participant_ids:
            if tracker.is_exhausted or discussion_used >= discussion_budget:
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
            disc_prompt = inject_lens_perspective(
                disc_prompt,
                pid,
                lens_assignments,
            )

            logger.debug(
                MEETING_AGENT_CALLED,
                meeting_id=meeting_id,
                agent_id=pid,
                phase=MeetingPhase.DISCUSSION,
            )

            remaining_discussion = discussion_budget - discussion_used
            disc_response = await agent_caller(
                pid,
                disc_prompt,
                min(tokens_per_agent, remaining_discussion),
            )
            tracker.record(
                disc_response.input_tokens,
                disc_response.output_tokens,
            )
            discussion_used += disc_response.input_tokens + disc_response.output_tokens

            disc_contribution = MeetingContribution(
                agent_id=pid,
                content=disc_response.content,
                phase=MeetingPhase.DISCUSSION,
                turn_number=turn_number,
                input_tokens=disc_response.input_tokens,
                output_tokens=disc_response.output_tokens,
                timestamp=datetime.now(UTC),
            )
            round_contributions.append(disc_contribution)
            round_discussion.append((pid, disc_response.content))

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
            discussion_contributions=len(round_discussion),
        )

        return turn_number, round_contributions, round_discussion

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
        turn_number: int,
    ) -> tuple[str, MeetingContribution]:
        """Run the synthesis phase.

        Returns:
            Tuple of (summary_text, synthesis_contribution).

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

        return summary, synthesis_contribution
