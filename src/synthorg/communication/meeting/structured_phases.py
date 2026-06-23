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
from typing import Final, Protocol, runtime_checkable

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
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meeting import (
    MEETING_BUDGET_EXHAUSTED,
    MEETING_CONSENSUS_VELOCITY_FAILED,
    MEETING_CONSENSUS_VELOCITY_FORCED,
    MEETING_PHASE_COMPLETED,
    MEETING_PHASE_STARTED,
    MEETING_PREMORTEM_APPENDED,
    MEETING_PREMORTEM_FAILED,
    MEETING_TOKENS_RECORDED,
)

logger = get_logger(__name__)

#: Heading the premortem section is folded under when appended to the
#: synthesis summary so action-item / decision parsing still scans it.
_PREMORTEM_SECTION_HEADING: Final[str] = "## Premortem Analysis"


@runtime_checkable
class ConsensusVelocityHook(Protocol):
    """Premature-consensus check over the gathered input positions.

    Structurally typed in the meeting package so the strategy
    subsystem (``engine.strategy.consensus``) can be injected without
    ``communication.meeting`` importing ``engine.strategy`` (which would
    close an import cycle: the strategy package already depends on the
    meeting package). The api layer binds the concrete
    ``ConsensusVelocityDetector`` + its config behind this signature.
    """

    def __call__(self, positions: tuple[str, ...]) -> bool:
        """Return whether the positions show premature consensus."""
        ...


@runtime_checkable
class PremortemHook(Protocol):
    """Premortem analysis over the synthesis summary.

    Returns the rendered premortem section text (empty when no failure
    modes / assumptions surfaced). Structurally typed for the same
    cycle-avoidance reason as :class:`ConsensusVelocityHook`.
    """

    async def __call__(
        self,
        *,
        synthesis_text: str,
        participant_ids: tuple[str, ...],
        agent_caller: AgentCaller,
        token_budget: int,
        context_id: str,
    ) -> str:
        """Run premortem and return the rendered section (or empty)."""
        ...


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
        consensus_hook: Optional premature-consensus check run over the
            gathered input positions. When it fires, the discussion
            (devil's-advocate) round is forced even if the leader's
            conflict check found none. Absent -> no velocity check.
        premortem_hook: Optional premortem analysis run over the
            synthesis summary; its rendered section is folded into the
            summary. Absent -> no premortem phase.
    """

    __slots__ = (
        "_config",
        "_conflict_detector",
        "_consensus_hook",
        "_premortem_hook",
    )

    def __init__(
        self,
        config: StructuredPhasesConfig,
        *,
        conflict_detector: ConflictDetector | None = None,
        consensus_hook: ConsensusVelocityHook | None = None,
        premortem_hook: PremortemHook | None = None,
    ) -> None:
        self._config = config
        self._conflict_detector: ConflictDetector = (
            conflict_detector
            if conflict_detector is not None
            else build_conflict_detector(config)
        )
        self._consensus_hook = consensus_hook
        self._premortem_hook = premortem_hook

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

        # Consensus-velocity check: when the gathered positions have
        # converged prematurely the discussion round is forced so a
        # devil's-advocate pass surfaces the suppressed disagreement,
        # even if the leader's own conflict check would skip it.
        force_discussion = self._detect_premature_consensus(
            meeting_id=meeting_id,
            inputs=inputs,
        )

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
                force_discussion=force_discussion,
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

        # Premortem: fold a failure-mode / assumption analysis of the
        # synthesised decision into the summary so decisions / action
        # items parsed below also scan it.
        summary = await self._maybe_append_premortem(
            meeting_id=meeting_id,
            summary=summary,
            participant_ids=participant_ids,
            agent_caller=agent_caller,
            tracker=tracker,
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

    def _detect_premature_consensus(
        self,
        *,
        meeting_id: str,
        inputs: list[tuple[str, str]],
    ) -> bool:
        """Run the consensus-velocity hook over the gathered positions.

        Returns:
            ``True`` when the hook is wired AND it flags the input
            positions as prematurely converged (so the discussion round
            should be forced); ``False`` otherwise.
        """
        if self._consensus_hook is None:
            return False
        positions = tuple(content for _, content in inputs)
        try:
            detected = self._consensus_hook(positions)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # Advisory check only: a hook failure must not abort the meeting
            # and discard all phase output. Skip the forced-discussion nudge.
            logger.warning(
                MEETING_CONSENSUS_VELOCITY_FAILED,
                meeting_id=meeting_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False
        if detected:
            logger.info(
                MEETING_CONSENSUS_VELOCITY_FORCED,
                meeting_id=meeting_id,
                position_count=len(positions),
            )
        return detected

    async def _maybe_append_premortem(
        self,
        *,
        meeting_id: str,
        summary: str,
        participant_ids: tuple[str, ...],
        agent_caller: AgentCaller,
        tracker: TokenTracker,
    ) -> str:
        """Fold a premortem analysis section into the synthesis summary.

        No-op (returns ``summary`` unchanged) when no premortem hook is
        wired, the token budget is exhausted, or the analysis surfaced
        nothing.

        Returns:
            The summary, with a premortem section appended when one was
            produced.
        """
        if self._premortem_hook is None or tracker.is_exhausted:
            return summary
        logger.info(
            MEETING_PHASE_STARTED,
            meeting_id=meeting_id,
            phase=MeetingPhase.PREMORTEM,
        )
        try:
            section = await self._premortem_hook(
                synthesis_text=summary,
                participant_ids=participant_ids,
                agent_caller=agent_caller,
                token_budget=tracker.remaining,
                context_id=meeting_id,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # Best-effort phase: a hook failure returns the summary without
            # a premortem section rather than discarding the whole meeting.
            logger.warning(
                MEETING_PREMORTEM_FAILED,
                meeting_id=meeting_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return summary
        logger.info(
            MEETING_PHASE_COMPLETED,
            meeting_id=meeting_id,
            phase=MeetingPhase.PREMORTEM,
        )
        if not section.strip():
            return summary
        logger.info(
            MEETING_PREMORTEM_APPENDED,
            meeting_id=meeting_id,
            section_length=len(section),
        )
        return f"{summary}\n\n{_PREMORTEM_SECTION_HEADING}\n\n{section}"
