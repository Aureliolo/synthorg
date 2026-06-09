"""Structured-phases meeting protocol (see Communication design page).

A phased approach: agenda broadcast, parallel input gathering,
optional conflict-driven discussion, and leader synthesis. The most
structured protocol, suitable for design reviews and decision
meetings.

The four ``_run_*`` phase coroutines live in
``StructuredPhaseRunnersMixin`` (``_phase_runners.py``) and the prompt
builders in ``_structured_phases_prompts.py``; this module keeps the
thin coordinator (``__init__`` / ``get_protocol_type`` / ``run``).
"""

from collections.abc import Mapping
from datetime import UTC, datetime

from synthorg.communication.meeting._parsing import (
    parse_action_items,
    parse_decisions,
)
from synthorg.communication.meeting._phase_runners import (
    StructuredPhaseRunnersMixin,
)
from synthorg.communication.meeting._prompts import build_agenda_prompt
from synthorg.communication.meeting._token_tracker import TokenTracker
from synthorg.communication.meeting.config import (
    StructuredPhasesConfig,
)
from synthorg.communication.meeting.enums import (
    MeetingPhase,
    MeetingProtocolType,
)
from synthorg.communication.meeting.factory import build_conflict_detector
from synthorg.communication.meeting.models import (
    MeetingAgenda,
    MeetingContribution,
    MeetingMinutes,
)
from synthorg.communication.meeting.protocol import (
    AgentCaller,
    ConflictDetector,
)
from synthorg.observability import get_logger
from synthorg.observability.events.meeting import (
    MEETING_BUDGET_EXHAUSTED,
    MEETING_PHASE_COMPLETED,
    MEETING_PHASE_STARTED,
    MEETING_TOKENS_RECORDED,
)

logger = get_logger(__name__)


class StructuredPhasesProtocol(StructuredPhaseRunnersMixin):
    """Structured-phases meeting protocol implementation.

    Executes a meeting in distinct phases: agenda broadcast, parallel
    input gathering, optional discussion (if conflicts detected), and
    leader synthesis.

    Args:
        config: Structured phases protocol configuration.
        conflict_detector: Strategy for detecting conflicts in agent
            responses. When ``None``, ``build_conflict_detector(config)``
            dispatches by ``config.conflict_detector`` so the runtime
            choice tracks the configured enum.
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
            conflict_detector
            if conflict_detector is not None
            else build_conflict_detector(config)
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

        # Agenda broadcast (data only, no LLM call).
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

        # Input gathering (parallel).
        inputs, input_contributions = await self._run_input_gathering(
            meeting_id=meeting_id,
            agenda_text=agenda_text,
            participant_ids=participant_ids,
            agent_caller=agent_caller,
            tracker=tracker,
            lens_assignments=lens_assignments,
        )
        turn_number = len(participant_ids)

        # Discussion (conditional on conflicts).
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

        # Synthesis.
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
