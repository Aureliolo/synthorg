"""Proposal parking + compensation mixin for the Chief of Staff proposer.

Records the proposed work + steering items for one converse() turn,
parking each behind the human approval queue with multi-proposal
compensation: a later failure in the batch unwinds the earlier writes
before re-raising. The conversational turn pipeline that produces a
:class:`ProposeDecision` lives in ``propose``; this mixin owns only the
park / unwind mechanics.
"""

import uuid
from datetime import datetime

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.communication.conversation.enums import (
    ConversationalProposalStatus,
    ConversationStatus,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff._intake_parking import (
    build_work_approval_item,
    build_work_item,
    park_steering,
    unwind_parked_steering,
)
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.models import (
    Conversation,
    ConversationalProposal,
    ProposeArgs,
    ProposedApprovalSummary,
    ProposeDecision,
    ProposedSteering,
    ProposedWork,
    ProposeResult,
    SteeringProposalSummary,
)
from synthorg.meta.chief_of_staff.responder import (
    RoutingDecision,
    build_attributed_assistant_turn,
)
from synthorg.meta.errors import ConversationalProposeResponseInvalidError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import (
    COS_CONVERSATION_STATUS_TRANSITIONED,
    COS_PROPOSE_FAILED,
    COS_PROPOSE_PROPOSED,
    COS_PROPOSE_RESPONSE_INVALID,
)
from synthorg.persistence.conversation_protocol import (
    ConversationRepository,
    ConversationTurnRepository,
)
from synthorg.persistence.conversational_proposal_protocol import (
    ConversationalProposalRepository,
)

logger = get_logger(__name__)


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


class ProposeParkingMixin:
    """Park / unwind proposed work + steering behind the approval queue.

    Relies on the concrete :class:`ChiefOfStaffProposer` to supply the
    approval store, proposal / conversation / turn repositories, and
    the configuration.
    """

    _approval_store: ApprovalStoreProtocol
    _proposal_repo: ConversationalProposalRepository
    _turn_repo: ConversationTurnRepository
    _conversation_repo: ConversationRepository
    _config: ChiefOfStaffConfig

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
                    conversation_id=NotBlankStr(str(conversation.id)),
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
                conversation_id=NotBlankStr(str(conversation.id)),
                sequence=sequence,
                content=NotBlankStr(
                    _summarise_decision(decision.proposals, decision.steering)
                ),
                routing=routing,
                now=now,
            )
        )
        transitioned = await self._conversation_repo.transition_if(
            NotBlankStr(str(conversation.id)),
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
            conversation_id=NotBlankStr(str(conversation.id)),
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
        work_item = build_work_item(conversation, args, proposed, project, now)
        proposal = ConversationalProposal(
            conversation_id=NotBlankStr(str(conversation.id)),
            approval_id=approval_id,
            work_item_json=NotBlankStr(work_item.model_dump_json()),
            status=ConversationalProposalStatus.PENDING,
            created_at=now,
        )
        proposal_id = NotBlankStr(str(proposal.id))
        await self._proposal_repo.save(proposal)
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
