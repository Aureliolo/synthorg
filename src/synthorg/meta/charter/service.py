"""Deep CEO interview to project charter orchestration service.

Drives a multi-turn requirements-elicitation interview over the Chief
of Staff conversation substrate (``Conversation`` + ``ConversationTurn``)
and produces a single reviewable :class:`ProjectCharter` per
conversation. Each turn either records an elicitation question or
persists / updates the charter draft. Nothing executes here: an
approved charter is dispatched into the work pipeline by
``CharterDispatcher`` via the dedicated approve endpoint.
"""

import asyncio
import uuid
from datetime import datetime

from synthorg.communication.conversation.enums import (
    ConversationRole,
    ConversationStatus,
)
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.types import NotBlankStr
from synthorg.meta.charter._charter_crud import CharterCrudMixin
from synthorg.meta.charter.config import CharterConfig
from synthorg.meta.charter.enums import CharterStatus
from synthorg.meta.charter.models import (
    CharterDraft,
    InterviewTurnArgs,
    InterviewTurnResult,
    ProjectCharter,
)
from synthorg.meta.charter.strategy import CharterInterviewStrategy
from synthorg.meta.chief_of_staff.models import Conversation, ConversationTurn
from synthorg.meta.errors import (
    ConversationClosedError,
    ConversationNotFoundError,
)
from synthorg.observability import get_logger
from synthorg.observability.events.charter import (
    CHARTER_INTERVIEW_CAP_REACHED,
    CHARTER_INTERVIEW_DRAFTED,
    CHARTER_INTERVIEW_QUESTION,
    CHARTER_INTERVIEW_TURN,
    CHARTER_STATUS_TRANSITIONED,
)
from synthorg.persistence.charter_protocol import CharterFilterSpec, CharterRepository
from synthorg.persistence.conversation_protocol import (
    ConversationRepository,
    ConversationTurnFilterSpec,
    ConversationTurnRepository,
)

logger = get_logger(__name__)

_MAX_TURNS_QUERY_LIMIT: int = 1000
_CAP_MESSAGE: NotBlankStr = NotBlankStr(
    "We have explored this idea over many turns without converging on a"
    " charter. Closing this interview -- please open a new one with a"
    " sharper starting brief."
)


def _new_id() -> NotBlankStr:
    """Return a fresh opaque identifier.

    Returns:
        ``NotBlankStr`` instance.
    """
    return NotBlankStr(str(uuid.uuid4()))


def _summarise_draft(draft: CharterDraft) -> NotBlankStr:
    """One-line assistant summary acknowledging a drafted charter.

    Returns:
        ``NotBlankStr`` instance.
    """
    return NotBlankStr(
        f"I've drafted the project charter '{draft.title}'. Review and edit"
        " it, then approve to start the project run."
    )


class CharterInterviewService(CharterCrudMixin):
    """Multi-turn charter interview orchestrator.

    Args:
        strategy: Pluggable interview strategy (one model turn).
        config: Charter-interview configuration.
        conversation_repo: Conversation header store.
        turn_repo: Append-only conversation turn store.
        charter_repo: Project charter store.
        clock: Injectable time source (defaults to ``SystemClock``).
    """

    def __init__(  # noqa: PLR0913 -- DI seam: independently-wired collaborators
        self,
        *,
        strategy: CharterInterviewStrategy,
        config: CharterConfig,
        conversation_repo: ConversationRepository,
        turn_repo: ConversationTurnRepository,
        charter_repo: CharterRepository,
        clock: Clock | None = None,
    ) -> None:
        self._strategy = strategy
        self._config = config
        self._conversation_repo = conversation_repo
        self._turn_repo = turn_repo
        self._charter_repo = charter_repo
        self._clock: Clock = clock or SystemClock()
        # Per-conversation locks serialise the turn pipeline so two
        # concurrent run_turn() calls on one conversation cannot
        # interleave their history snapshots nor double-create charters.
        # Lazy-initialised so the guard binds to the request loop.
        self._conversation_locks: dict[str, asyncio.Lock] = {}
        self._conversation_locks_guard: asyncio.Lock | None = None

    async def _lock_for(self, conversation_id: str) -> asyncio.Lock:
        """Return the per-conversation lock, creating it once.

        Returns:
            ``asyncio.Lock`` instance.
        """
        if self._conversation_locks_guard is None:
            self._conversation_locks_guard = asyncio.Lock()
        async with self._conversation_locks_guard:
            lock = self._conversation_locks.get(conversation_id)
            if lock is None:
                lock = asyncio.Lock()
                self._conversation_locks[conversation_id] = lock
            return lock

    async def run_turn(self, args: InterviewTurnArgs) -> InterviewTurnResult:
        """Run one interview turn (elicit a question or draft the charter).

        Raises:
            ConversationNotFoundError: ``conversation_id`` is unknown.
            ConversationClosedError: The conversation is terminal.
            CharterInterviewResponseInvalidError: The model output did
                not satisfy the structured contract.

        Returns:
            ``InterviewTurnResult`` instance.
        """
        now = self._clock.now()
        conversation = await self._resolve_conversation(args, now)
        async with await self._lock_for(conversation.id):
            return await self._run_turn(conversation, args, now)

    async def _run_turn(
        self,
        conversation: Conversation,
        args: InterviewTurnArgs,
        now: datetime,
    ) -> InterviewTurnResult:
        """Body of one interview turn under the conversation lock.

        Returns:
            ``InterviewTurnResult`` instance.

        Raises:
            ConversationClosedError: Raised on the corresponding failure path.
        """
        current = await self._conversation_repo.get(conversation.id)
        if current is None or current.status is not ConversationStatus.ACTIVE:
            raise ConversationClosedError(conversation_id=conversation.id)
        conversation = current
        prior_turns = await self._ordered_turns(conversation.id)
        next_sequence = len(prior_turns)

        await self._append_turn(
            conversation.id, next_sequence, ConversationRole.USER, args.message, now
        )
        logger.info(
            CHARTER_INTERVIEW_TURN,
            conversation_id=conversation.id,
            sequence=next_sequence,
        )

        assistant_turns = sum(
            1 for t in prior_turns if t.role is ConversationRole.ASSISTANT
        )
        if assistant_turns >= self._config.interview_max_turns:
            return await self._cap_conversation(conversation, next_sequence + 1, now)

        history = (
            *prior_turns,
            self._build_turn(
                conversation.id, next_sequence, ConversationRole.USER, args.message, now
            ),
        )
        decision = await self._strategy.run_turn(
            history,
            project_id=args.project,
            currency=self._config.default_currency,
        )
        if decision.needs_more:
            assert decision.next_question is not None  # noqa: S101 -- validator-guaranteed
            return await self._record_question(
                conversation, decision.next_question, next_sequence + 1, now
            )
        assert decision.draft is not None  # noqa: S101 -- validator-guaranteed
        return await self._record_draft(
            conversation, decision.draft, next_sequence + 1, now
        )

    async def _resolve_conversation(
        self, args: InterviewTurnArgs, now: datetime
    ) -> Conversation:
        """Load an existing conversation or open a fresh interview.

        Returns:
            ``Conversation`` instance.

        Raises:
            ConversationNotFoundError: Raised on the corresponding failure path.
            ConversationClosedError: Raised on the corresponding failure path.
        """
        if args.conversation_id is None:
            conversation = Conversation(
                id=_new_id(),
                created_by=args.created_by,
                created_at=now,
                updated_at=now,
                status=ConversationStatus.ACTIVE,
            )
            await self._conversation_repo.save(conversation)
            return conversation
        existing = await self._conversation_repo.get(args.conversation_id)
        if existing is None or existing.created_by != args.created_by:
            # Map ownership mismatch to NotFound so the response cannot
            # be used to probe a foreign conversation's existence.
            raise ConversationNotFoundError(conversation_id=args.conversation_id)
        if existing.status is ConversationStatus.CLOSED:
            raise ConversationClosedError(conversation_id=existing.id)
        return existing

    async def _ordered_turns(
        self, conversation_id: NotBlankStr
    ) -> tuple[ConversationTurn, ...]:
        """Return all turns for a conversation, oldest-first.

        Returns:
            Tuple of the declared element types.
        """
        newest_first = await self._turn_repo.query(
            ConversationTurnFilterSpec(conversation_id=conversation_id),
            limit=_MAX_TURNS_QUERY_LIMIT,
        )
        return tuple(sorted(newest_first, key=lambda turn: turn.sequence))

    def _build_turn(
        self,
        conversation_id: NotBlankStr,
        sequence: int,
        role: ConversationRole,
        content: NotBlankStr,
        now: datetime,
    ) -> ConversationTurn:
        """Construct a conversation turn (not persisted).

        Returns:
            ``ConversationTurn`` instance.
        """
        return ConversationTurn(
            id=_new_id(),
            conversation_id=conversation_id,
            sequence=sequence,
            role=role,
            content=content,
            created_at=now,
        )

    async def _append_turn(
        self,
        conversation_id: NotBlankStr,
        sequence: int,
        role: ConversationRole,
        content: NotBlankStr,
        now: datetime,
    ) -> None:
        """Append one turn to the append-only turn store."""
        await self._turn_repo.append(
            self._build_turn(conversation_id, sequence, role, content, now)
        )

    async def _record_question(
        self,
        conversation: Conversation,
        question: NotBlankStr,
        sequence: int,
        now: datetime,
    ) -> InterviewTurnResult:
        """Persist the assistant question; conversation stays ACTIVE.

        Returns:
            ``InterviewTurnResult`` instance.
        """
        await self._append_turn(
            conversation.id, sequence, ConversationRole.ASSISTANT, question, now
        )
        await self._conversation_repo.save(
            conversation.model_copy(update={"updated_at": now})
        )
        logger.info(CHARTER_INTERVIEW_QUESTION, conversation_id=conversation.id)
        return InterviewTurnResult(
            conversation_id=conversation.id,
            status="needs_more",
            next_question=question,
        )

    async def _record_draft(
        self,
        conversation: Conversation,
        draft: CharterDraft,
        sequence: int,
        now: datetime,
    ) -> InterviewTurnResult:
        """Persist (or update) the single charter for this conversation.

        Returns:
            ``InterviewTurnResult`` instance.
        """
        existing = await self._existing_charter(conversation.id)
        charter = self._charter_from_draft(conversation, draft, existing, now)
        await self._charter_repo.save(charter)
        await self._append_turn(
            conversation.id,
            sequence,
            ConversationRole.ASSISTANT,
            _summarise_draft(draft),
            now,
        )
        await self._conversation_repo.save(
            conversation.model_copy(update={"updated_at": now})
        )
        logger.info(
            CHARTER_INTERVIEW_DRAFTED,
            conversation_id=conversation.id,
            charter_id=charter.id,
            version=charter.version,
        )
        return InterviewTurnResult(
            conversation_id=conversation.id,
            status="drafted",
            charter=charter,
        )

    async def _existing_charter(
        self, conversation_id: NotBlankStr
    ) -> ProjectCharter | None:
        """Return the conversation's DRAFTED charter, if one exists.

        Returns:
            The ``ProjectCharter`` value when present, ``None`` otherwise.
        """
        rows = await self._charter_repo.query(
            CharterFilterSpec(
                conversation_id=conversation_id, status=CharterStatus.DRAFTED
            ),
            limit=1,
        )
        return rows[0] if rows else None

    def _charter_from_draft(
        self,
        conversation: Conversation,
        draft: CharterDraft,
        existing: ProjectCharter | None,
        now: datetime,
    ) -> ProjectCharter:
        """Mint a new charter or bump the existing draft in place.

        Returns:
            ``ProjectCharter`` instance.
        """
        charter_id = existing.id if existing is not None else _new_id()
        version = existing.version + 1 if existing is not None else 1
        created_at = existing.created_at if existing is not None else now
        return ProjectCharter(
            id=charter_id,
            conversation_id=conversation.id,
            created_by=conversation.created_by,
            version=version,
            status=CharterStatus.DRAFTED,
            title=draft.title,
            brief=draft.brief,
            goals=draft.goals,
            constraints=draft.constraints,
            success_criteria=draft.success_criteria,
            scope=draft.scope,
            envelope=draft.envelope,
            project_id=draft.project_id,
            proposed_project_name=draft.proposed_project_name,
            proposed_project_description=draft.proposed_project_description,
            created_at=created_at,
            updated_at=now,
        )

    async def _cap_conversation(
        self,
        conversation: Conversation,
        sequence: int,
        now: datetime,
    ) -> InterviewTurnResult:
        """Force-close an interview that will not converge.

        Returns:
            ``InterviewTurnResult`` instance.
        """
        await self._append_turn(
            conversation.id, sequence, ConversationRole.ASSISTANT, _CAP_MESSAGE, now
        )
        transitioned = await self._conversation_repo.transition_if(
            conversation.id,
            from_state=conversation.status,
            to_state=ConversationStatus.CLOSED,
            updated_at=now.isoformat(),
        )
        if transitioned:
            logger.info(
                CHARTER_STATUS_TRANSITIONED,
                conversation_id=conversation.id,
                from_state=conversation.status.value,
                to_state=ConversationStatus.CLOSED.value,
            )
        logger.warning(CHARTER_INTERVIEW_CAP_REACHED, conversation_id=conversation.id)
        return InterviewTurnResult(
            conversation_id=conversation.id,
            status="needs_more",
            next_question=_CAP_MESSAGE,
            conversation_closed=True,
        )


__all__ = ["CharterInterviewService"]
