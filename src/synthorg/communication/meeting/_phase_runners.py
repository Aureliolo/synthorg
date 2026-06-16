# module-kind: code
"""Phase-execution mixin for the structured-phases meeting protocol.

Holds the four ``_run_*`` phase coroutines. ``StructuredPhasesProtocol``
inherits this mixin so the protocol class itself stays a thin coordinator
(``__init__`` / ``get_protocol_type`` / ``run``). The mixin reads
``self._config`` and ``self._conflict_detector``, which the concrete
protocol owns via ``__slots__``.
"""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime

from synthorg.communication.meeting._prompts import inject_lens_perspective
from synthorg.communication.meeting._structured_phases_prompts import (
    build_conflict_check_prompt,
    build_discussion_prompt,
    build_input_prompt,
    build_synthesis_prompt,
)
from synthorg.communication.meeting._token_tracker import TokenTracker
from synthorg.communication.meeting.config import StructuredPhasesConfig
from synthorg.communication.meeting.enums import MeetingPhase
from synthorg.communication.meeting.errors import (
    MeetingBudgetExhaustedError,
    MeetingPhaseSlotError,
)
from synthorg.communication.meeting.models import MeetingContribution
from synthorg.communication.meeting.protocol import (
    AgentCaller,
    ConflictDetector,
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
)

logger = get_logger(__name__)


class StructuredPhaseRunnersMixin:
    """Phase coroutines for ``StructuredPhasesProtocol``.

    The concrete protocol supplies ``_config`` and ``_conflict_detector``
    through its own ``__slots__``; declared here as annotations for the
    type checker without creating class attributes.
    """

    __slots__ = ()

    _config: StructuredPhasesConfig
    _conflict_detector: ConflictDetector

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

        Raises:
            MeetingPhaseSlotError: If any parallel input slot is left
                unfilled (an internal invariant violation).
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
            """Collect one participant's input into the result slots."""
            prompt = build_input_prompt(agenda_text, participant_id)
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
                meeting_id,
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
                _ = tg.create_task(_collect_input(pid, idx, tokens_per_agent))

        # All slots must be filled -- TaskGroup propagates ExceptionGroup
        # on any task failure, so reaching this point means all succeeded.
        if not all(r is not None for r in result_inputs):
            msg = f"Expected {num_participants} inputs but some slots are None"
            logger.error(
                MEETING_INTERNAL_ERROR,
                error=msg,
                meeting_id=meeting_id,
            )
            raise MeetingPhaseSlotError(msg)
        if not all(c is not None for c in result_contributions):
            msg = f"Expected {num_participants} contributions but some slots are None"
            logger.error(
                MEETING_INTERNAL_ERROR,
                error=msg,
                meeting_id=meeting_id,
            )
            raise MeetingPhaseSlotError(msg)
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
        conflict_prompt = build_conflict_check_prompt(
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
            meeting_id,
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

            disc_prompt = build_discussion_prompt(
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
                meeting_id,
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

        synthesis_prompt = build_synthesis_prompt(
            agenda_text,
            inputs,
            discussion or None,
        )
        synthesis_response = await agent_caller(
            leader_id,
            synthesis_prompt,
            tracker.remaining,
            meeting_id,
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
