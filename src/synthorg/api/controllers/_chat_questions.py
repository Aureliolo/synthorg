# module-kind: service
"""Reading and answering parked agent questions from the conversation.

A question is an ``ApprovalItem`` an agent created when it stopped to ask; this
module projects the open ones for the chat surface and applies an answer or a
decline through the SAME decision write the approvals endpoints use, so the two
doors can never record different things for one question.
"""

import asyncio
from datetime import UTC, datetime
from typing import Final, LiteralString

from litestar import Request
from litestar.datastructures import State
from pydantic import ValidationError

from synthorg._core.features import require_service
from synthorg.api.controllers._chat_question_models import (
    AnswerQuestionRequest,
    ParkedQuestion,
    ParkedQuestionOption,
    QuestionDecisionResult,
)
from synthorg.api.controllers.approvals._decide import (
    NarrowDoor,
    apply_approval,
    apply_rejection,
)
from synthorg.api.controllers.approvals._enrichment import resolve_approval_context
from synthorg.api.controllers.approvals._shared import (
    ApprovalContext,
    ApprovalResponse,
)
from synthorg.api.lifecycle_helpers.plan_questions import apply_plan_question_answer
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalStatus, QuestionReversibility
from synthorg.approval.questions import (
    QUESTION_ACTION_TYPES,
    is_question,
)
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.approval import ApprovalItem
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ResourceNotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff._turn_redaction import redact_turn_content
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import (
    META_CHAT_QUESTION_ANSWERED,
    META_CHAT_QUESTION_NOT_FOUND,
    META_CHAT_QUESTION_PLAN_WRITEBACK_FAILED,
    META_CHAT_QUESTION_REVERSIBILITY_UNDECODABLE,
    META_CHAT_QUESTION_UNPROJECTABLE,
)
from synthorg.persistence.state import PersistenceStateSlice

logger = get_logger(__name__)

#: Fixed, server-owned audit reason for a declined question. Never operator
#: input: the decline route takes no request body, so there is no field an
#: attacker could populate on the "I am not answering" path. This is what the
#: audit row records; the guidance the agent is meant to ACT on travels
#: separately on the resume's trusted channel as ``DECLINED_QUESTION_NOTE``,
#: because a rejection reason is fenced as untrusted data by contract.
DECLINE_REASON: Final[str] = "The operator declined to answer this question."

#: 404 message shared by an unknown id and a non-question id, so a caller
#: cannot probe which arbitrary approval ids exist through this narrow door.
#: Threaded into the fetch as well as the scope check, because the default
#: miss message quotes the caller's identifier back and would otherwise make
#: the two cases distinguishable by response body alone.
_NOT_FOUND_MESSAGE: Final[LiteralString] = "Question not found"


def _decode_reversibility(
    raw: str | None, *, approval_id: str
) -> QuestionReversibility | None:
    """Decode the agent's declared reversibility from approval metadata.

    Returns:
        The declared value, or ``None`` when absent or unrecognised. ``None``
        is rendered as unclassified rather than defaulted, because inventing a
        value the agent never asserted is exactly the signal this field exists
        to carry.
    """
    if not raw:
        # A question parked before the tools required the field. Expected, and
        # distinct from the corrupt case below, which is why only that one logs.
        return None
    try:
        return QuestionReversibility(raw)
    except ValueError:
        logger.warning(
            META_CHAT_QUESTION_REVERSIBILITY_UNDECODABLE,
            approval_id=approval_id,
            known_values=[member.value for member in QuestionReversibility],
        )
        return None


def _to_question(item: ApprovalItem, context: ApprovalContext | None) -> ParkedQuestion:
    """Project one parked approval onto the chat surface's question shape.

    Returns:
        The question as the dashboard renders it.
    """
    evidence = item.evidence_package
    # The structural pick resolves against ``evidence_package.options``, which
    # carries the ids; ``metadata["options"]`` holds titles only, for the brain
    # DECISION record's alternatives.
    options = tuple(
        ParkedQuestionOption(
            id=opt.id,
            title=opt.title,
            summary=opt.summary,
            recommended=opt.recommended,
        )
        for opt in (evidence.options if evidence is not None else ())
    )
    agent = context.agent if context is not None else None
    task = context.task if context is not None else None
    project = context.project if context is not None else None
    return ParkedQuestion(
        approval_id=NotBlankStr(str(item.id)),
        question=item.description,
        asked_by_id=item.requested_by,
        asked_by_name=agent.name if agent is not None else item.requested_by,
        task_id=item.task_id,
        task_title=task.title if task is not None else None,
        project=project.name if project is not None else None,
        reversibility=_decode_reversibility(
            item.metadata.get("reversibility"), approval_id=str(item.id)
        ),
        options=options,
        asked_at=item.created_at,
    )


def _ordering_key(item: ApprovalItem) -> tuple[bool, datetime]:
    """Sort key putting hard-to-reverse questions first, then oldest first.

    Reads the raw approval rather than the projection so the whole set can be
    ordered and paged before anything is enriched.

    Returns:
        A tuple whose first element is ``False`` for a hard-to-reverse question
        so it sorts ahead of the rest.
    """
    reversibility = _decode_reversibility(
        item.metadata.get("reversibility"), approval_id=str(item.id)
    )
    hard = reversibility is QuestionReversibility.HARD_TO_REVERSE
    return (not hard, item.created_at)


async def open_question_items(app_state: AppState) -> tuple[ApprovalItem, ...]:
    """Return every open question as a raw approval, in display order.

    Enrichment is deliberately NOT done here. It costs a task read, an
    artifact query and an oracle evaluation per item, and the caller only
    renders one page: enriching the whole backlog first would make a polled
    endpoint's cost scale with the queue rather than with the page.

    Returns:
        The open questions, hard-to-reverse first and oldest first within each
        band, so the operator answers what blocks most first.

    Raises:
        ServiceUnavailableError: When the approval store is not wired.
    """
    store = require_service(app_state.slice(ApprovalStateSlice).store, "Approval Store")

    # Filter on status here rather than paging: ``list_items_page`` has no
    # status filter, so a page could come back entirely decided. The two reads
    # are independent, so they fan out rather than running back to back.
    async with asyncio.TaskGroup() as group:
        reads = [
            group.create_task(
                store.list_items(status=ApprovalStatus.PENDING, action_type=action_type)
            )
            for action_type in QUESTION_ACTION_TYPES
        ]
    items = [item for read in reads for item in read.result()]
    return tuple(sorted(items, key=_ordering_key))


async def project_questions(
    app_state: AppState,
    items: tuple[ApprovalItem, ...],
) -> tuple[ParkedQuestion, ...]:
    """Enrich and project one page of open questions.

    A row whose projection fails is dropped with a warning rather than failing
    the page: the DTO's field caps match the tool's today, but the underlying
    approval fields are unbounded, so one malformed row must not take the whole
    surface down for every operator. The enrichment layer degrades per row for
    the same reason.

    Returns:
        The questions as the dashboard renders them, in the order supplied.
    """
    contexts = await resolve_approval_context(app_state, items)
    projected: list[ParkedQuestion] = []
    for item in items:
        try:
            projected.append(_to_question(item, contexts.get(str(item.id))))
        except ValidationError as exc:
            logger.warning(
                META_CHAT_QUESTION_UNPROJECTABLE,
                approval_id=str(item.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
    return tuple(projected)


def _require_question(item: ApprovalItem) -> None:
    """Refuse an approval that is not a parked agent question.

    Runs before the pending check and before any decision is written, so an
    out-of-scope approval is refused without being decided. (The preceding
    store read still applies lazy TTL expiry, as it does for any reader.)

    Raises:
        ResourceNotFoundError: When the approval is not a parked question.
    """
    if not is_question(item.action_type):
        logger.warning(
            META_CHAT_QUESTION_NOT_FOUND,
            approval_id=str(item.id),
            reason="not_a_question",
        )
        raise ResourceNotFoundError(_NOT_FOUND_MESSAGE)


#: The scope this surface decides within: only a parked agent question, and
#: one fixed 404 whether the id is unknown or merely out of scope.
_QUESTION_DOOR: Final[NarrowDoor] = NarrowDoor(
    not_found_message=_NOT_FOUND_MESSAGE,
    require=_require_question,
)


async def _write_back_to_plan(
    app_state: AppState,
    item: ApprovalItem,
    *,
    answer: str | None,
) -> None:
    """Land a decided plan question on the plan the agents execute.

    A question parked off a plan is only answered once the plan says so: the
    dispatch tree is rebuilt from the durable plan, so an answer that stops at
    the approval row reaches nobody. Every other parked question is a no-op
    here (they resume their own agent instead).

    A persistence failure is logged, never raised: the operator's answer is
    already recorded and the agent already resumed, so failing the response
    would report a decision that in fact happened.
    """
    persistence = app_state.slice(PersistenceStateSlice).backend
    if persistence is None:
        return
    try:
        await apply_plan_question_answer(
            persistence.plans, item, answer=answer, clock=app_state.clock
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- the decision is already durable; a failed
        # write-back leaves the question listed, which is visible and retryable.
        reraise_critical(exc)
        logger.warning(
            META_CHAT_QUESTION_PLAN_WRITEBACK_FAILED,
            approval_id=str(item.id),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


def _to_result(response: ApprovalResponse) -> QuestionDecisionResult:
    """Project the decided approval onto the chat surface's result shape.

    Returns:
        The decision as the dashboard renders it.
    """
    return QuestionDecisionResult(
        approval_id=NotBlankStr(str(response.id)),
        status=response.status,
        recorded_answer=NotBlankStr(response.decision_reason or ""),
        decided_at=response.decided_at or datetime.now(UTC),
    )


async def answer_question(
    app_state: AppState,
    request: Request[object, object, State],
    approval_id: str,
    *,
    data: AnswerQuestionRequest,
) -> QuestionDecisionResult:
    """Answer a parked question and resume the agent with the answer.

    Returns:
        What was recorded, so the transcript echoes the persisted text.

    Raises:
        ResourceNotFoundError: When the id is unknown or is not a question.
        ConflictError: When the question was already decided.
        ValidationError: When a project decision has no valid chosen option.
    """
    # Redact before the answer becomes the decision reason, so the masked copy
    # is what is persisted, resumed with and broadcast rather than only what
    # is echoed back. The agent writes the question, so it can solicit a
    # credential ("paste the token so I can continue") and the operator can
    # oblige; the untrusted-content fence stops the text steering the model but
    # does nothing about handing a live secret to a tool-capable agent. The
    # sibling act/configure turns redact for the same reason.
    answer = NotBlankStr(redact_turn_content(data.answer))
    response = await apply_approval(
        app_state,
        request,
        approval_id,
        comment=answer,
        chosen_option_id=data.chosen_option_id,
        door=_QUESTION_DOOR,
    )
    await _write_back_to_plan(app_state, response, answer=answer)
    logger.info(
        META_CHAT_QUESTION_ANSWERED,
        approval_id=approval_id,
        declined=False,
        chose_option=data.chosen_option_id is not None,
    )
    return _to_result(response)


async def decline_question(
    app_state: AppState,
    request: Request[object, object, State],
    approval_id: str,
) -> QuestionDecisionResult:
    """Decline to answer, resuming the agent on its own judgement.

    Returns:
        What was recorded, carrying the fixed decline text.

    Raises:
        ResourceNotFoundError: When the id is unknown or is not a question.
        ConflictError: When the question was already decided.
    """
    response = await apply_rejection(
        app_state,
        request,
        approval_id,
        reason=DECLINE_REASON,
        door=_QUESTION_DOOR,
    )
    await _write_back_to_plan(app_state, response, answer=None)
    logger.info(
        META_CHAT_QUESTION_ANSWERED,
        approval_id=approval_id,
        declined=True,
        chose_option=False,
    )
    return _to_result(response)


__all__ = [
    "DECLINE_REASON",
    "answer_question",
    "decline_question",
    "open_question_items",
    "project_questions",
]
