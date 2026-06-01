# module-kind: service
"""Multi-agent group-chat service.

One human talks to several agents in a ``kind='group'`` conversation.
Each human turn drives ONE round-robin round: every active participant
contributes once, in stable enrolment order, seeing the shared
transcript (prior turns + the human message, untrusted-content fenced as
``<task-data>``) plus this round's earlier peer contributions (fenced
as ``<peer-contribution>``). Contributions are attributed and persisted
as ``AGENT`` turns. There is no synthesis or weighting -- the human
reads every attributed contribution.

A round is bounded so a single human turn cannot drive unbounded cost:
a per-round :class:`TokenTracker` budget (with a reserve), a
participant cap, and a total-turn cap. When a bound trips mid-round the
remaining participants are recorded in ``participants_skipped`` and the
bound is surfaced as ``truncated_reason`` -- never silently dropped.

AuthorityDeference (S1 risk 2.2): the round feeds prior contributions
(possibly an authority-bearing role's) to later participants in the
same round, which is the deference vector the
:class:`AuthorityDeferenceGuard` exists for. This service reuses that
guard's pattern scan to audit the peer-contribution block before each
dispatch (detect-and-log, matching the guard's own contract); the
untrusted-content ``<peer-contribution>`` fencing in the persona prompt is the
injection defence.
"""

from typing import TYPE_CHECKING

from synthorg.budget.tracker import CostTracker
from synthorg.communication.meeting._token_tracker import TokenTracker
from synthorg.communication.meeting.protocol import AgentCaller
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import ConversationRole, ConversationStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.middleware.s1_constraints import AuthorityDeferenceGuard
from synthorg.hr.registry import AgentRegistryService
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.conversation_lock import ConversationLockRegistry
from synthorg.meta.chief_of_staff.enums import (
    ConversationKind,
    GroupChatTruncationReason,
)
from synthorg.meta.chief_of_staff.group_invite import GroupInviteCoordinator
from synthorg.meta.chief_of_staff.group_models import (
    AttributedContribution,
    ConversationParticipant,
    GroupConverseArgs,
    GroupConverseResult,
    InviteRequest,
    PendingInviteSummary,
)
from synthorg.meta.chief_of_staff.group_prompt import (
    audit_authority,
    build_group_prompt,
)
from synthorg.meta.chief_of_staff.group_roster import (
    active_participants,
    dedupe_participants,
    enrol_participants,
    new_id,
    ordered_turns,
    resolve_identities,
)
from synthorg.meta.chief_of_staff.models import Conversation, ConversationTurn
from synthorg.meta.chief_of_staff.prompts import GROUP_CONTRIBUTION_PROMPT
from synthorg.meta.errors import (
    ConversationClosedError,
    ConversationNotFoundError,
    GroupConversationEmptyError,
    GroupParticipantLimitError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import (
    COS_GROUP_CONTRIBUTION,
    COS_GROUP_CONTRIBUTION_FAILED,
    COS_GROUP_ROUND_COMPLETED,
    COS_GROUP_ROUND_STARTED,
    COS_GROUP_ROUND_TRUNCATED,
)
from synthorg.persistence.conversation_participant_protocol import (
    ConversationParticipantRepository,
)
from synthorg.persistence.conversation_protocol import (
    ConversationRepository,
    ConversationTurnRepository,
)

if TYPE_CHECKING:
    from datetime import datetime

logger = get_logger(__name__)


class GroupChatService:
    """Round-robin multi-agent group chat over the shared conversation store.

    Args:
        agent_caller: Per-agent LLM dispatch (persona + untrusted-content directive),
            built via ``build_meeting_agent_caller``.
        agent_registry: Source of truth for participant identities.
        config: Chief of Staff configuration (group-chat bounds).
        conversation_repo: Conversation header store.
        turn_repo: Append-only conversation turn store.
        participant_repo: Group-chat participant roster store.
        clock: Injectable time source (defaults to ``SystemClock``).
        authority_guard: Authority-cue scanner reused for peer-block
            auditing; defaults to a fresh guard with the standard config.
        cost_tracker: Optional cost tracker (the caller dispatch records
            via the meeting chokepoint when wired).
        invite_coordinator: Agent-initiated invite coordinator;
            present only when the invite feature is on. When ``None`` the
            round runs the plain-text contribution path unchanged.
    """

    def __init__(  # noqa: PLR0913 -- DI seam: independently-wired collaborators
        self,
        *,
        agent_caller: AgentCaller,
        agent_registry: AgentRegistryService,
        config: ChiefOfStaffConfig,
        conversation_repo: ConversationRepository,
        turn_repo: ConversationTurnRepository,
        participant_repo: ConversationParticipantRepository,
        clock: Clock | None = None,
        authority_guard: AuthorityDeferenceGuard | None = None,
        cost_tracker: CostTracker | None = None,
        invite_coordinator: GroupInviteCoordinator | None = None,
    ) -> None:
        self._agent_caller = agent_caller
        self._agent_registry = agent_registry
        self._config = config
        self._conversation_repo = conversation_repo
        self._turn_repo = turn_repo
        self._participant_repo = participant_repo
        self._clock: Clock = clock or SystemClock()
        self._authority_guard = authority_guard or AuthorityDeferenceGuard()
        self._cost_tracker = cost_tracker
        # Present only when the invite feature is on (built into the
        # service by ``build_group_chat_service``); ``None`` keeps the
        # round on the literal plain-text contribution path.
        self._invite_coordinator = invite_coordinator
        self._locks = ConversationLockRegistry()

    async def converse(self, args: GroupConverseArgs) -> GroupConverseResult:
        """Run one round-robin round for a group conversation.

        Args:
            args: The round input (message, owner, optional conversation
                id, initial participants when opening, optional project).

        Returns:
            The round outcome: attributed contributions, the active
            roster, and any truncation.

        Raises:
            ConversationNotFoundError: ``conversation_id`` is unknown,
                owned by another user, or not a group conversation.
            ConversationClosedError: The conversation is terminal.
            GroupConversationEmptyError: A new conversation named no
                participants.
            GroupParticipantLimitError: More participants than the cap.
            GroupParticipantUnknownError: A named agent is not registered.
        """
        now = self._clock.now()
        conversation = await self._resolve_conversation(args, now)
        # Serialise the whole round per conversation so two concurrent
        # converse() calls cannot interleave their history snapshots nor
        # commit turns the other side never saw. Held across every
        # agent dispatch -- round-robin contributions must stay linear
        # because each one feeds the next.
        async with await self._locks.acquire_for(conversation.id):
            return await self._run_round(conversation, args, now)

    async def _resolve_conversation(
        self, args: GroupConverseArgs, now: datetime
    ) -> Conversation:
        """Open a new group conversation or load an existing one.

        Returns:
            ``Conversation`` instance.

        Raises:
            ConversationNotFoundError: Unknown / foreign / non-group id.
            ConversationClosedError: The conversation is terminal.
            GroupConversationEmptyError: New conversation, no participants.
            GroupParticipantLimitError: More participants than the cap.
            GroupParticipantUnknownError: A named agent is not registered.
        """
        if args.conversation_id is None:
            return await self._open_group_conversation(args, now)
        existing = await self._conversation_repo.get(args.conversation_id)
        # Ownership + kind mismatch both map to NotFound so a caller
        # cannot probe foreign or non-group conversations by id.
        if existing is None or existing.created_by != args.created_by:
            raise ConversationNotFoundError(conversation_id=args.conversation_id)
        if existing.kind is not ConversationKind.GROUP:
            raise ConversationNotFoundError(conversation_id=args.conversation_id)
        if existing.status is ConversationStatus.CLOSED:
            raise ConversationClosedError(conversation_id=existing.id)
        return existing

    async def _open_group_conversation(
        self, args: GroupConverseArgs, now: datetime
    ) -> Conversation:
        """Validate participants, create the conversation, enrol the roster.

        Every named agent is resolved BEFORE the conversation row is
        written so an unknown agent cannot leave a dangling participant-
        less conversation.

        Returns:
            ``Conversation`` instance.

        Raises:
            GroupConversationEmptyError: No participants were named.
            GroupParticipantLimitError: More participants than the cap.
            GroupParticipantUnknownError: A named agent is not registered.
        """
        agent_ids = dedupe_participants(args.participants)
        if not agent_ids:
            raise GroupConversationEmptyError
        if len(agent_ids) > self._config.group_chat_max_participants:
            raise GroupParticipantLimitError(
                requested=len(agent_ids),
                limit=self._config.group_chat_max_participants,
            )
        identities = await resolve_identities(self._agent_registry, agent_ids)
        conversation = Conversation(
            id=new_id(),
            created_by=args.created_by,
            created_at=now,
            updated_at=now,
            status=ConversationStatus.ACTIVE,
            kind=ConversationKind.GROUP,
        )
        await self._conversation_repo.save(conversation)
        await enrol_participants(
            self._participant_repo,
            conversation_id=conversation.id,
            identities=identities,
            added_by=args.created_by,
            now=now,
        )
        return conversation

    async def _run_round(
        self, conversation: Conversation, args: GroupConverseArgs, now: datetime
    ) -> GroupConverseResult:
        """Append the human turn, then round-robin the active roster.

        Re-fetches under the lock and aborts if the conversation is no
        longer ACTIVE (a concurrent close).

        Returns:
            ``GroupConverseResult`` instance.

        Raises:
            ConversationClosedError: The conversation went terminal.
        """
        current = await self._conversation_repo.get(conversation.id)
        if current is None or current.status is not ConversationStatus.ACTIVE:
            raise ConversationClosedError(conversation_id=conversation.id)
        conversation = current
        participants = await active_participants(
            self._participant_repo, conversation.id
        )
        prior_turns = await ordered_turns(self._turn_repo, conversation.id)
        next_sequence = len(prior_turns)
        user_turn = ConversationTurn(
            id=new_id(),
            conversation_id=conversation.id,
            sequence=next_sequence,
            role=ConversationRole.USER,
            content=args.message,
            created_at=now,
        )
        await self._turn_repo.append(user_turn)
        logger.info(
            COS_GROUP_ROUND_STARTED,
            conversation_id=conversation.id,
            participant_count=len(participants),
        )
        history = (*prior_turns, user_turn)
        return await self._round_robin(
            conversation, history, participants, next_sequence + 1, now
        )

    async def _round_robin(
        self,
        conversation: Conversation,
        history: tuple[ConversationTurn, ...],
        participants: tuple[ConversationParticipant, ...],
        start_sequence: int,
        now: datetime,
    ) -> GroupConverseResult:
        """Drive each active participant once, bounded by budget + turns.

        Returns:
            ``GroupConverseResult`` instance.
        """
        budget = self._config.group_chat_round_token_budget
        tracker = TokenTracker(budget=budget)
        reserve = int(budget * self._config.group_chat_token_reserve_ratio)
        contributions: list[AttributedContribution] = []
        pending_invites: list[PendingInviteSummary] = []
        skipped: list[NotBlankStr] = []
        truncated: GroupChatTruncationReason | None = None
        sequence = start_sequence
        total_turns = len(history)
        for index, participant in enumerate(participants):
            truncated = self._round_bound(tracker, reserve, total_turns)
            if truncated is not None:
                skipped.extend(p.agent_id for p in participants[index:])
                break
            call_max_tokens = min(
                self._config.group_chat_per_agent_max_tokens,
                tracker.remaining - reserve,
            )
            contribution, invite_req = await self._dispatch_contribution(
                conversation,
                history,
                contributions,
                participant,
                sequence,
                call_max_tokens,
                tracker,
                now,
            )
            if contribution is None:
                skipped.append(participant.agent_id)
                continue
            contributions.append(contribution)
            await self._maybe_park_invite(
                conversation, participant, invite_req, pending_invites, now
            )
            sequence += 1
            total_turns += 1
        await self._conversation_repo.save(
            conversation.model_copy(update={"updated_at": now})
        )
        self._log_round_outcome(conversation.id, contributions, skipped, truncated)
        return GroupConverseResult(
            conversation_id=conversation.id,
            contributions=tuple(contributions),
            participants=participants,
            participants_skipped=tuple(skipped),
            truncated_reason=truncated,
            pending_invites=tuple(pending_invites),
        )

    async def _maybe_park_invite(
        self,
        conversation: Conversation,
        participant: ConversationParticipant,
        invite_req: InviteRequest | None,
        pending_invites: list[PendingInviteSummary],
        now: datetime,
    ) -> None:
        """Park an agent-initiated invite if one was requested and uncapped.

        No-op unless the invite feature is on, an invite was parsed this
        turn, and the per-round park cap (``invite_max_per_round``) is
        not yet reached. A parked invite is appended to *pending_invites*
        so the round result can surface it for the inline consent prompt.
        """
        if (
            invite_req is None
            or self._invite_coordinator is None
            or len(pending_invites) >= self._config.invite_max_per_round
        ):
            return
        summary = await self._invite_coordinator.request_invite(
            conversation_id=conversation.id,
            requested_by_agent_id=participant.agent_id,
            requested_by_name=participant.agent_name,
            invite_request=invite_req,
            now=now,
        )
        if summary is not None:
            pending_invites.append(summary)

    def _round_bound(
        self, tracker: TokenTracker, reserve: int, total_turns: int
    ) -> GroupChatTruncationReason | None:
        """Return the bound that stops the round now, or ``None``.

        Returns:
            The tripped truncation reason, or ``None`` to continue.
        """
        if total_turns >= self._config.group_chat_max_total_turns:
            return GroupChatTruncationReason.MAX_TOTAL_TURNS_REACHED
        if tracker.remaining <= reserve:
            return GroupChatTruncationReason.TOKEN_BUDGET_EXHAUSTED
        return None

    async def _dispatch_contribution(  # noqa: PLR0913 -- one contribution's full context
        self,
        conversation: Conversation,
        history: tuple[ConversationTurn, ...],
        prior_contributions: list[AttributedContribution],
        participant: ConversationParticipant,
        sequence: int,
        max_tokens: int,
        tracker: TokenTracker,
        now: datetime,
    ) -> tuple[AttributedContribution | None, InviteRequest | None]:
        """Dispatch one participant's turn and persist its attributed turn.

        When the invite feature is on, the agent answers a structured
        envelope: the parsed ``message`` is persisted as the turn (never
        the raw JSON), and any parsed invite request is returned for the
        round loop to park behind consent. When off, the raw reply is
        the message and the invite is always ``None``.

        Returns:
            ``(contribution, invite_request)``: the attributed
            contribution (or ``None`` when the agent returned empty
            content), paired with any parsed invite request (or ``None``).

        Raises:
            Exception: The agent dispatch failed (the round aborts; the
                lock guarantees no interleaving with a concurrent round).
        """
        # The invite feature swaps in a structured-envelope template; the
        # plain template stays the default so the feature-off path is
        # unchanged. The structured-output ask lives with the invite
        # coordinator, never the shared persona renderer.
        template = (
            self._invite_coordinator.contribution_prompt()
            if self._invite_coordinator is not None
            else GROUP_CONTRIBUTION_PROMPT
        )
        preamble: str | None = None
        if self._invite_coordinator is not None:
            preamble = await self._invite_coordinator.invited_preamble(
                conversation.id,
                participant.agent_id,
                already_spoke=any(
                    turn.author_agent_id == participant.agent_id for turn in history
                ),
            )
        prompt = build_group_prompt(
            history, prior_contributions, template=template, preamble=preamble
        )
        audit_authority(
            self._authority_guard, conversation.id, participant, prior_contributions
        )
        try:
            response = await self._agent_caller(
                participant.agent_id, prompt, max_tokens, conversation.id
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                COS_GROUP_CONTRIBUTION_FAILED,
                conversation_id=conversation.id,
                agent_id=participant.agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        tracker.record(response.input_tokens, response.output_tokens)
        content, invite_req = self._extract_contribution(
            response.content, conversation.id, participant.agent_id
        )
        if not content:
            logger.warning(
                COS_GROUP_CONTRIBUTION_FAILED,
                conversation_id=conversation.id,
                agent_id=participant.agent_id,
                detail="empty_content",
            )
            return None, None
        await self._turn_repo.append(
            ConversationTurn(
                id=new_id(),
                conversation_id=conversation.id,
                sequence=sequence,
                role=ConversationRole.AGENT,
                content=NotBlankStr(content),
                author_agent_id=participant.agent_id,
                author_name=participant.agent_name,
                created_at=now,
            )
        )
        logger.info(
            COS_GROUP_CONTRIBUTION,
            conversation_id=conversation.id,
            agent_id=participant.agent_id,
            sequence=sequence,
        )
        return (
            AttributedContribution(
                agent_id=participant.agent_id,
                agent_name=participant.agent_name,
                participant_role=participant.participant_role,
                content=NotBlankStr(content),
                sequence=sequence,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
            ),
            invite_req,
        )

    def _extract_contribution(
        self,
        raw_content: str | None,
        conversation_id: NotBlankStr,
        agent_id: NotBlankStr,
    ) -> tuple[str, InviteRequest | None]:
        """Resolve one reply into its message text + optional invite.

        With the invite feature on, the reply is a structured envelope:
        the parsed ``message`` text and any invite are returned. When
        off, this is the literal plain-text path -- the raw reply,
        stripped, with no invite.

        Returns:
            ``(message_text, invite_request)``.
        """
        if self._invite_coordinator is None:
            return (raw_content or "").strip(), None
        parsed = self._invite_coordinator.parse_contribution(
            raw_content or "",
            conversation_id=conversation_id,
            agent_id=agent_id,
        )
        return parsed.message.strip(), parsed.invite

    def _log_round_outcome(
        self,
        conversation_id: NotBlankStr,
        contributions: list[AttributedContribution],
        skipped: list[NotBlankStr],
        truncated: GroupChatTruncationReason | None,
    ) -> None:
        """Log the round result -- truncation is never silent."""
        if truncated is not None:
            logger.warning(
                COS_GROUP_ROUND_TRUNCATED,
                conversation_id=conversation_id,
                reason=truncated.value,
                contributions=len(contributions),
                skipped=len(skipped),
            )
            return
        logger.info(
            COS_GROUP_ROUND_COMPLETED,
            conversation_id=conversation_id,
            contributions=len(contributions),
            skipped=len(skipped),
        )


__all__ = ["GroupChatService"]
