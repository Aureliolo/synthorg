"""Chief of Staff clarify-and-propose service.

Extends the explain-only chat surface with a propose+create path:
a human talks in natural language; the Chief of Staff either asks a
clarifying question or parks one or more concrete work items behind
the human approval queue. Nothing executes here -- approved items run
through the work pipeline via the approval-decision seam (still no
autonomous acting).
"""

import asyncio
import uuid
from typing import TYPE_CHECKING

from pydantic import ValidationError

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker import CostTracker  # noqa: TC001
from synthorg.core.approval import ApprovalItem
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.enums import (
    ApprovalSource,
    ApprovalStatus,
    ConversationalProposalStatus,
    ConversationRole,
    ConversationStatus,
)
from synthorg.core.json_parsing import extract_json_from_llm_response
from synthorg.core.types import NotBlankStr
from synthorg.engine.pipeline.models import WorkItem, WorkSource
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig  # noqa: TC001
from synthorg.meta.chief_of_staff.models import (
    Conversation,
    ConversationalProposal,
    ConversationTurn,
    ProposeArgs,
    ProposedApprovalSummary,
    ProposeDecision,
    ProposedWork,
    ProposeResult,
)
from synthorg.meta.chief_of_staff.prompts import CONVERSATIONAL_PROPOSE_PROMPT
from synthorg.meta.errors import (
    ConversationalProposeResponseInvalidError,
    ConversationClosedError,
    ConversationNotFoundError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import (
    COS_CONVERSATION_STATUS_TRANSITIONED,
    COS_PROPOSE_CAP_REACHED,
    COS_PROPOSE_CLARIFICATION,
    COS_PROPOSE_FAILED,
    COS_PROPOSE_PROPOSED,
    COS_PROPOSE_RESPONSE_INVALID,
    COS_PROPOSE_TURN,
)
from synthorg.persistence.conversation_protocol import (
    ConversationRepository,
    ConversationTurnFilterSpec,
    ConversationTurnRepository,
)
from synthorg.persistence.conversational_proposal_protocol import (  # noqa: TC001
    ConversationalProposalRepository,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider  # noqa: TC001

from synthorg.approval.protocol import (  # noqa: TC001  isort: skip
    ApprovalStoreProtocol,
)

if TYPE_CHECKING:
    from datetime import datetime

logger = get_logger(__name__)

_ACTION_TYPE: NotBlankStr = NotBlankStr("conversational:create_work")
_ORIGIN_ADAPTER_ID: NotBlankStr = NotBlankStr("conversational-cos")
_CAP_MESSAGE: NotBlankStr = NotBlankStr(
    "We have gone back and forth several times without converging on "
    "actionable work. Closing this conversation -- please open a new "
    "one with a more specific request.",
)
# 1:1 conversational threads are short by design (max_clarification_turns
# is bounded at 20 above). 1000 is a generous ceiling that gives every
# turn-rendering caller the full history without forcing pagination; the
# repo's own ``_MAX_PAGE_LIMIT`` (1000) clamps anything larger anyway.
_MAX_TURNS_QUERY_LIMIT: int = 1000


def _new_id() -> NotBlankStr:
    """Return a fresh opaque identifier."""
    return NotBlankStr(str(uuid.uuid4()))


def _render_history(turns: tuple[ConversationTurn, ...]) -> str:
    """Render chronological turns into a prompt-ready transcript."""
    return "\n".join(f"{turn.role.value.upper()}: {turn.content}" for turn in turns)


def _summarise_proposals(proposals: tuple[ProposedWork, ...]) -> str:
    """One-line-per-item assistant summary of parked proposals."""
    lines = [f"- {p.title}" for p in proposals]
    return "I've queued the following work for your approval:\n" + "\n".join(lines)


class ChiefOfStaffProposer:
    """Clarify-or-propose conversational service.

    Single responsibility (keeps ``ChiefOfStaffChat`` explain-only).
    Each :meth:`converse` call appends the human turn, runs one
    structured LLM turn, and either records a clarifying question or
    parks concrete work items behind the approval queue.

    Args:
        provider: LLM completion provider.
        config: Chief of Staff configuration.
        conversation_repo: Conversation header store.
        turn_repo: Append-only conversation turn store.
        proposal_repo: Conversational proposal store.
        approval_store: Human approval queue.
        clock: Injectable time source (defaults to ``SystemClock``).
        cost_tracker: Optional cost tracker for LLM accounting.
    """

    def __init__(  # noqa: PLR0913 -- DI seam: independently-wired protocols
        self,
        *,
        provider: CompletionProvider,
        config: ChiefOfStaffConfig,
        conversation_repo: ConversationRepository,
        turn_repo: ConversationTurnRepository,
        proposal_repo: ConversationalProposalRepository,
        approval_store: ApprovalStoreProtocol,
        clock: Clock | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self._provider = provider
        self._config = config
        self._conversation_repo = conversation_repo
        self._turn_repo = turn_repo
        self._proposal_repo = proposal_repo
        self._approval_store = approval_store
        self._clock: Clock = clock or SystemClock()
        self._cost_tracker = cost_tracker
        # Per-conversation locks serialise the whole turn pipeline
        # (resolve -> ordered_turns -> append user -> run model ->
        # append assistant + proposals -> update conversation) so two
        # concurrent ``converse()`` calls on the same conversation
        # cannot interleave their snapshots of history nor commit
        # turns the other side never saw. New conversations
        # (``args.conversation_id is None``) skip the lock because no
        # other caller can address the id before it's assigned.
        # ``_conversation_locks_guard`` is lazy-initialised on first
        # use so the lock binds to the request-handling event loop
        # rather than whichever loop ran ``__init__``.
        self._conversation_locks: dict[str, asyncio.Lock] = {}
        self._conversation_locks_guard: asyncio.Lock | None = None

    async def _lock_for(self, conversation_id: str) -> asyncio.Lock:
        """Return the asyncio.Lock for *conversation_id*, creating it once.

        Lazy-initialises ``_conversation_locks_guard`` so the guard
        lock binds to the live request-handling loop rather than the
        loop that built the proposer. Subsequent callers re-use the
        same per-conversation lock instance through the guarded
        dict lookup; a tight race on first guard-init merely wastes
        a Lock() instance (the loser drops its instance after
        observing the populated guard).
        """
        if self._conversation_locks_guard is None:
            self._conversation_locks_guard = asyncio.Lock()
        async with self._conversation_locks_guard:
            lock = self._conversation_locks.get(conversation_id)
            if lock is None:
                lock = asyncio.Lock()
                self._conversation_locks[conversation_id] = lock
            return lock

    async def converse(self, args: ProposeArgs) -> ProposeResult:
        """Run one clarify-or-propose turn.

        Args:
            args: The turn input (message, owner, optional conversation
                id, optional project).

        Returns:
            The turn outcome: a clarifying question or parked proposals.

        Raises:
            ConversationNotFoundError: ``conversation_id`` is unknown.
            ConversationClosedError: The conversation is terminal.
            ConversationalProposeResponseInvalidError: The model output
                did not satisfy the structured contract.

        Note:
            The user turn is appended before the LLM call, the
            assistant turn after. A request cancelled between the two
            therefore leaves the conversation with an unanswered user
            turn (no data corruption; the next turn picks up where
            the cancelled one left off). Atomic two-turn append would
            require a bespoke ``append_pair`` on the turn repo (ADR-0001
            D7) and is deferred to a follow-up when cancellation rate
            warrants it.
        """
        now = self._clock.now()
        conversation = await self._resolve_conversation(args, now)
        # Serialise the turn pipeline per conversation so concurrent
        # converse() calls cannot snapshot the same prior_turns and
        # then commit assistant turns the other side never saw.
        # The lock is held across the LLM call -- this is intentional
        # for v1's 1:1 conversational interface where turns must stay
        # linear; the LLM round-trip is the natural pacing unit and
        # the contention window is small (one user per conversation
        # at v1 fan-out).
        async with await self._lock_for(conversation.id):
            return await self._run_turn(conversation, args, now)

    async def _run_turn(
        self,
        conversation: Conversation,
        args: ProposeArgs,
        now: datetime,
    ) -> ProposeResult:
        """Body of one converse() turn under the conversation lock."""
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
            COS_PROPOSE_TURN,
            conversation_id=conversation.id,
            sequence=next_sequence,
        )

        assistant_turns = sum(
            1 for t in prior_turns if t.role is ConversationRole.ASSISTANT
        )
        if assistant_turns >= self._config.propose_max_clarification_turns:
            return await self._cap_conversation(conversation, next_sequence + 1, now)

        history = (*prior_turns, user_turn)
        decision = await self._run_decision(history)

        if decision.needs_clarification:
            return await self._record_clarification(
                conversation, decision, next_sequence + 1, now
            )
        return await self._record_proposals(
            conversation, args, decision, next_sequence + 1, now
        )

    async def _resolve_conversation(
        self, args: ProposeArgs, now: datetime
    ) -> Conversation:
        """Load an existing conversation or open a fresh one."""
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
        if existing is None:
            raise ConversationNotFoundError(conversation_id=args.conversation_id)
        # Authorisation: only the original creator may resume a
        # conversation. A caller who learns a foreign conversation_id
        # would otherwise be able to append turns to it and have prior
        # history fed back into the model -- a cross-tenant privacy
        # break. Map ownership mismatch to NotFound so the response
        # cannot be used to probe existence either.
        if existing.created_by != args.created_by:
            raise ConversationNotFoundError(conversation_id=args.conversation_id)
        if existing.status is ConversationStatus.CLOSED:
            raise ConversationClosedError(conversation_id=existing.id)
        return existing

    async def _ordered_turns(
        self, conversation_id: NotBlankStr
    ) -> tuple[ConversationTurn, ...]:
        """Return all turns for a conversation, oldest-first.

        The append-only store yields newest-first; v1 1:1 threads are
        short, so reversing the bounded result in memory is the
        chronological order the prompt needs.
        """
        newest_first = await self._turn_repo.query(
            ConversationTurnFilterSpec(conversation_id=conversation_id),
            limit=_MAX_TURNS_QUERY_LIMIT,
        )
        return tuple(sorted(newest_first, key=lambda turn: turn.sequence))

    async def _run_decision(
        self, history: tuple[ConversationTurn, ...]
    ) -> ProposeDecision:
        """Call the model and parse its structured clarify/propose output."""
        prompt = CONVERSATIONAL_PROPOSE_PROMPT.format(
            conversation_history=wrap_untrusted(
                TAG_TASK_DATA, _render_history(history)
            ),
            max_proposals=self._config.propose_max_proposals_per_turn,
        )
        messages = [ChatMessage(role=MessageRole.USER, content=prompt)]
        completion_config = CompletionConfig(
            temperature=self._config.propose_temperature,
            max_tokens=self._config.propose_max_tokens,
        )
        try:
            async with cost_recording_scope(
                cost_tracker=self._cost_tracker,
                agent_id=NotBlankStr("system"),
                task_id=NotBlankStr("system:cos:propose"),
                call_category=LLMCallCategory.SYSTEM,
            ):
                response = await self._provider.complete(
                    messages,
                    self._config.propose_model,
                    config=completion_config,
                )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.error(
                COS_PROPOSE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        raw = (response.content or "").strip()
        parsed = extract_json_from_llm_response(
            raw,
            logger_callback=lambda detail: logger.warning(
                COS_PROPOSE_RESPONSE_INVALID, detail=detail
            ),
        )
        if parsed is None:
            raise ConversationalProposeResponseInvalidError
        try:
            return ProposeDecision.model_validate(parsed)
        except ValidationError as exc:
            logger.warning(
                COS_PROPOSE_RESPONSE_INVALID,
                detail="schema_validation_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConversationalProposeResponseInvalidError from exc

    async def _record_clarification(
        self,
        conversation: Conversation,
        decision: ProposeDecision,
        sequence: int,
        now: datetime,
    ) -> ProposeResult:
        """Persist the assistant question; conversation stays ACTIVE."""
        question = decision.clarifying_question
        # ProposeDecision's validator guarantees this is set on the
        # clarification branch; assert for the type-checker only.
        assert question is not None  # noqa: S101
        await self._turn_repo.append(
            ConversationTurn(
                id=_new_id(),
                conversation_id=conversation.id,
                sequence=sequence,
                role=ConversationRole.ASSISTANT,
                content=question,
                created_at=now,
            )
        )
        await self._conversation_repo.save(
            conversation.model_copy(update={"updated_at": now})
        )
        logger.info(COS_PROPOSE_CLARIFICATION, conversation_id=conversation.id)
        return ProposeResult(
            conversation_id=conversation.id,
            status="needs_clarification",
            clarifying_question=question,
        )

    async def _record_proposals(
        self,
        conversation: Conversation,
        args: ProposeArgs,
        decision: ProposeDecision,
        sequence: int,
        now: datetime,
    ) -> ProposeResult:
        """Park each proposed work item behind one approval-queue item."""
        summaries: list[ProposedApprovalSummary] = []
        for proposed in decision.proposals:
            project = proposed.project or args.project
            if project is None:
                logger.warning(
                    COS_PROPOSE_RESPONSE_INVALID,
                    detail="proposal_missing_project",
                    conversation_id=conversation.id,
                )
                raise ConversationalProposeResponseInvalidError
            summaries.append(
                await self._park_proposal(conversation, args, proposed, project, now)
            )

        await self._turn_repo.append(
            ConversationTurn(
                id=_new_id(),
                conversation_id=conversation.id,
                sequence=sequence,
                role=ConversationRole.ASSISTANT,
                content=NotBlankStr(_summarise_proposals(decision.proposals)),
                created_at=now,
            )
        )
        transitioned = await self._conversation_repo.transition_if(
            conversation.id,
            from_state=ConversationStatus.ACTIVE,
            to_state=ConversationStatus.PROPOSED,
            updated_at=now.isoformat(),
        )
        if transitioned:
            logger.info(
                COS_CONVERSATION_STATUS_TRANSITIONED,
                conversation_id=conversation.id,
                from_state=ConversationStatus.ACTIVE.value,
                to_state=ConversationStatus.PROPOSED.value,
            )
        else:
            # A concurrent propose-turn on this same conversation already
            # flipped the status; the proposals from THIS call still
            # landed (parked in the approval queue), so the conversation
            # is consistent. Surface the no-op so an operator can spot
            # cross-talk if it happens.
            logger.warning(
                COS_PROPOSE_FAILED,
                detail="conversation_status_already_transitioned",
                conversation_id=conversation.id,
                from_state=ConversationStatus.ACTIVE.value,
            )
        logger.info(
            COS_PROPOSE_PROPOSED,
            conversation_id=conversation.id,
            proposal_count=len(summaries),
        )
        return ProposeResult(
            conversation_id=conversation.id,
            status="proposed",
            proposals=tuple(summaries),
        )

    def _build_work_item(
        self,
        conversation: Conversation,
        args: ProposeArgs,
        proposed: ProposedWork,
        project: NotBlankStr,
        now: datetime,
    ) -> WorkItem:
        """Compose the pipeline-spine envelope for one proposal."""
        return WorkItem(
            origin_adapter_id=_ORIGIN_ADAPTER_ID,
            source=WorkSource.CONVERSATIONAL,
            title=proposed.title,
            raw_intent=proposed.raw_intent,
            project=project,
            requested_by=args.created_by,
            priority=proposed.priority,
            task_type=proposed.task_type,
            estimated_complexity=proposed.estimated_complexity,
            acceptance_criteria=proposed.acceptance_criteria,
            correlation_id=conversation.id,
            created_at=now,
        )

    def _build_approval_item(  # noqa: PLR0913 -- ApprovalItem field set is broad by design
        self,
        *,
        approval_id: NotBlankStr,
        proposal_id: NotBlankStr,
        conversation: Conversation,
        args: ProposeArgs,
        proposed: ProposedWork,
        now: datetime,
    ) -> ApprovalItem:
        """Compose the parked approval-queue item for one proposal."""
        return ApprovalItem(
            id=approval_id,
            action_type=_ACTION_TYPE,
            title=proposed.title,
            description=proposed.raw_intent,
            requested_by=args.created_by,
            risk_level=self._config.propose_default_risk_level,
            source=ApprovalSource.CONVERSATIONAL_INTAKE,
            status=ApprovalStatus.PENDING,
            created_at=now,
            metadata={
                "conversation_id": conversation.id,
                "proposal_id": proposal_id,
            },
        )

    async def _park_proposal(
        self,
        conversation: Conversation,
        args: ProposeArgs,
        proposed: ProposedWork,
        project: NotBlankStr,
        now: datetime,
    ) -> ProposedApprovalSummary:
        """Persist the proposal, then publish the gating approval.

        Order matters: the proposal row is written FIRST so an
        ``approval_store.add`` failure leaves at most an invisible
        orphan proposal (the dispatcher gracefully skips a missing
        approval). The reverse order would surface as a visible
        dangling queue item with no backing WorkItem.
        """
        approval_id = _new_id()
        proposal_id = _new_id()
        work_item = self._build_work_item(conversation, args, proposed, project, now)
        await self._proposal_repo.save(
            ConversationalProposal(
                id=proposal_id,
                conversation_id=conversation.id,
                approval_id=approval_id,
                work_item_json=NotBlankStr(work_item.model_dump_json()),
                status=ConversationalProposalStatus.PENDING,
                created_at=now,
            )
        )
        await self._approval_store.add(
            self._build_approval_item(
                approval_id=approval_id,
                proposal_id=proposal_id,
                conversation=conversation,
                args=args,
                proposed=proposed,
                now=now,
            )
        )
        return ProposedApprovalSummary(
            approval_id=approval_id,
            proposal_id=proposal_id,
            title=proposed.title,
            task_type=proposed.task_type,
            priority=proposed.priority,
        )

    async def _cap_conversation(
        self,
        conversation: Conversation,
        sequence: int,
        now: datetime,
    ) -> ProposeResult:
        """Force-close a conversation that will not converge."""
        await self._turn_repo.append(
            ConversationTurn(
                id=_new_id(),
                conversation_id=conversation.id,
                sequence=sequence,
                role=ConversationRole.ASSISTANT,
                content=_CAP_MESSAGE,
                created_at=now,
            )
        )
        transitioned = await self._conversation_repo.transition_if(
            conversation.id,
            from_state=conversation.status,
            to_state=ConversationStatus.CLOSED,
            updated_at=now.isoformat(),
        )
        if transitioned:
            logger.info(
                COS_CONVERSATION_STATUS_TRANSITIONED,
                conversation_id=conversation.id,
                from_state=conversation.status.value,
                to_state=ConversationStatus.CLOSED.value,
            )
        logger.warning(COS_PROPOSE_CAP_REACHED, conversation_id=conversation.id)
        return ProposeResult(
            conversation_id=conversation.id,
            status="needs_clarification",
            clarifying_question=_CAP_MESSAGE,
            conversation_closed=True,
        )


__all__ = ["ChiefOfStaffProposer"]
