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

from synthorg.approval.protocol import (
    ApprovalStoreProtocol,
)
from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker import CostTracker
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import (
    ConversationalProposalStatus,
    ConversationRole,
    ConversationStatus,
)
from synthorg.core.json_parsing import extract_json_from_llm_response
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.meta.chief_of_staff._intake_parking import (
    build_work_approval_item,
    build_work_item,
    park_steering,
    unwind_parked_steering,
)
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.conversation_lock import ConversationLockRegistry
from synthorg.meta.chief_of_staff.models import (
    Conversation,
    ConversationalProposal,
    ConversationTurn,
    ProposeArgs,
    ProposedApprovalSummary,
    ProposeDecision,
    ProposedSteering,
    ProposedWork,
    ProposeResult,
    SteeringProposalSummary,
)
from synthorg.meta.chief_of_staff.prompts import CONVERSATIONAL_PROPOSE_PROMPT
from synthorg.meta.chief_of_staff.responder import (
    Responder,
    RoutingDecision,
    build_attributed_assistant_turn,
    mark_conversation_routed,
    resolve_responder_provider,
    select_responder,
)
from synthorg.meta.chief_of_staff.routing import RoleRouter
from synthorg.meta.chief_of_staff.transcript import render_turns_transcript
from synthorg.meta.errors import (
    ConversationalProposeResponseInvalidError,
    ConversationClosedError,
    ConversationNotFoundError,
)
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
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
from synthorg.persistence.conversational_proposal_protocol import (
    ConversationalProposalRepository,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider
from synthorg.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from datetime import datetime

logger = get_logger(__name__)

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
    """Return a fresh opaque identifier.

    Returns:
        ``NotBlankStr`` instance.
    """
    return NotBlankStr(str(uuid.uuid4()))


def _summarise_decision(
    proposals: tuple[ProposedWork, ...],
    steering: tuple[ProposedSteering, ...],
) -> str:
    """One-line-per-item assistant summary of parked work and steering.

    Returns:
        Resulting string.
    """
    lines = [f"- {p.title}" for p in proposals]
    lines += [f"- steer ({s.kind.value}): {s.text}" for s in steering]
    return "I've queued the following for your approval:\n" + "\n".join(lines)


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
        role_router: Optional concern router. When present, each
            turn is classified to a role agent; ``None`` keeps the v1
            generic Chief of Staff behaviour.
        provider_registry: Optional provider registry used to resolve a
            routed responder's own provider; falls back to ``provider``
            when absent. Required only when ``role_router`` is wired.
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
        role_router: RoleRouter | None = None,
        provider_registry: ProviderRegistry | None = None,
    ) -> None:
        self._provider = provider
        self._config = config
        self._conversation_repo = conversation_repo
        self._turn_repo = turn_repo
        self._proposal_repo = proposal_repo
        self._approval_store = approval_store
        self._clock: Clock = clock or SystemClock()
        self._cost_tracker = cost_tracker
        self._role_router = role_router
        self._provider_registry = provider_registry
        # Per-conversation locks serialise the whole turn pipeline
        # (resolve -> ordered_turns -> append user -> run model ->
        # append assistant + proposals -> update conversation) so two
        # concurrent ``converse()`` calls on the same conversation
        # cannot interleave their snapshots of history nor commit
        # turns the other side never saw. New conversations
        # (``args.conversation_id is None``) skip the lock because no
        # other caller can address the id before it's assigned.
        self._locks = ConversationLockRegistry()

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
        # at v1 fan-out). Locks accumulate in ``_conversation_locks``
        # over the process lifetime -- the dict cannot be safely
        # cleaned up without a refcount of in-flight + queued waiters
        # (a deletion otherwise lets a concurrent caller mint a fresh
        # lock while a queued waiter on the old one is still pending),
        # which v1 does not need at its expected scale.
        async with await self._locks.acquire_for(conversation.id):
            return await self._run_turn(conversation, args, now)

    async def _run_turn(
        self,
        conversation: Conversation,
        args: ProposeArgs,
        now: datetime,
    ) -> ProposeResult:
        """Body of one converse() turn under the conversation lock.

        Re-fetches the conversation under the lock and aborts if the
        status is no longer ACTIVE. Without this check a second
        caller that read ACTIVE in ``_resolve_conversation`` before
        the lock was free can wake up and park additional approvals
        against a now-terminal conversation (``_park_proposal`` runs
        before the ``transition_if`` no-ops, so proposals are saved
        even though the status transition fails).

        Returns:
            ``ProposeResult`` instance.

        Raises:
            ConversationClosedError: Raised on the corresponding failure path.
        """
        current = await self._conversation_repo.get(conversation.id)
        if current is None or current.status is not ConversationStatus.ACTIVE:
            logger.warning(
                COS_PROPOSE_FAILED,
                detail="conversation_terminal_under_lock",
                conversation_id=conversation.id,
                current_status=(
                    current.status.value if current is not None else "missing"
                ),
            )
            raise ConversationClosedError(conversation_id=conversation.id)
        conversation = current
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
        routing = (
            await self._role_router.route(history)
            if self._role_router is not None
            else None
        )
        responder = select_responder(routing, propose_model=self._config.propose_model)
        # Advance a still-direct conversation to ``routed`` on its first
        # routed turn so the kind discriminator reflects the surface.
        routed_conversation = mark_conversation_routed(conversation, routing)
        if routed_conversation is not None:
            conversation = routed_conversation
            await self._conversation_repo.save(conversation)
        decision = await self._run_decision(history, responder)

        if decision.needs_clarification:
            return await self._record_clarification(
                conversation, decision, routing, next_sequence + 1, now
            )
        return await self._record_proposals(
            conversation, args, decision, routing, next_sequence + 1, now
        )

    async def _resolve_conversation(
        self, args: ProposeArgs, now: datetime
    ) -> Conversation:
        """Load an existing conversation or open a fresh one.

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

        Returns:
            Tuple of the declared element types.
        """
        newest_first = await self._turn_repo.query(
            ConversationTurnFilterSpec(conversation_id=conversation_id),
            limit=_MAX_TURNS_QUERY_LIMIT,
        )
        return tuple(sorted(newest_first, key=lambda turn: turn.sequence))

    async def _run_decision(
        self, history: tuple[ConversationTurn, ...], responder: Responder
    ) -> ProposeDecision:
        """Call the model and parse its structured clarify/propose output.

        The *responder* sets the identity preamble injected into the
        prompt, the model id, and the provider; the structured-output
        discipline (temperature, token budget, JSON contract) stays the
        proposer's so routing never weakens the decision schema.

        Returns:
            ``ProposeDecision`` instance.

        Raises:
            Exception: Provider call failed.
            ConversationalProposeResponseInvalidError: Provider
                response failed validation.
        """
        prompt = CONVERSATIONAL_PROPOSE_PROMPT.format(
            responder_identity=responder.persona,
            conversation_history=wrap_untrusted(
                TAG_TASK_DATA, render_turns_transcript(history)
            ),
            max_proposals=self._config.propose_max_proposals_per_turn,
        )
        messages = [ChatMessage(role=MessageRole.USER, content=prompt)]
        completion_config = CompletionConfig(
            temperature=self._config.propose_temperature,
            max_tokens=self._config.propose_max_tokens,
        )
        provider = resolve_responder_provider(
            responder,
            default=self._provider,
            registry=self._provider_registry,
        )
        try:
            async with cost_recording_scope(
                cost_tracker=self._cost_tracker,
                agent_id=responder.agent_id or NotBlankStr("system"),
                task_id=NotBlankStr("system:cos:propose"),
                call_category=LLMCallCategory.SYSTEM,
            ):
                response = await provider.complete(
                    messages,
                    responder.model,
                    config=completion_config,
                )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(logger, COS_PROPOSE_FAILED, exc)
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
        routing: RoutingDecision | None,
        sequence: int,
        now: datetime,
    ) -> ProposeResult:
        """Persist the assistant question; conversation stays ACTIVE.

        Returns:
            ``ProposeResult`` instance.
        """
        question = decision.clarifying_question
        # ProposeDecision's validator guarantees this is set on the
        # clarification branch; assert for the type-checker only.
        assert question is not None  # noqa: S101
        await self._turn_repo.append(
            build_attributed_assistant_turn(
                conversation_id=conversation.id,
                sequence=sequence,
                content=question,
                routing=routing,
                now=now,
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
            responder_role=routing.responder.role if routing is not None else None,
            responder_name=routing.responder.name if routing is not None else None,
            routed_topic=routing.topic if routing is not None else None,
            routing_confidence=routing.confidence if routing is not None else None,
        )

    async def _record_proposals(  # noqa: PLR0913 -- one turn's full record context
        self,
        conversation: Conversation,
        args: ProposeArgs,
        decision: ProposeDecision,
        routing: RoutingDecision | None,
        sequence: int,
        now: datetime,
    ) -> ProposeResult:
        """Park each proposed work item behind one approval-queue item.

        Multi-proposal compensation: every successful ``_park_proposal``
        is tracked, and any later failure in the batch unwinds the
        earlier writes before re-raising. Without compensation, a
        partial commit leaves earlier approvals visible in the queue
        with no assistant summary turn, no conversation transition,
        and no proposal_id for the retry to dedupe against -- a
        client retry would then double-park those items.

        Returns:
            ``ProposeResult`` instance.

        Raises:
            Exception: Provider call failed.
            ConversationalProposeResponseInvalidError: Provider
                response failed validation.
        """
        # Pre-validate every proposal's project BEFORE any park lands
        # so an invalid model output raises without committing any
        # state. Pairing each proposal with its resolved project here
        # also means the park loop below cannot encounter ``None``
        # mid-flight, keeping the try/except scoped to genuine
        # persistence failures.
        resolved: list[tuple[ProposedWork, NotBlankStr]] = []
        for proposed in decision.proposals:
            project = proposed.project or args.project
            if project is None:
                logger.warning(
                    COS_PROPOSE_RESPONSE_INVALID,
                    detail="proposal_missing_project",
                    conversation_id=conversation.id,
                )
                raise ConversationalProposeResponseInvalidError
            resolved.append((proposed, project))
        resolved_steering: list[tuple[ProposedSteering, NotBlankStr]] = []
        for steer in decision.steering:
            steer_project = steer.project or args.project
            if steer_project is None:
                logger.warning(
                    COS_PROPOSE_RESPONSE_INVALID,
                    detail="steering_missing_project",
                    conversation_id=conversation.id,
                )
                raise ConversationalProposeResponseInvalidError
            resolved_steering.append((steer, steer_project))

        summaries: list[ProposedApprovalSummary] = []
        steering_summaries: list[SteeringProposalSummary] = []
        try:
            for proposed, project in resolved:
                summaries.append(
                    await self._park_proposal(
                        conversation, args, proposed, project, now
                    )
                )
            for steer, steer_project in resolved_steering:
                steering_summaries.append(
                    await park_steering(
                        approval_store=self._approval_store,
                        conversation=conversation,
                        args=args,
                        steer=steer,
                        project=steer_project,
                        config=self._config,
                        now=now,
                    )
                )
        except Exception as exc:
            reraise_critical(exc)
            for parked in summaries:
                await self._unwind_parked_proposal(
                    conversation_id=conversation.id,
                    proposal_id=parked.proposal_id,
                    approval_id=parked.approval_id,
                )
            for parked_steer in steering_summaries:
                await unwind_parked_steering(
                    self._approval_store, parked_steer.approval_id
                )
            raise

        await self._turn_repo.append(
            build_attributed_assistant_turn(
                conversation_id=conversation.id,
                sequence=sequence,
                content=NotBlankStr(
                    _summarise_decision(decision.proposals, decision.steering)
                ),
                routing=routing,
                now=now,
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
            proposal_count=len(summaries) + len(steering_summaries),
        )
        return ProposeResult(
            conversation_id=conversation.id,
            status="proposed",
            proposals=tuple(summaries),
            steering=tuple(steering_summaries),
            responder_role=routing.responder.role if routing is not None else None,
            responder_name=routing.responder.name if routing is not None else None,
            routed_topic=routing.topic if routing is not None else None,
            routing_confidence=routing.confidence if routing is not None else None,
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

        Order matters: the proposal row is written FIRST so the
        dispatcher's "approval without backing proposal" failure
        mode -- a visible dangling queue item with no work_item --
        is unreachable. The reverse order would surface as a
        dangling approval on every approval-store failure.

        Self-atomic: if the approval-store ``add`` fails after the
        proposal row was committed, the proposal row is removed
        before re-raising so the caller's compensation loop only
        needs to unwind fully-successful parks. The cleanup is
        best-effort -- the original exception is preserved even if
        the proposal delete itself fails.

        Returns:
            ``ProposedApprovalSummary`` instance.

        Raises:
            Exception: Raised on the corresponding failure path.
        """
        approval_id = _new_id()
        proposal_id = _new_id()
        work_item = build_work_item(conversation, args, proposed, project, now)
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
        try:
            await self._approval_store.add(
                build_work_approval_item(
                    approval_id=approval_id,
                    proposal_id=proposal_id,
                    conversation=conversation,
                    args=args,
                    proposed=proposed,
                    config=self._config,
                    now=now,
                )
            )
        except Exception as exc:
            reraise_critical(exc)
            try:
                await self._proposal_repo.delete(proposal_id)
            except Exception as cleanup_exc:
                reraise_critical(cleanup_exc)
                logger.warning(
                    COS_PROPOSE_FAILED,
                    detail="park_proposal_cleanup_failed",
                    conversation_id=conversation.id,
                    proposal_id=proposal_id,
                    error_type=type(cleanup_exc).__name__,
                    error=safe_error_description(cleanup_exc),
                )
            raise
        return ProposedApprovalSummary(
            approval_id=approval_id,
            proposal_id=proposal_id,
            title=proposed.title,
            task_type=proposed.task_type,
            priority=proposed.priority,
        )

    async def _unwind_parked_proposal(
        self,
        conversation_id: NotBlankStr,
        proposal_id: NotBlankStr,
        approval_id: NotBlankStr,
    ) -> None:
        """Remove a previously-parked proposal + approval pair.

        Called by ``_record_proposals`` compensation when a later
        proposal in the batch fails. Unwinds in reverse order of
        ``_park_proposal``: approval first (so no caller can see a
        dangling approval pointing at a deleted proposal), then the
        proposal row. Each step is logged but never re-raises -- the
        caller's original exception is the one operators need to see.
        """
        try:
            await self._approval_store.delete(approval_id)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                COS_PROPOSE_FAILED,
                detail="unwind_approval_failed",
                conversation_id=conversation_id,
                approval_id=approval_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        try:
            await self._proposal_repo.delete(proposal_id)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                COS_PROPOSE_FAILED,
                detail="unwind_proposal_failed",
                conversation_id=conversation_id,
                proposal_id=proposal_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _cap_conversation(
        self,
        conversation: Conversation,
        sequence: int,
        now: datetime,
    ) -> ProposeResult:
        """Force-close a conversation that will not converge.

        Returns:
            ``ProposeResult`` instance.
        """
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
