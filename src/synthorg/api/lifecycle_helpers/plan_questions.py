# module-kind: service
"""A plan's open questions, as questions someone can actually answer.

The decomposer surfaces what it could not resolve onto ``plan.open_questions``.
That field was written, persisted and rendered, and nothing ever read it into an
answer surface: the escalation fired into a void, and the operator was shown a
list of things the org needed with no way to say them.

Two decisions, one owner each. "Does a human need to decide this?" belongs to
the planner, which already answered it by surfacing the question. "What is the
answer?" belongs to the human, so each question is parked as a real
``clarify:question`` approval, listed and answered through the same narrow door
every other agent question uses, and the answer is written back onto the plan
the agents execute.
"""

from datetime import datetime
from typing import Final

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.approval.questions import CLARIFY_ACTION_TYPE
from synthorg.core.approval import ApprovalItem
from synthorg.core.clock import Clock
from synthorg.core.plan import Plan
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.pipeline import (
    PIPELINE_PLAN_QUESTION_ANSWERED,
    PIPELINE_PLAN_QUESTION_PARKED,
)
from synthorg.persistence.plan_protocol import PlanRepository

logger = get_logger(__name__)

#: ``ApprovalItem.metadata`` key naming the plan a parked question belongs to.
#: Shared with the plan-approval item so both point at the same durable plan.
PLAN_ID_METADATA_KEY: Final[str] = "plan_id"

#: ``ApprovalItem.metadata`` key carrying the question's position in
#: ``plan.open_questions`` at park time. The text is matched first, because a
#: concurrent edit can reorder the list; the index only disambiguates duplicates.
PLAN_QUESTION_INDEX_KEY: Final[str] = "plan_question_index"

#: Recorded on the plan when the operator declined to answer. The plan still
#: says what it proceeded on, which is the point of the assumptions list.
_DECLINED_ASSUMPTION: Final[str] = (
    "{question} -- the operator declined to answer; the plan proceeds on the "
    "planner's own judgement"
)

#: Recorded on the plan when the operator answered.
_ANSWERED_ASSUMPTION: Final[str] = "{question} -- answered: {answer}"


def build_plan_questions(
    plan: Plan,
    *,
    task_id: NotBlankStr,
    requested_by: NotBlankStr,
    now: datetime,
) -> tuple[ApprovalItem, ...]:
    """Build one parked question per unresolved question on *plan*.

    Args:
        plan: The durable plan whose ``open_questions`` are being parked.
        task_id: The plan's objective task, so the question inherits the same
            context enrichment every other parked question gets.
        requested_by: Who the question is attributed to.
        now: Park timestamp.

    Returns:
        One ``clarify:question`` approval per open question, in plan order.
    """
    return tuple(
        ApprovalItem(
            action_type=NotBlankStr(CLARIFY_ACTION_TYPE),
            title=NotBlankStr(f"Question about the plan for: {plan.objective_title}"),
            description=question,
            requested_by=requested_by,
            # The plan cannot proceed as researched until it is answered, but
            # answering is not irreversible: the plan is still awaiting approval.
            risk_level=ApprovalRiskLevel.MEDIUM,
            source=ApprovalSource.PLAN_REVIEW,
            status=ApprovalStatus.PENDING,
            created_at=now,
            task_id=task_id,
            metadata={
                PLAN_ID_METADATA_KEY: str(plan.id),
                PLAN_QUESTION_INDEX_KEY: str(index),
            },
        )
        for index, question in enumerate(plan.open_questions)
    )


def _settle(plan: Plan, question: str, assumption: str, *, now: datetime) -> Plan:
    """Move *question* out of the open list and record what settled it.

    Returns:
        The plan with the question dropped and the assumption appended.
    """
    remaining = tuple(q for q in plan.open_questions if q != question)
    return plan.model_copy(
        update={
            "open_questions": remaining,
            "assumptions": (*plan.assumptions, NotBlankStr(assumption)),
            "version": plan.version + 1,
            "updated_at": now,
        }
    )


async def apply_plan_question_answer(
    plans: PlanRepository,
    item: ApprovalItem,
    *,
    answer: str | None,
    clock: Clock,
) -> None:
    """Write a decided plan question back onto the plan the agents execute.

    An answer that stops at the approval row is an answer the plan never
    heard: the items are rebuilt from the durable plan at dispatch, so a
    question answered anywhere else is answered to nobody.

    A no-op when the approval is not a plan question, when the plan is gone,
    or when the question is no longer open (an operator edit may already have
    resolved it), so a decision is never blocked on the write-back.

    Args:
        plans: Repository the durable plan is read from and written to.
        item: The decided question approval.
        answer: What the operator said, or ``None`` when they declined.
        clock: Time seam stamping the revision.
    """
    plan_id = item.metadata.get(PLAN_ID_METADATA_KEY)
    if plan_id is None:
        return
    plan = await plans.get(NotBlankStr(str(plan_id)))
    if plan is None:
        return
    question = item.description
    if question not in plan.open_questions:
        return
    assumption = (
        _ANSWERED_ASSUMPTION.format(question=question, answer=answer)
        if answer
        else _DECLINED_ASSUMPTION.format(question=question)
    )
    settled = _settle(plan, question, assumption, now=clock.now())
    await plans.update(settled, expected_version=plan.version)
    logger.info(
        PIPELINE_PLAN_QUESTION_ANSWERED,
        plan_id=str(plan.id),
        approval_id=str(item.id),
        declined=not answer,
        remaining_questions=len(settled.open_questions),
    )


def log_parked(plan: Plan, count: int) -> None:
    """Record that *count* questions were parked for *plan*.

    Args:
        plan: The plan whose questions were parked.
        count: How many were parked.
    """
    if count:
        logger.info(
            PIPELINE_PLAN_QUESTION_PARKED,
            plan_id=str(plan.id),
            question_count=count,
        )


__all__ = [
    "PLAN_ID_METADATA_KEY",
    "PLAN_QUESTION_INDEX_KEY",
    "apply_plan_question_answer",
    "build_plan_questions",
    "log_parked",
]
