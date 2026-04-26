"""Round-robin meeting protocol (see Communication design page).

Participants take sequential turns with full transcript context.
Each agent sees the entire conversation history when contributing,
producing rich contextual dialogue at the cost of quadratic token
growth.
"""

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
from synthorg.communication.meeting.config import RoundRobinConfig  # noqa: TC001
from synthorg.communication.meeting.enums import (
    MeetingPhase,
    MeetingProtocolType,
)
from synthorg.communication.meeting.errors import (
    MeetingBudgetExhaustedError,
)
from synthorg.communication.meeting.models import (
    ActionItem,
    MeetingAgenda,
    MeetingContribution,
    MeetingMinutes,
)
from synthorg.communication.meeting.protocol import AgentCaller  # noqa: TC001
from synthorg.engine.prompt_safety import (
    TAG_PEER_CONTRIBUTION,
    wrap_untrusted,
)
from synthorg.observability import get_logger
from synthorg.observability.events.meeting import (
    MEETING_AGENT_CALLED,
    MEETING_AGENT_RESPONDED,
    MEETING_BUDGET_EXHAUSTED,
    MEETING_CONTRIBUTION_RECORDED,
    MEETING_PHASE_COMPLETED,
    MEETING_PHASE_STARTED,
    MEETING_SUMMARY_GENERATED,
    MEETING_SUMMARY_SKIPPED,
    MEETING_TOKENS_RECORDED,
)

logger = get_logger(__name__)


def _format_transcript_entry(agent_id: str, content: str) -> str:
    """Render a single transcript entry with SEC-1 fencing.

    Centralises the security-critical formatting used by both the
    discussion-round transcript build and the leader-summary transcript
    rebuild so the two paths cannot drift out of sync (#1596).  The
    ``[agent_id]:`` prefix sits outside the fence as model-trusted
    attribution; the agent's free-form output goes inside the fence.
    """
    return f"[{agent_id}]:\n" + wrap_untrusted(
        TAG_PEER_CONTRIBUTION,
        content,
    )


def _build_turn_prompt(
    agenda_text: str,
    transcript: list[str],
    agent_id: str,
) -> str:
    """Build a turn prompt with agenda, transcript, and instruction."""
    parts = [agenda_text, ""]
    if transcript:
        parts.append("Transcript so far:")
        parts.extend(transcript)
        parts.append("")
    parts.append(
        f"It is your turn, {agent_id}. Share your thoughts on the agenda items."
    )
    return "\n".join(parts)


def _build_summary_prompt(
    agenda_text: str,
    transcript: list[str],
) -> str:
    """Build a summary prompt for the leader."""
    parts = [agenda_text, "", "Full transcript:"]
    parts.extend(transcript)
    parts.append("")
    parts.append(
        "Please summarize this meeting using exactly these section "
        "headers:\n\n"
        "Decisions:\n"
        "1. <decision>\n\n"
        "Action Items:\n"
        "- <action item> (assigned to <agent_id>)"
    )
    return "\n".join(parts)


class RoundRobinProtocol:
    """Round-robin meeting protocol implementation.

    Participants speak in order, each seeing the full transcript.
    The leader optionally produces a final summary.

    Args:
        config: Round-robin protocol configuration.
    """

    __slots__ = ("_config",)

    def __init__(self, config: RoundRobinConfig) -> None:
        self._config = config

    def get_protocol_type(self) -> MeetingProtocolType:
        """Return the protocol type."""
        return MeetingProtocolType.ROUND_ROBIN

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
        """Execute the round-robin meeting protocol.

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
        """
        started_at = datetime.now(UTC)
        tracker = TokenTracker(budget=token_budget)
        agenda_text = build_agenda_prompt(agenda)

        summary_reserve = (
            int(token_budget * self._config.summary_reserve_fraction)
            if self._config.leader_summarizes
            else 0
        )
        discussion_budget = token_budget - summary_reserve

        contributions = await self._run_discussion_rounds(
            meeting_id=meeting_id,
            participant_ids=participant_ids,
            agent_caller=agent_caller,
            tracker=tracker,
            discussion_budget=discussion_budget,
            agenda_text=agenda_text,
            lens_assignments=lens_assignments,
        )

        turn_number = len(contributions)
        # SEC-1: each transcript entry's content originates from another
        # agent's free-form output and may itself have been prompt-
        # injected upstream.  Wrap each contribution in its own
        # ``<peer-contribution>`` fence so a literal closing tag in the
        # content cannot inject into the leader's summary prompt (#1596).
        transcript = [
            _format_transcript_entry(c.agent_id, c.content) for c in contributions
        ]

        # Summary phase
        summary = ""
        decisions: tuple[str, ...] = ()
        action_items: tuple[ActionItem, ...] = ()
        all_contributions: tuple[MeetingContribution, ...] = tuple(contributions)
        if self._config.leader_summarizes and not tracker.is_exhausted:
            summary, summary_contribution = await self._run_summary(
                meeting_id=meeting_id,
                leader_id=leader_id,
                agent_caller=agent_caller,
                tracker=tracker,
                agenda_text=agenda_text,
                transcript=transcript,
                turn_number=turn_number,
            )
            all_contributions = (*contributions, summary_contribution)
            decisions = parse_decisions(summary)
            raw_action_items = parse_action_items(summary)
            allowed_assignees = set(participant_ids) | {leader_id}
            action_items = tuple(
                item
                for item in raw_action_items
                if item.assignee_id is None or item.assignee_id in allowed_assignees
            )
        elif self._config.leader_summarizes and tracker.is_exhausted:
            logger.warning(
                MEETING_SUMMARY_SKIPPED,
                meeting_id=meeting_id,
                reason="budget_exhausted",
                tokens_used=tracker.used,
                token_budget=token_budget,
            )
            msg = (
                f"Cannot generate meeting summary: token budget exhausted "
                f"({tracker.used}/{token_budget} tokens used)"
            )
            raise MeetingBudgetExhaustedError(
                msg,
                context={
                    "meeting_id": meeting_id,
                    "tokens_used": tracker.used,
                    "token_budget": token_budget,
                },
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
            protocol_type=MeetingProtocolType.ROUND_ROBIN,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agenda=agenda,
            contributions=all_contributions,
            summary=summary,
            decisions=decisions,
            action_items=action_items,
            total_input_tokens=tracker.input_tokens,
            total_output_tokens=tracker.output_tokens,
            started_at=started_at,
            ended_at=ended_at,
        )

    async def _run_discussion_rounds(  # noqa: PLR0913
        self,
        *,
        meeting_id: str,
        participant_ids: tuple[str, ...],
        agent_caller: AgentCaller,
        tracker: TokenTracker,
        discussion_budget: int,
        agenda_text: str,
        lens_assignments: Mapping[str, str] | None = None,
    ) -> list[MeetingContribution]:
        """Execute all discussion rounds and return contributions.

        Args:
            meeting_id: Unique meeting identifier.
            participant_ids: IDs of participating agents.
            agent_caller: Callback to invoke agents.
            tracker: Token budget tracker.
            discussion_budget: Token budget for discussion (excluding summary).
            agenda_text: Formatted agenda text.
            lens_assignments: Optional lens assignments for participants.

        Returns:
            List of contributions from the discussion phase.
        """
        contributions: list[MeetingContribution] = []
        transcript: list[str] = []
        turn_number = 0
        budget_exhausted = False

        logger.info(
            MEETING_PHASE_STARTED,
            meeting_id=meeting_id,
            phase=MeetingPhase.ROUND_ROBIN_TURN,
            participant_count=len(participant_ids),
        )

        for _round_idx in range(self._config.max_turns_per_agent):
            if budget_exhausted:
                break
            for participant_id in participant_ids:
                if turn_number >= self._config.max_total_turns:
                    break
                tokens_available = discussion_budget - tracker.used
                if tokens_available <= 0:
                    budget_exhausted = True
                    logger.warning(
                        MEETING_BUDGET_EXHAUSTED,
                        meeting_id=meeting_id,
                        tokens_used=tracker.used,
                        token_budget=tracker.budget,
                    )
                    break

                contribution = await self._execute_turn(
                    meeting_id=meeting_id,
                    participant_id=participant_id,
                    agent_caller=agent_caller,
                    tracker=tracker,
                    agenda_text=agenda_text,
                    transcript=transcript,
                    turn_number=turn_number,
                    tokens_available=tokens_available,
                    lens_assignments=lens_assignments,
                )
                contributions.append(contribution)
                # SEC-1: each peer turn is wrapped in its own
                # ``<peer-contribution>`` fence so a compromised
                # contribution cannot escape and inject into downstream
                # turns or the leader's summary prompt (#1596).
                transcript.append(
                    _format_transcript_entry(
                        participant_id,
                        contribution.content,
                    )
                )
                turn_number += 1

            if turn_number >= self._config.max_total_turns:
                break

        logger.info(
            MEETING_PHASE_COMPLETED,
            meeting_id=meeting_id,
            phase=MeetingPhase.ROUND_ROBIN_TURN,
            total_turns=turn_number,
        )

        # Sort by turn_number for deterministic ordering.
        # For round-robin this is already sequential, but we sort
        # explicitly to make the contract clear and future-proof.
        contributions.sort(key=lambda c: c.turn_number)

        return contributions

    async def _execute_turn(  # noqa: PLR0913
        self,
        *,
        meeting_id: str,
        participant_id: str,
        agent_caller: AgentCaller,
        tracker: TokenTracker,
        agenda_text: str,
        transcript: list[str],
        turn_number: int,
        tokens_available: int,
        lens_assignments: Mapping[str, str] | None = None,
    ) -> MeetingContribution:
        """Execute a single agent turn and return the contribution.

        Args:
            meeting_id: Unique meeting identifier.
            participant_id: ID of the agent taking this turn.
            agent_caller: Callback to invoke agents.
            tracker: Token budget tracker.
            agenda_text: Formatted agenda text.
            transcript: Current transcript lines.
            turn_number: Current turn number.
            tokens_available: Tokens available for this turn.
            lens_assignments: Optional lens assignments for participants.

        Returns:
            The contribution from this turn.
        """
        prompt = _build_turn_prompt(agenda_text, transcript, participant_id)
        prompt = inject_lens_perspective(prompt, participant_id, lens_assignments)

        logger.debug(
            MEETING_AGENT_CALLED,
            meeting_id=meeting_id,
            agent_id=participant_id,
            turn_number=turn_number,
        )

        response = await agent_caller(
            participant_id,
            prompt,
            tokens_available,
        )
        tracker.record(response.input_tokens, response.output_tokens)

        logger.debug(
            MEETING_AGENT_RESPONDED,
            meeting_id=meeting_id,
            agent_id=participant_id,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )

        contribution = MeetingContribution(
            agent_id=participant_id,
            content=response.content,
            phase=MeetingPhase.ROUND_ROBIN_TURN,
            turn_number=turn_number,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            timestamp=datetime.now(UTC),
        )

        logger.debug(
            MEETING_CONTRIBUTION_RECORDED,
            meeting_id=meeting_id,
            agent_id=participant_id,
            turn_number=turn_number,
        )

        return contribution

    async def _run_summary(  # noqa: PLR0913
        self,
        *,
        meeting_id: str,
        leader_id: str,
        agent_caller: AgentCaller,
        tracker: TokenTracker,
        agenda_text: str,
        transcript: list[str],
        turn_number: int,
    ) -> tuple[str, MeetingContribution]:
        """Execute the summary phase and return summary text and contribution.

        Args:
            meeting_id: Unique meeting identifier.
            leader_id: ID of the meeting leader.
            agent_caller: Callback to invoke agents.
            tracker: Token budget tracker.
            agenda_text: Formatted agenda text.
            transcript: Full transcript lines.
            turn_number: Next turn number for the summary contribution.

        Returns:
            Tuple of (summary text, summary contribution).
        """
        logger.info(
            MEETING_PHASE_STARTED,
            meeting_id=meeting_id,
            phase=MeetingPhase.SUMMARY,
        )

        summary_prompt = _build_summary_prompt(agenda_text, transcript)
        summary_response = await agent_caller(
            leader_id,
            summary_prompt,
            tracker.remaining,
        )
        tracker.record(
            summary_response.input_tokens,
            summary_response.output_tokens,
        )
        summary = summary_response.content

        summary_contribution = MeetingContribution(
            agent_id=leader_id,
            content=summary,
            phase=MeetingPhase.SUMMARY,
            turn_number=turn_number,
            input_tokens=summary_response.input_tokens,
            output_tokens=summary_response.output_tokens,
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
            phase=MeetingPhase.SUMMARY,
        )

        return summary, summary_contribution
