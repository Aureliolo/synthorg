# module-kind: service
"""Multi-agent group-chat service (#1970).

One human talks to several agents in a ``kind='group'`` conversation.
Each human turn drives ONE round-robin round: every active participant
contributes once, in stable enrolment order, seeing the shared
transcript (prior turns + the human message, SEC-1 fenced as
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
SEC-1 ``<peer-contribution>`` fencing in the persona prompt is the
injection defence.
"""

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

from synthorg.budget.tracker import CostTracker
from synthorg.communication.meeting._token_tracker import TokenTracker
from synthorg.communication.meeting.protocol import AgentCaller
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import ConversationRole, ConversationStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.middleware.s1_constraints import AuthorityDeferenceGuard
from synthorg.engine.prompt_safety import (
    TAG_PEER_CONTRIBUTION,
    TAG_TASK_DATA,
    wrap_untrusted,
)
from synthorg.hr.registry import AgentRegistryService
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.conversation_lock import ConversationLockRegistry
from synthorg.meta.chief_of_staff.enums import (
    ConversationKind,
    ConversationParticipantStatus,
    GroupChatTruncationReason,
)
from synthorg.meta.chief_of_staff.group_models import (
    AttributedContribution,
    ConversationParticipant,
    GroupConverseArgs,
    GroupConverseResult,
)
from synthorg.meta.chief_of_staff.models import Conversation, ConversationTurn
from synthorg.meta.chief_of_staff.prompts import GROUP_CONTRIBUTION_PROMPT
from synthorg.meta.errors import (
    ConversationClosedError,
    ConversationNotFoundError,
    GroupConversationEmptyError,
    GroupParticipantLimitError,
    GroupParticipantUnknownError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import (
    COS_GROUP_AUTHORITY_CUES_DETECTED,
    COS_GROUP_CONTRIBUTION,
    COS_GROUP_CONTRIBUTION_FAILED,
    COS_GROUP_PARTICIPANTS_ADDED,
    COS_GROUP_ROUND_COMPLETED,
    COS_GROUP_ROUND_STARTED,
    COS_GROUP_ROUND_TRUNCATED,
)
from synthorg.persistence.conversation_participant_protocol import (
    ConversationParticipantFilterSpec,
    ConversationParticipantRepository,
)
from synthorg.persistence.conversation_protocol import (
    ConversationRepository,
    ConversationTurnFilterSpec,
    ConversationTurnRepository,
)

if TYPE_CHECKING:
    from datetime import datetime

    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)

# Group conversations are short interactive sessions; 1000 turns is a
# generous ceiling that hands every round the full history without
# pagination (the repo's own _MAX_PAGE_LIMIT clamps anything larger).
_MAX_TURNS_QUERY_LIMIT: int = 1000


def _new_id() -> NotBlankStr:
    """Return a fresh opaque identifier.

    Returns:
        ``NotBlankStr`` instance.
    """
    return NotBlankStr(str(uuid.uuid4()))


class GroupChatService:
    """Round-robin multi-agent group chat over the shared conversation store.

    Args:
        agent_caller: Per-agent LLM dispatch (persona + SEC-1 directive),
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
        agent_ids = self._dedupe(args.participants)
        if not agent_ids:
            raise GroupConversationEmptyError
        if len(agent_ids) > self._config.group_chat_max_participants:
            raise GroupParticipantLimitError(
                requested=len(agent_ids),
                limit=self._config.group_chat_max_participants,
            )
        identities = await self._resolve_identities(agent_ids)
        conversation = Conversation(
            id=_new_id(),
            created_by=args.created_by,
            created_at=now,
            updated_at=now,
            status=ConversationStatus.ACTIVE,
            kind=ConversationKind.GROUP,
        )
        await self._conversation_repo.save(conversation)
        await self._enrol(conversation.id, identities, args.created_by, now)
        return conversation

    async def _resolve_identities(
        self, agent_ids: list[NotBlankStr]
    ) -> list[AgentIdentity]:
        """Resolve every agent id to its identity, or fail fast.

        Returns:
            The resolved identities, in the order supplied.

        Raises:
            GroupParticipantUnknownError: A named agent is not registered.
        """
        identities: list[AgentIdentity] = []
        for agent_id in agent_ids:
            identity = await self._agent_registry.get(agent_id)
            if identity is None:
                raise GroupParticipantUnknownError(agent_id=agent_id)
            identities.append(identity)
        return identities

    async def _enrol(
        self,
        conversation_id: NotBlankStr,
        identities: list[AgentIdentity],
        added_by: NotBlankStr,
        now: datetime,
    ) -> None:
        """Persist an active participant row for each resolved identity.

        A per-index microsecond offset on ``added_at`` records enrolment
        order, so the roster query (``added_at ASC, id ASC``) walks the
        round in the order the caller listed participants rather than by
        the random participant uuid -- a batch otherwise shares one
        ``now`` and the order would be arbitrary.
        """
        for index, identity in enumerate(identities):
            await self._participant_repo.save(
                ConversationParticipant(
                    id=_new_id(),
                    conversation_id=conversation_id,
                    agent_id=NotBlankStr(str(identity.id)),
                    agent_name=identity.name,
                    participant_role=identity.role,
                    status=ConversationParticipantStatus.ACTIVE,
                    added_by=added_by,
                    added_at=now + timedelta(microseconds=index),
                )
            )
        logger.info(
            COS_GROUP_PARTICIPANTS_ADDED,
            conversation_id=conversation_id,
            count=len(identities),
        )

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
        participants = await self._active_participants(conversation.id)
        prior_turns = await self._ordered_turns(conversation.id)
        next_sequence = len(prior_turns)
        user_turn = ConversationTurn(
            id=_new_id(),
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
            contribution = await self._dispatch_contribution(
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
        )

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
    ) -> AttributedContribution | None:
        """Dispatch one participant's turn and persist its attributed turn.

        Returns:
            The attributed contribution, or ``None`` when the agent
            returned empty content (skipped, logged, surfaced).

        Raises:
            Exception: The agent dispatch failed (the round aborts; the
                lock guarantees no interleaving with a concurrent round).
        """
        prompt = self._build_prompt(history, prior_contributions)
        self._audit_authority(conversation.id, participant, prior_contributions)
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
        content = (response.content or "").strip()
        if not content:
            logger.warning(
                COS_GROUP_CONTRIBUTION_FAILED,
                conversation_id=conversation.id,
                agent_id=participant.agent_id,
                detail="empty_content",
            )
            return None
        await self._turn_repo.append(
            ConversationTurn(
                id=_new_id(),
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
        return AttributedContribution(
            agent_id=participant.agent_id,
            agent_name=participant.agent_name,
            participant_role=participant.participant_role,
            content=NotBlankStr(content),
            sequence=sequence,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )

    def _build_prompt(
        self,
        history: tuple[ConversationTurn, ...],
        prior_contributions: list[AttributedContribution],
    ) -> str:
        """Assemble the fenced contribution prompt for one participant.

        Returns:
            The formatted prompt: history + human message fenced as
            ``<task-data>``, this round's peer contributions fenced as
            ``<peer-contribution>``.
        """
        history_block = wrap_untrusted(
            TAG_TASK_DATA, self._render_group_history(history)
        )
        peer_block = wrap_untrusted(
            TAG_PEER_CONTRIBUTION, self._render_round_contributions(prior_contributions)
        )
        return GROUP_CONTRIBUTION_PROMPT.format(
            conversation_history=history_block,
            prior_contributions=peer_block,
        )

    def _audit_authority(
        self,
        conversation_id: NotBlankStr,
        participant: ConversationParticipant,
        prior_contributions: list[AttributedContribution],
    ) -> None:
        """Scan the peer-contribution block for authority cues (audit only).

        Reuses the :class:`AuthorityDeferenceGuard` pattern scan so the
        same cues the agent-middleware path logs are recorded here, where
        a later participant could otherwise defer to an earlier peer's
        claimed authority. Detection + logging only (no redaction); the
        ``<peer-contribution>`` fencing is the injection defence.
        """
        if not prior_contributions:
            return
        peer_text = self._render_round_contributions(prior_contributions)
        cue_count = self._authority_guard.scan(peer_text)
        if cue_count > 0:
            logger.info(
                COS_GROUP_AUTHORITY_CUES_DETECTED,
                conversation_id=conversation_id,
                recipient_agent_id=participant.agent_id,
                cue_count=cue_count,
            )

    @staticmethod
    def _render_group_history(turns: tuple[ConversationTurn, ...]) -> str:
        """Render attributed transcript lines for the group history.

        Returns:
            One ``Speaker: content`` line per turn (the human as
            ``Human``, each agent by its attributed name).
        """
        lines: list[str] = []
        for turn in turns:
            if turn.role is ConversationRole.USER:
                speaker = "Human"
            elif turn.role is ConversationRole.AGENT:
                speaker = turn.author_name or "Agent"
            else:
                speaker = "Assistant"
            lines.append(f"{speaker}: {turn.content}")
        return "\n".join(lines)

    @staticmethod
    def _render_round_contributions(
        contributions: list[AttributedContribution],
    ) -> str:
        """Render this round's peer contributions for the fenced block.

        Returns:
            One attributed line per contribution, or a placeholder when
            no peer has spoken yet this round.
        """
        if not contributions:
            return "(no contributions yet this round)"
        return "\n".join(
            f"{c.agent_name} ({c.participant_role}): {c.content}" for c in contributions
        )

    async def _active_participants(
        self, conversation_id: NotBlankStr
    ) -> tuple[ConversationParticipant, ...]:
        """Return the active roster for a conversation, enrolment order.

        Returns:
            Tuple of active participants, oldest-enrolled first.
        """
        return await self._participant_repo.query(
            ConversationParticipantFilterSpec(
                conversation_id=conversation_id,
                status=ConversationParticipantStatus.ACTIVE,
            )
        )

    async def _ordered_turns(
        self, conversation_id: NotBlankStr
    ) -> tuple[ConversationTurn, ...]:
        """Return all turns for a conversation, oldest-first.

        Returns:
            Tuple of turns sorted by sequence ascending.
        """
        newest_first = await self._turn_repo.query(
            ConversationTurnFilterSpec(conversation_id=conversation_id),
            limit=_MAX_TURNS_QUERY_LIMIT,
        )
        return tuple(sorted(newest_first, key=lambda turn: turn.sequence))

    @staticmethod
    def _dedupe(participants: tuple[NotBlankStr, ...]) -> list[NotBlankStr]:
        """Drop duplicate agent ids, preserving first-seen order.

        Returns:
            The de-duplicated agent ids.
        """
        seen: set[str] = set()
        result: list[NotBlankStr] = []
        for participant in participants:
            if participant not in seen:
                seen.add(participant)
                result.append(participant)
        return result

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
