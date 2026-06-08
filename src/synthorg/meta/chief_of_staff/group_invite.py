# module-kind: service
"""Agent-initiated invite coordinator for group chat.

Owns the PARK half of the agent-initiated invite flow: it parses a
structured group-chat contribution into a message + optional invite
request, and parks an invite (write the ``ConversationInvite`` row
FIRST, then the gating ``ApprovalItem``) behind human consent. Mirrors
``ChiefOfStaffProposer._park_proposal`` -- row first so no caller sees a
gating approval with no backing invite, self-atomic cleanup on the
approval-store failure path.

The RESUME half (accept / decline on the human consent decision) lives
in ``api/controllers/_conversational_resume.py``, repo-direct and
ungated, so a parked invite stays decidable even after the invite
feature is toggled off -- exactly as the conversational-intake flow
splits park (``propose.py``) from resume (the resume module).
"""

import uuid
from datetime import datetime

from synthorg.approval.enums import ApprovalSource, ApprovalStatus
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.agent import AgentIdentity
from synthorg.core.approval import ApprovalItem
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.json_parsing import extract_json_from_llm_response
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.hr.registry import AgentRegistryService
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.enums import (
    ConversationInviteStatus,
    ConversationParticipantStatus,
)
from synthorg.meta.chief_of_staff.group_models import (
    ConversationInvite,
    ConversationParticipant,
    GroupContribution,
    InviteRequest,
    PendingInviteSummary,
)
from synthorg.meta.chief_of_staff.prompts import GROUP_CONTRIBUTION_INVITE_PROMPT
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import (
    COS_GROUP_INVITE_PARK_FAILED,
    COS_GROUP_INVITE_REQUESTED,
    COS_GROUP_INVITE_RESPONSE_INVALID,
    COS_GROUP_INVITE_SKIPPED,
)
from synthorg.persistence.conversation_invite_protocol import (
    ConversationInviteFilterSpec,
    ConversationInviteRepository,
)
from synthorg.persistence.conversation_participant_protocol import (
    ConversationParticipantFilterSpec,
    ConversationParticipantRepository,
)

logger = get_logger(__name__)

_ACTION_TYPE = NotBlankStr("conversational:invite_agent")


def _new_id() -> NotBlankStr:
    """Return a fresh opaque identifier.

    Returns:
        ``NotBlankStr`` instance.
    """
    return NotBlankStr(str(uuid.uuid4()))


def _parse_invite(raw: object) -> InviteRequest | None:
    """Parse the ``invite`` field of a contribution envelope.

    Returns:
        An :class:`InviteRequest` when ``raw`` is a well-formed
        ``{"target": ..., "reason": ...}`` object, else ``None``.
    """
    if not isinstance(raw, dict):
        return None
    target = raw.get("target")
    reason = raw.get("reason")
    if (
        isinstance(target, str)
        and target.strip()
        and isinstance(reason, str)
        and reason.strip()
    ):
        return InviteRequest(
            target=NotBlankStr(target.strip()),
            reason=NotBlankStr(reason.strip()),
        )
    return None


def parse_group_contribution(raw: str) -> tuple[GroupContribution, bool]:
    """Parse a structured group-chat contribution envelope.

    Graceful: a non-envelope or malformed response degrades to
    ``message=<raw text>`` with no invite, so one agent's bad structured
    output never drops its contribution nor parks a bogus invite.

    Returns:
        ``(contribution, was_valid_envelope)``. ``was_valid_envelope`` is
        ``False`` when the response could not be read as a
        ``{"message": str, ...}`` object, so the caller can log it.
    """
    text = raw.strip()
    data = extract_json_from_llm_response(text)
    if not isinstance(data, dict):
        return GroupContribution(message=text, invite=None), False
    message = data.get("message")
    if not isinstance(message, str):
        return GroupContribution(message=text, invite=None), False
    invite = _parse_invite(data.get("invite"))
    return GroupContribution(message=message, invite=invite), True


class GroupInviteCoordinator:
    """Parks agent-initiated group-chat invites behind human consent.

    Args:
        invite_repo: Durable store for parked invites.
        approval_store: Human approval queue (consent gate).
        agent_registry: Source of truth for resolving an invite target
            (by name or role) to a registered identity.
        participant_repo: Roster store, read to reject an invite for an
            agent that is already a participant or when the room is full.
        config: Chief of Staff configuration (invite bounds).
        clock: Injectable time source (defaults to ``SystemClock``).
    """

    def __init__(  # noqa: PLR0913 -- DI seam: independently-wired collaborators
        self,
        *,
        invite_repo: ConversationInviteRepository,
        approval_store: ApprovalStoreProtocol,
        agent_registry: AgentRegistryService,
        participant_repo: ConversationParticipantRepository,
        config: ChiefOfStaffConfig,
        clock: Clock | None = None,
    ) -> None:
        self._invite_repo = invite_repo
        self._approval_store = approval_store
        self._agent_registry = agent_registry
        self._participant_repo = participant_repo
        self._config = config
        self._clock: Clock = clock or SystemClock()

    def contribution_prompt(self) -> str:
        """Return the invite-enabled contribution prompt template.

        Returns:
            The structured-envelope prompt used in place of the plain
            template when the invite feature is on.
        """
        return GROUP_CONTRIBUTION_INVITE_PROMPT

    def parse_contribution(
        self,
        raw: str,
        *,
        conversation_id: NotBlankStr,
        agent_id: NotBlankStr,
    ) -> GroupContribution:
        """Parse one agent's structured contribution, logging a degrade.

        Returns:
            The parsed contribution; on a malformed / non-envelope
            response the raw text is used as the message with no invite
            and an invalid-response event is logged (round continues).
        """
        contribution, valid = parse_group_contribution(raw)
        if not valid:
            logger.info(
                COS_GROUP_INVITE_RESPONSE_INVALID,
                conversation_id=conversation_id,
                agent_id=agent_id,
            )
        return contribution

    async def invited_preamble(
        self,
        conversation_id: NotBlankStr,
        target_agent_id: NotBlankStr,
        *,
        already_spoke: bool,
    ) -> str | None:
        """Build the fenced inviter+reason handover for an invited agent.

        Looks up the accepted invite that admitted *target_agent_id* and
        renders a preamble naming the inviter and the stated reason,
        fenced as ``<task-data>`` so the joining agent treats the
        handover as context, not instructions. The handover lands once,
        on the agent's first turn only.

        Args:
            conversation_id: The group conversation.
            target_agent_id: The participant about to take a turn.
            already_spoke: Whether the participant has a prior attributed
                turn in this conversation; the caller derives it from the
                transcript it owns.

        Returns:
            The fenced preamble, or ``None`` when the agent already
            contributed (the handover is first-turn only) or was not
            admitted through an accepted invite (an original
            participant), so only a genuinely invited agent receives it.
        """
        if already_spoke:
            return None
        accepted = await self._invite_repo.query(
            ConversationInviteFilterSpec(
                conversation_id=conversation_id,
                target_agent_id=target_agent_id,
                status=ConversationInviteStatus.ACCEPTED,
            ),
            limit=1,
        )
        if not accepted:
            return None
        invite = accepted[0]
        inviter = await self._agent_registry.get(invite.requested_by_agent_id)
        inviter_label = (
            inviter.name if inviter is not None else invite.requested_by_agent_id
        )
        body = (
            f"You were invited into this conversation by {inviter_label}. "
            f"Reason given: {invite.reason}"
        )
        return wrap_untrusted(TAG_TASK_DATA, body)

    async def request_invite(
        self,
        *,
        conversation_id: NotBlankStr,
        requested_by_agent_id: NotBlankStr,
        requested_by_name: NotBlankStr,
        invite_request: InviteRequest,
        now: datetime,
    ) -> PendingInviteSummary | None:
        """Park an agent-initiated invite behind human consent.

        Bounds (each logged + skipped, never raised): the target must
        resolve to a registered agent, must not already be an active
        participant, must not breach the participant cap, and must not
        duplicate a still-pending invite for the same target.

        Returns:
            A :class:`PendingInviteSummary` when the invite was parked,
            or ``None`` when a bound tripped or the park failed (the
            contribution itself still stands).
        """
        roster = await self._participant_repo.query(
            ConversationParticipantFilterSpec(
                conversation_id=conversation_id,
                status=ConversationParticipantStatus.ACTIVE,
            )
        )
        target = await self._resolve_target(invite_request.target)
        skip = self._skip_reason(target, roster)
        if skip is not None or target is None:
            logger.info(
                COS_GROUP_INVITE_SKIPPED,
                conversation_id=conversation_id,
                requested_by=requested_by_agent_id,
                target=invite_request.target,
                reason=skip or "unknown_target",
            )
            return None
        target_agent_id = NotBlankStr(str(target.id))
        if await self._has_pending_invite(conversation_id, target_agent_id):
            logger.info(
                COS_GROUP_INVITE_SKIPPED,
                conversation_id=conversation_id,
                requested_by=requested_by_agent_id,
                target=invite_request.target,
                reason="duplicate_pending",
            )
            return None
        return await self._park(
            conversation_id=conversation_id,
            requested_by_agent_id=requested_by_agent_id,
            requested_by_name=requested_by_name,
            target=target,
            reason=invite_request.reason,
            now=now,
        )

    async def _resolve_target(self, target: str) -> AgentIdentity | None:
        """Resolve an invite target (a name or a role) to an identity.

        Tries an exact name match first, then the first active agent
        whose role matches (case-insensitive), mirroring the routing
        layer's role resolution.

        Returns:
            The resolved identity, or ``None`` when no active agent
            matches the target reference.
        """
        by_name = await self._agent_registry.get_by_name(NotBlankStr(target))
        if by_name is not None:
            return by_name
        wanted = target.strip().casefold()
        for identity in await self._agent_registry.list_active():
            if identity.role.strip().casefold() == wanted:
                return identity
        return None

    def _skip_reason(
        self,
        target: AgentIdentity | None,
        roster: tuple[ConversationParticipant, ...],
    ) -> str | None:
        """Return why the invite must be skipped, or ``None`` to proceed.

        Returns:
            A short reason code (``unknown_target`` / ``already_participant``
            / ``at_capacity``), or ``None`` when the invite may proceed.
        """
        if target is None:
            return "unknown_target"
        target_agent_id = str(target.id)
        if any(p.agent_id == target_agent_id for p in roster):
            return "already_participant"
        if len(roster) >= self._config.group_chat_max_participants:
            return "at_capacity"
        return None

    async def _has_pending_invite(
        self, conversation_id: NotBlankStr, target_agent_id: NotBlankStr
    ) -> bool:
        """Return whether a still-pending invite already targets *target_agent_id*.

        Returns:
            ``True`` when an undecided invite for the same target exists.
        """
        existing = await self._invite_repo.query(
            ConversationInviteFilterSpec(
                conversation_id=conversation_id,
                target_agent_id=target_agent_id,
                status=ConversationInviteStatus.PENDING,
            )
        )
        return len(existing) > 0

    async def _park(  # noqa: PLR0913 -- one parked invite's full context
        self,
        *,
        conversation_id: NotBlankStr,
        requested_by_agent_id: NotBlankStr,
        requested_by_name: NotBlankStr,
        target: AgentIdentity,
        reason: NotBlankStr,
        now: datetime,
    ) -> PendingInviteSummary | None:
        """Persist the invite, then publish the gating consent approval.

        Row-first (so no caller sees an approval with no backing invite)
        + self-atomic cleanup (delete the invite if the approval add
        fails). A park failure is non-fatal to the round: the
        contribution already stands, so this logs + returns ``None``
        rather than aborting mid-round.

        Returns:
            The parked invite summary, or ``None`` on park failure.
        """
        approval_id = _new_id()
        invite_id = _new_id()
        target_agent_id = NotBlankStr(str(target.id))
        await self._invite_repo.save(
            ConversationInvite(
                id=invite_id,
                conversation_id=conversation_id,
                approval_id=approval_id,
                requested_by_agent_id=requested_by_agent_id,
                target_agent_id=target_agent_id,
                target_role=target.role,
                reason=reason,
                status=ConversationInviteStatus.PENDING,
                created_at=now,
            )
        )
        try:
            await self._approval_store.add(
                self._build_approval_item(
                    approval_id=approval_id,
                    invite_id=invite_id,
                    conversation_id=conversation_id,
                    requested_by_agent_id=requested_by_agent_id,
                    target=target,
                    reason=reason,
                    now=now,
                )
            )
        except Exception as exc:
            reraise_critical(exc)
            await self._cleanup_invite(invite_id, conversation_id)
            logger.warning(
                COS_GROUP_INVITE_PARK_FAILED,
                conversation_id=conversation_id,
                invite_id=invite_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        logger.info(
            COS_GROUP_INVITE_REQUESTED,
            conversation_id=conversation_id,
            approval_id=approval_id,
            requested_by=requested_by_agent_id,
            target_agent_id=target_agent_id,
        )
        return PendingInviteSummary(
            approval_id=approval_id,
            requested_by_agent_id=requested_by_agent_id,
            requested_by_name=requested_by_name,
            target_agent_id=target_agent_id,
            target_name=target.name,
            target_role=target.role,
            reason=reason,
        )

    def _build_approval_item(  # noqa: PLR0913 -- ApprovalItem field set is broad
        self,
        *,
        approval_id: NotBlankStr,
        invite_id: NotBlankStr,
        conversation_id: NotBlankStr,
        requested_by_agent_id: NotBlankStr,
        target: AgentIdentity,
        reason: NotBlankStr,
        now: datetime,
    ) -> ApprovalItem:
        """Compose the parked consent approval for one invite.

        Returns:
            ``ApprovalItem`` instance.
        """
        return ApprovalItem(
            id=uuid.UUID(approval_id),
            action_type=_ACTION_TYPE,
            title=NotBlankStr(f"Invite {target.name} into the conversation"),
            description=reason,
            requested_by=requested_by_agent_id,
            risk_level=self._config.invite_default_risk_level,
            source=ApprovalSource.CONVERSATIONAL_INVITE,
            status=ApprovalStatus.PENDING,
            created_at=now,
            metadata={
                "conversation_id": conversation_id,
                "invite_id": invite_id,
                "target_agent_id": str(target.id),
                "requested_by": requested_by_agent_id,
            },
        )

    async def _cleanup_invite(
        self, invite_id: NotBlankStr, conversation_id: NotBlankStr
    ) -> None:
        """Best-effort delete of a parked invite whose approval add failed.

        Never re-raises: the original park failure is the error the
        operator needs, so a cleanup failure is only logged.
        """
        try:
            await self._invite_repo.delete(invite_id)
        except Exception as cleanup_exc:
            reraise_critical(cleanup_exc)
            logger.warning(
                COS_GROUP_INVITE_PARK_FAILED,
                conversation_id=conversation_id,
                invite_id=invite_id,
                detail="cleanup_failed",
                error_type=type(cleanup_exc).__name__,
                error=safe_error_description(cleanup_exc),
            )


__all__ = ["GroupInviteCoordinator", "parse_group_contribution"]
