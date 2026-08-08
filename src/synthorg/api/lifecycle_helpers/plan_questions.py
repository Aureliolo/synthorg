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
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import VersionConflictError
from synthorg.core.persistence_errors import PersistenceVersionConflictError
from synthorg.core.plan import Plan
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.pipeline import (
    PIPELINE_PLAN_QUESTION_ANSWERED,
    PIPELINE_PLAN_QUESTION_PARKED,
    PIPELINE_PLAN_QUESTION_WRITE_FAILED,
)
from synthorg.persistence.plan_protocol import PlanRepository

logger = get_logger(__name__)

#: ``ApprovalItem.metadata`` key naming the plan a parked question belongs to.
#: Shared with the plan-approval item so both point at the same durable plan.
PLAN_ID_METADATA_KEY: Final[str] = "plan_id"

#: Attempts (including the first) at the write-back before giving up. A losing
#: writer re-reads and re-applies rather than reporting a decision as failed:
#: two questions answered at once contend on the same plan row, and the second
#: answer is not wrong, it is just second.
_WRITE_BACK_MAX_ATTEMPTS: Final[int] = 5

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
            # The question text is the key. A position would go stale the
            # moment any other question is answered, and would then settle
            # a different question than the one the operator was reading.
            metadata={PLAN_ID_METADATA_KEY: str(plan.id)},
        )
        for question in plan.open_questions
    )


def _settle(plan: Plan, question: str, assumption: str, *, now: datetime) -> Plan:
    """Move one occurrence of *question* out of the open list.

    Exactly one, not every match: a planner may legitimately surface the same
    question twice (once per item it blocks), and one answer settles one of
    them. Dropping both would silently retire a question nobody answered.

    Returns:
        The plan with one occurrence dropped and the assumption appended.
    """
    remaining = list(plan.open_questions)
    remaining.remove(question)
    return plan.model_copy(
        update={
            "open_questions": tuple(remaining),
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

    Raises:
        PersistenceError: The write-back failed. Propagated rather than
            swallowed: an answer that did not reach the plan is an answer the
            agents will never see, and the operator has to be told their
            decision did not land.
        VersionConflictError: Every attempt lost the race to another writer,
            so the answer never landed. Same reasoning: reporting success
            would tell the operator the plan heard them when it did not.
    """
    plan_id = item.metadata.get(PLAN_ID_METADATA_KEY)
    if plan_id is None:
        return
    for _attempt in range(_WRITE_BACK_MAX_ATTEMPTS):
        if await _settle_once(plans, item, answer=answer, clock=clock):
            return
    # Every attempt lost the race. Reporting success would tell the operator
    # their answer reached the plan when it did not.
    msg = (
        f"plan {plan_id} was rewritten under every write-back attempt; "
        "the answer did not land"
    )
    logger.warning(
        PIPELINE_PLAN_QUESTION_WRITE_FAILED,
        plan_id=str(plan_id),
        approval_id=str(item.id),
        declined=not answer,
        reason="version_conflict_exhausted",
    )
    raise VersionConflictError(msg)


async def _settle_once(
    plans: PlanRepository,
    item: ApprovalItem,
    *,
    answer: str | None,
    clock: Clock,
) -> bool:
    """Read, settle and write once.

    Returns:
        ``True`` when the answer landed or there was nothing to settle;
        ``False`` when another writer moved the row first and the caller
        should re-read.

    Raises:
        PersistenceError: Any write failure other than a version conflict.
    """
    plan = await plans.get(NotBlankStr(str(item.metadata[PLAN_ID_METADATA_KEY])))
    if plan is None:
        return True
    question = item.description
    if question not in plan.open_questions:
        return True
    assumption = (
        _ANSWERED_ASSUMPTION.format(question=question, answer=answer)
        if answer
        else _DECLINED_ASSUMPTION.format(question=question)
    )
    settled = _settle(plan, question, assumption, now=clock.now())
    try:
        await plans.update(settled, expected_version=plan.version)
    except PersistenceVersionConflictError:
        # Another question on the same plan was answered between the read and
        # the write. Both answers are wanted, so the loser re-reads rather
        # than reporting the operator's decision as failed.
        return False
    except Exception as exc:
        reraise_critical(exc)
        # Logged before it propagates, like every sibling plan write: the
        # caller sees a failed decision, but only this frame knows which
        # question on which plan was being settled when it failed.
        logger.warning(
            PIPELINE_PLAN_QUESTION_WRITE_FAILED,
            plan_id=str(plan.id),
            approval_id=str(item.id),
            declined=not answer,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise
    logger.info(
        PIPELINE_PLAN_QUESTION_ANSWERED,
        plan_id=str(plan.id),
        approval_id=str(item.id),
        declined=not answer,
        remaining_questions=len(settled.open_questions),
    )
    return True


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
    "apply_plan_question_answer",
    "build_plan_questions",
    "log_parked",
]
