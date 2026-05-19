"""Chief of Staff clarify-and-propose service.

Extends the explain-only chat surface with a propose+create path:
a human talks in natural language; the Chief of Staff either asks a
clarifying question or parks one or more concrete work items behind
the human approval queue. Nothing executes here -- approved items run
through the work pipeline via the approval-decision seam (still no
autonomous acting).
"""

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
        """
        now = self._clock.now()
        conversation = await self._resolve_conversation(args, now)
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
            limit=1000,
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
        await self._conversation_repo.transition_if(
            conversation.id,
            from_state=ConversationStatus.ACTIVE,
            to_state=ConversationStatus.PROPOSED,
            updated_at=now.isoformat(),
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

    async def _park_proposal(
        self,
        conversation: Conversation,
        args: ProposeArgs,
        proposed: ProposedWork,
        project: NotBlankStr,
        now: datetime,
    ) -> ProposedApprovalSummary:
        """Build the WorkItem, the approval item, and the proposal row."""
        approval_id = _new_id()
        proposal_id = _new_id()
        work_item = WorkItem(
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
        approval = ApprovalItem(
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
        await self._approval_store.add(approval)
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
        await self._conversation_repo.transition_if(
            conversation.id,
            from_state=conversation.status,
            to_state=ConversationStatus.CLOSED,
            updated_at=now.isoformat(),
        )
        logger.warning(COS_PROPOSE_CAP_REACHED, conversation_id=conversation.id)
        return ProposeResult(
            conversation_id=conversation.id,
            status="needs_clarification",
            clarifying_question=_CAP_MESSAGE,
            conversation_closed=True,
        )


__all__ = ["ChiefOfStaffProposer"]
