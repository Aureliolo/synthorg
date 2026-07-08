# module-kind: code
"""Post-meeting bridge feeding detected conflicts into resolution.

When a structured-phases meeting finishes with ``conflicts_detected`` set,
this bridge builds a :class:`Conflict` from the participants' positions and
hands it to the :class:`ConflictResolutionService`. Under the default hybrid
strategy a clear conflict auto-resolves and an ambiguous one escalates to the
human queue; either way dissent records and events flow through the already
wired dissent pipeline.

The bridge is best-effort: its ``__call__`` never raises, because the meeting
orchestrator invokes it with no surrounding ``try/except`` and an escaping
exception would turn a completed meeting into an unhandled failure.
"""

from typing import Final

from synthorg.communication.conflict_resolution.models import ConflictPosition
from synthorg.communication.conflict_resolution.service import (
    ConflictResolutionService,
)
from synthorg.communication.enums import ConflictType
from synthorg.communication.meeting.enums import MeetingPhase
from synthorg.communication.meeting.models import (
    MeetingContribution,
    MeetingMinutes,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meeting import (
    MEETING_CONFLICT_ESCALATION_FAILED,
    MEETING_CONFLICT_ESCALATION_RESOLVED,
    MEETING_CONFLICT_ESCALATION_SKIPPED,
    MEETING_CONFLICT_ESCALATION_STARTED,
)
from synthorg.settings.kill_switch import resolve_bool_with_fallback
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

#: A conflict needs at least this many distinct positions (mirrors the
#: ``Conflict`` model invariant, checked here for a clean early return).
_MIN_POSITIONS: Final[int] = 2

#: Upper bound on the one-line ``position`` summary derived from a
#: contribution; the full text is preserved verbatim as ``reasoning``.
_POSITION_SUMMARY_MAX_CHARS: Final[int] = 200

_SETTINGS_NAMESPACE: Final[str] = "communication"
_KILL_SWITCH_KEY: Final[str] = "meeting_conflict_escalation_enabled"


class MeetingConflictEscalationBridge:
    """Feed a conflicted meeting's positions into conflict resolution."""

    __slots__ = ("_agent_registry", "_config_resolver", "_conflict_service")

    def __init__(
        self,
        *,
        conflict_service: ConflictResolutionService,
        agent_registry: AgentRegistryService,
        config_resolver: ConfigResolverProtocol | None = None,
    ) -> None:
        self._conflict_service = conflict_service
        self._agent_registry = agent_registry
        self._config_resolver = config_resolver

    async def __call__(self, minutes: MeetingMinutes) -> None:
        """Resolve a conflicted meeting, containing every failure.

        Never raises: a resolution failure (including the hybrid strategy's
        hard-fail on a judge/provider outage) is logged and swallowed so the
        already-completed meeting is not turned into an unhandled error.
        """
        try:
            await self._escalate(minutes)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised; must not raise
            reraise_critical(exc)
            logger.warning(
                MEETING_CONFLICT_ESCALATION_FAILED,
                meeting_id=minutes.meeting_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _escalate(self, minutes: MeetingMinutes) -> None:
        """Build and resolve a conflict from the meeting's positions."""
        if not minutes.conflicts_detected:
            return
        enabled = await resolve_bool_with_fallback(
            resolver=self._config_resolver,
            namespace=_SETTINGS_NAMESPACE,
            key=_KILL_SWITCH_KEY,
            fallback=True,
        )
        if not enabled:
            logger.info(
                MEETING_CONFLICT_ESCALATION_SKIPPED,
                meeting_id=minutes.meeting_id,
                reason="disabled",
            )
            return

        positions = await self._build_positions(minutes)
        if len(positions) < _MIN_POSITIONS:
            logger.info(
                MEETING_CONFLICT_ESCALATION_SKIPPED,
                meeting_id=minutes.meeting_id,
                reason="insufficient_positions",
                position_count=len(positions),
            )
            return

        conflict = self._conflict_service.create_conflict(
            conflict_type=ConflictType.OTHER,
            subject=minutes.agenda.title,
            positions=positions,
            task_id=minutes.meeting_id,
        )
        logger.info(
            MEETING_CONFLICT_ESCALATION_STARTED,
            meeting_id=minutes.meeting_id,
            conflict_id=str(conflict.id),
            position_count=len(positions),
        )
        resolution, dissent_records = await self._conflict_service.resolve(conflict)
        logger.info(
            MEETING_CONFLICT_ESCALATION_RESOLVED,
            meeting_id=minutes.meeting_id,
            conflict_id=str(conflict.id),
            outcome=resolution.outcome,
            dissent_count=len(dissent_records),
        )

    async def _build_positions(
        self,
        minutes: MeetingMinutes,
    ) -> tuple[ConflictPosition, ...]:
        """Build one position per participant with resolvable metadata.

        A participant with no non-blank contribution, or one absent from the
        agent registry, is dropped rather than guessed at.

        Returns:
            The conflict positions, at most one per participant.
        """
        selected = self._select_contributions(minutes)
        if not selected:
            return ()
        identities = await self._agent_registry.get_by_ids(tuple(selected))
        positions: list[ConflictPosition] = []
        for agent_id, contribution in selected.items():
            identity = identities.get(agent_id)
            if identity is None:
                continue
            positions.append(
                ConflictPosition(
                    agent_id=agent_id,
                    agent_department=identity.department,
                    agent_level=identity.level,
                    position=_summarise(contribution.content),
                    reasoning=contribution.content,
                    timestamp=contribution.timestamp,
                )
            )
        return tuple(positions)

    def _select_contributions(
        self,
        minutes: MeetingMinutes,
    ) -> dict[str, MeetingContribution]:
        """Pick each participant's stance, discussion turn over input paper.

        The leader's conflict-check turn is excluded automatically: the
        leader is never in ``participant_ids``. Blank contributions are
        skipped so a position's text is always non-blank.

        Returns:
            Participant id to their chosen contribution, in participant order.
        """
        participants = set(minutes.participant_ids)
        discussion: dict[str, MeetingContribution] = {}
        gathering: dict[str, MeetingContribution] = {}
        for contribution in minutes.contributions:
            if contribution.agent_id not in participants:
                continue
            if not contribution.content.strip():
                continue
            if contribution.phase == MeetingPhase.DISCUSSION:
                discussion[contribution.agent_id] = contribution
            elif contribution.phase == MeetingPhase.INPUT_GATHERING:
                gathering[contribution.agent_id] = contribution
        selected: dict[str, MeetingContribution] = {}
        for participant_id in minutes.participant_ids:
            chosen = discussion.get(participant_id) or gathering.get(participant_id)
            if chosen is not None:
                selected[participant_id] = chosen
        return selected


def _summarise(content: str) -> NotBlankStr:
    """Condense a contribution to a one-line, length-capped position summary.

    Returns:
        The first line of *content*, capped, and guaranteed non-blank
        because callers only pass contributions with non-blank content.
    """
    first_line = content.strip().splitlines()[0].strip()
    if len(first_line) > _POSITION_SUMMARY_MAX_CHARS:
        return NotBlankStr(first_line[:_POSITION_SUMMARY_MAX_CHARS].rstrip())
    return NotBlankStr(first_line)


__all__ = ["MeetingConflictEscalationBridge"]
