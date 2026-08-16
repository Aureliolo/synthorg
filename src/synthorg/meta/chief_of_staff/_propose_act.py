"""Act-on-decision mixin for the Chief of Staff proposer.

Once a ``converse()`` turn resolves to a concrete :class:`ProposeDecision`
(not a clarifying question), this mixin carries it out by parking each
steering directive behind the approval queue. The conversational turn
pipeline that produces the decision lives in ``propose``; this mixin owns
only the act + compensation mechanics.

**This surface cannot start work.** Standing up an initiative commits the
organisation to a body of effort and a budget, so it happens one way only:
the charter interview asks until it has enough, and the operator approves
what it drafts. Steering redirects work that decision already authorised,
and is the one legitimately single-action conversational approval.
"""

from datetime import datetime

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.communication.conversation.enums import ConversationStatus
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff._intake_parking import (
    park_steering,
    unwind_parked_steering,
)
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.enums import RoutingReason
from synthorg.meta.chief_of_staff.models import (
    Conversation,
    ProposeArgs,
    ProposeDecision,
    ProposedSteering,
    ProposeResult,
    SteeringProposalSummary,
)
from synthorg.meta.chief_of_staff.responder import (
    RoutingDecision,
    build_attributed_assistant_turn,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import (
    COS_CONVERSATION_STATUS_TRANSITIONED,
    COS_PROPOSE_FAILED,
    COS_PROPOSE_PROPOSED,
)
from synthorg.persistence.conversation_protocol import (
    ConversationRepository,
    ConversationTurnRepository,
)

logger = get_logger(__name__)


def _summarise_decision(steering: tuple[ProposedSteering, ...]) -> str:
    """Multi-line assistant summary of the parked steering directives.

    Returns:
        Resulting string (a lead line plus one bullet per directive).
    """
    lines = [f"- steer ({s.kind.value}): {s.text}" for s in steering]
    # Parked, not started: these are approvals awaiting the operator, and a
    # summary claiming the work is under way describes a state the org is
    # not in and cannot reach until they decide.
    return "These are waiting for your approval:\n" + "\n".join(lines)


async def _best_effort_unwind(
    approval_store: ApprovalStoreProtocol,
    summaries: list[SteeringProposalSummary],
    conversation_id: str,
) -> None:
    """Unwind every parked steering approval, tolerating individual failures.

    A single unwind failure must not abort the remaining cleanups nor replace
    the original plan/parking exception the caller re-raises; each failure is
    logged and swallowed so compensation stays best-effort.
    """
    for parked in summaries:
        try:
            await unwind_parked_steering(approval_store, parked.approval_id)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                COS_PROPOSE_FAILED,
                conversation_id=conversation_id,
                approval_id=parked.approval_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="steering unwind failed; compensation stays best-effort",
            )


class ProposeActMixin:
    """Park the steering directives a concrete decision asked for.

    Relies on the concrete :class:`ChiefOfStaffProposer` to supply the
    approval store, conversation / turn repositories and the configuration.
    """

    _approval_store: ApprovalStoreProtocol
    _turn_repo: ConversationTurnRepository
    _conversation_repo: ConversationRepository
    _config: ChiefOfStaffConfig

    async def _act_on_decision(
        self,
        conversation: Conversation,
        args: ProposeArgs,
        *,
        decision: ProposeDecision,
        routing: RoutingDecision | None,
        routing_reason: RoutingReason,
        sequence: int,
        now: datetime,
    ) -> ProposeResult:
        """Carry out a concrete decision by parking its steering directives.

        A parking failure part-way through unwinds what already landed
        before re-raising, so a partial turn never leaves orphaned steering
        approvals with no assistant summary and no conversation transition.

        Returns:
            ``ProposeResult`` instance.

        Raises:
            Exception: When parking fails after earlier directives landed.
        """
        steering_summaries = await self._park_steering(
            conversation, args, decision, now
        )
        try:
            await self._turn_repo.append(
                build_attributed_assistant_turn(
                    conversation_id=str(conversation.id),
                    sequence=sequence,
                    content=NotBlankStr(_summarise_decision(decision.steering)),
                    routing=routing,
                    now=now,
                )
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                COS_PROPOSE_FAILED,
                conversation_id=str(conversation.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="turn append failed; unwinding parked steering",
                parked=len(steering_summaries),
            )
            await _best_effort_unwind(
                self._approval_store, steering_summaries, str(conversation.id)
            )
            raise

        await self._transition_to_proposed(conversation, now)
        logger.info(
            COS_PROPOSE_PROPOSED,
            conversation_id=str(conversation.id),
            steering_count=len(steering_summaries),
        )
        return ProposeResult(
            conversation_id=str(conversation.id),
            status="proposed",
            steering=tuple(steering_summaries),
            responder_role=routing.responder.role if routing is not None else None,
            responder_name=routing.responder.name if routing is not None else None,
            routed_topic=routing.topic if routing is not None else None,
            routing_confidence=routing.confidence if routing is not None else None,
            routing_reason=routing_reason,
        )

    async def _park_steering(
        self,
        conversation: Conversation,
        args: ProposeArgs,
        decision: ProposeDecision,
        now: datetime,
    ) -> list[SteeringProposalSummary]:
        """Park each steering directive, resolving its project first.

        Every directive's project is resolved before any park lands so an
        invalid directive raises without committing partial state; a later
        park failure unwinds the earlier ones before re-raising.

        Returns:
            The parked steering summaries.

        Raises:
            ValueError: When a steering directive resolves to no project.
            Exception: When an approval-store park fails mid-batch.
        """
        resolved: list[tuple[ProposedSteering, NotBlankStr]] = []
        for steer in decision.steering:
            project = steer.project or args.project
            if project is None:
                logger.warning(
                    COS_PROPOSE_FAILED,
                    detail="steering_missing_project",
                    conversation_id=str(conversation.id),
                )
                msg = "Steering directive resolves to no project."
                raise ValueError(msg)
            resolved.append((steer, project))

        summaries: list[SteeringProposalSummary] = []
        try:
            for steer, project in resolved:
                summaries.append(
                    await park_steering(
                        approval_store=self._approval_store,
                        conversation=conversation,
                        args=args,
                        steer=steer,
                        project=project,
                        config=self._config,
                        now=now,
                    )
                )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                COS_PROPOSE_FAILED,
                conversation_id=str(conversation.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="steering park failed mid-batch; unwinding earlier parks",
                parked=len(summaries),
            )
            await _best_effort_unwind(
                self._approval_store, summaries, str(conversation.id)
            )
            raise
        return summaries

    async def _transition_to_proposed(
        self, conversation: Conversation, now: datetime
    ) -> None:
        """CAS the conversation ACTIVE -> PROPOSED after the turn acted.

        A concurrent turn on the same conversation may already have flipped
        the status; the work of this turn still landed, so the no-op is
        surfaced rather than treated as a failure.
        """
        transitioned = await self._conversation_repo.transition_if(
            str(conversation.id),
            from_state=ConversationStatus.ACTIVE,
            to_state=ConversationStatus.PROPOSED,
            updated_at=now.isoformat(),
        )
        if transitioned:
            logger.info(
                COS_CONVERSATION_STATUS_TRANSITIONED,
                conversation_id=str(conversation.id),
                from_state=ConversationStatus.ACTIVE.value,
                to_state=ConversationStatus.PROPOSED.value,
            )
            return
        logger.warning(
            COS_PROPOSE_FAILED,
            detail="conversation_status_already_transitioned",
            conversation_id=str(conversation.id),
            from_state=ConversationStatus.ACTIVE.value,
        )
