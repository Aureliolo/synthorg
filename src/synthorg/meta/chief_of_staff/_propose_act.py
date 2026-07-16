"""Act-on-decision mixin for the Chief of Staff proposer.

Once a ``converse()`` turn resolves to a concrete :class:`ProposeDecision`
(not a clarifying question), this mixin carries it out: it drafts a plan
for the single work brief (handed to the plan-review spine via the
:class:`ConversationalPlanDispatcher`) and/or parks each steering
directive behind the approval queue. The conversational turn pipeline
that produces the decision lives in ``propose``; this mixin owns only the
act + compensation mechanics.

A work brief is never fragmented into per-item approvals: it becomes one
objective whose owner decomposes it into a single durable ``Plan``,
reviewed holistically in Plan Review. Steering is the one legitimately
single-action conversational approval and stays on the approval queue.
"""

from datetime import datetime

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.communication.conversation.enums import ConversationStatus
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff._intake_parking import (
    park_steering,
    unwind_parked_steering,
)
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.enums import RoutingReason
from synthorg.meta.chief_of_staff.models import (
    Conversation,
    PlanDraftSummary,
    ProposeArgs,
    ProposeDecision,
    ProposedSteering,
    ProposedWork,
    ProposeResult,
    SteeringProposalSummary,
)
from synthorg.meta.chief_of_staff.plan_intake import ConversationalPlanDispatcher
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


def _summarise_decision(
    work: ProposedWork | None,
    steering: tuple[ProposedSteering, ...],
) -> str:
    """Multi-line assistant summary of the drafted plan and parked steering.

    Returns:
        Resulting string (a lead line plus one bullet per work/steering item).
    """
    lines: list[str] = []
    if work is not None:
        lines.append(f"- Drafting a plan for: {work.title} (review it in Plan Review)")
    lines += [f"- steer ({s.kind.value}): {s.text}" for s in steering]
    return "I've started on the following:\n" + "\n".join(lines)


class ProposeActMixin:
    """Draft a plan for a work brief and/or park steering directives.

    Relies on the concrete :class:`ChiefOfStaffProposer` to supply the
    approval store, conversation / turn repositories, the configuration,
    and the late-bound plan dispatcher.
    """

    _approval_store: ApprovalStoreProtocol
    _turn_repo: ConversationTurnRepository
    _conversation_repo: ConversationRepository
    _config: ChiefOfStaffConfig
    _plan_dispatcher: ConversationalPlanDispatcher | None

    def attach_plan_dispatcher(self, dispatcher: ConversationalPlanDispatcher) -> None:
        """Attach the conversational plan dispatcher (late-bind seam).

        The dispatcher drives an accepted work brief into the plan-review
        spine (provision project, intake the objective, background the
        decompose+park). Wired by the startup hook once the work pipeline
        and background-dispatch port are available.
        """
        self._plan_dispatcher = dispatcher

    async def _act_on_decision(  # noqa: PLR0913 -- one turn's full act context
        self,
        conversation: Conversation,
        args: ProposeArgs,
        decision: ProposeDecision,
        routing: RoutingDecision | None,
        routing_reason: RoutingReason,
        sequence: int,
        now: datetime,
    ) -> ProposeResult:
        """Carry out a concrete decision: draft a plan and/or park steering.

        Steering is parked first (compensatable), then the plan is drafted
        (a pipeline dispatch, not compensatable): a plan-draft failure
        unwinds the just-parked steering before re-raising, so a partial
        turn never leaves orphaned steering approvals with no assistant
        summary and no conversation transition.

        Returns:
            ``ProposeResult`` instance.

        Raises:
            ServiceUnavailableError: When a work brief needs drafting but no
                plan dispatcher is attached (the pipeline is not wired).
            Exception: When plan drafting fails after steering was parked.
        """
        steering_summaries = await self._park_steering(
            conversation, args, decision, now
        )
        try:
            plan_draft = await self._draft_plan(conversation, args, decision, now)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                COS_PROPOSE_FAILED,
                conversation_id=str(conversation.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="plan draft failed; unwinding parked steering",
                parked=len(steering_summaries),
            )
            for parked in steering_summaries:
                await unwind_parked_steering(self._approval_store, parked.approval_id)
            raise

        await self._turn_repo.append(
            build_attributed_assistant_turn(
                conversation_id=str(conversation.id),
                sequence=sequence,
                content=NotBlankStr(
                    _summarise_decision(decision.work, decision.steering)
                ),
                routing=routing,
                now=now,
            )
        )
        await self._transition_to_proposed(conversation, now)
        logger.info(
            COS_PROPOSE_PROPOSED,
            conversation_id=str(conversation.id),
            drafted_plan=plan_draft is not None,
            steering_count=len(steering_summaries),
        )
        return ProposeResult(
            conversation_id=str(conversation.id),
            status="proposed",
            plan_draft=plan_draft,
            steering=tuple(steering_summaries),
            responder_role=routing.responder.role if routing is not None else None,
            responder_name=routing.responder.name if routing is not None else None,
            routed_topic=routing.topic if routing is not None else None,
            routing_confidence=routing.confidence if routing is not None else None,
            routing_reason=routing_reason,
        )

    async def _draft_plan(
        self,
        conversation: Conversation,
        args: ProposeArgs,
        decision: ProposeDecision,
        now: datetime,
    ) -> PlanDraftSummary | None:
        """Draft a plan for the decision's work brief, if it carries one.

        Returns:
            The plan-draft handoff, or ``None`` on a steering-only turn.

        Raises:
            ServiceUnavailableError: When a work brief needs drafting but no
                plan dispatcher is attached.
        """
        if decision.work is None:
            return None
        if self._plan_dispatcher is None:
            logger.error(
                COS_PROPOSE_FAILED,
                conversation_id=str(conversation.id),
                note="work brief accepted but plan dispatcher not wired",
            )
            msg = "Plan drafting is unavailable: the work pipeline is not wired."
            raise ServiceUnavailableError(msg)
        return await self._plan_dispatcher.draft_plan(
            conversation=conversation,
            args=args,
            work=decision.work,
            now=now,
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
            for parked in summaries:
                await unwind_parked_steering(self._approval_store, parked.approval_id)
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
