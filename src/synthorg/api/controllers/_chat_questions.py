# module-kind: service
"""Reading and answering parked agent questions from the conversation.

A question is an ``ApprovalItem`` an agent created when it stopped to ask; this
module projects the open ones for the chat surface and applies an answer or a
decline through the SAME decision write the approvals endpoints use, so the two
doors can never record different things for one question.
"""

from datetime import UTC, datetime
from typing import Final

from litestar import Request
from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.api.controllers._chat_question_models import (
    AnswerQuestionRequest,
    ParkedQuestion,
    ParkedQuestionOption,
    QuestionDecisionResult,
)
from synthorg.api.controllers.approvals._decide import apply_approval, apply_rejection
from synthorg.api.controllers.approvals._enrichment import resolve_approval_context
from synthorg.api.controllers.approvals._shared import (
    ApprovalContext,
    ApprovalResponse,
)
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalStatus, QuestionReversibility
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.approval import ApprovalItem
from synthorg.core.domain_errors import ResourceNotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.meta import (
    META_CHAT_QUESTION_ANSWERED,
    META_CHAT_QUESTION_NOT_FOUND,
)

logger = get_logger(__name__)

#: The two action types an agent's question is recorded under.
CLARIFY_ACTION_TYPE: Final[str] = "clarify:question"
DECISION_ACTION_TYPE: Final[str] = "decision:project"
QUESTION_ACTION_TYPES: Final[tuple[str, ...]] = (
    CLARIFY_ACTION_TYPE,
    DECISION_ACTION_TYPE,
)

#: Fixed, server-owned resume text for a declined question. Never operator
#: input: the decline route takes no request body, so there is no field an
#: attacker could populate on the "I am not answering" path.
DECLINE_REASON: Final[str] = (
    "The operator declined to answer this question. Proceed on your own best "
    "judgement, and state the assumption you made in your next output."
)

#: 404 message shared by an unknown id and a non-question id, so a caller
#: cannot probe which arbitrary approval ids exist through this narrow door.
_NOT_FOUND_MESSAGE: Final[str] = "Question not found"


def _decode_reversibility(raw: str | None) -> QuestionReversibility | None:
    """Decode the agent's declared reversibility from approval metadata.

    Returns:
        The declared value, or ``None`` when absent or unrecognised. ``None``
        is rendered as unclassified rather than defaulted, because inventing a
        value the agent never asserted is exactly the signal this field exists
        to carry.
    """
    if not raw:
        return None
    try:
        return QuestionReversibility(raw)
    except ValueError:
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
        reversibility=_decode_reversibility(item.metadata.get("reversibility")),
        is_decision=item.action_type == DECISION_ACTION_TYPE,
        options=options,
        asked_at=item.created_at,
    )


def _ordering_key(question: ParkedQuestion) -> tuple[bool, datetime]:
    """Sort key putting hard-to-reverse questions first, then oldest first.

    Returns:
        A tuple whose first element is ``False`` for a hard-to-reverse question
        so it sorts ahead of the rest.
    """
    hard = question.reversibility is QuestionReversibility.HARD_TO_REVERSE
    return (not hard, question.asked_at)


async def list_open_questions(app_state: AppState) -> tuple[ParkedQuestion, ...]:
    """Return every question currently waiting on a human.

    Returns:
        The open questions, hard-to-reverse first and oldest first within each
        band, so the operator answers what blocks most first.

    Raises:
        ServiceUnavailableError: When the approval store is not wired.
    """
    store = require_service(app_state.slice(ApprovalStateSlice).store, "Approval Store")
    items: list[ApprovalItem] = []
    for action_type in QUESTION_ACTION_TYPES:
        # Filter on status here rather than paging: ``list_items_page`` has no
        # status filter, so a page could come back entirely decided.
        items.extend(
            await store.list_items(
                status=ApprovalStatus.PENDING, action_type=action_type
            )
        )
    contexts = await resolve_approval_context(app_state, tuple(items))
    questions = [_to_question(item, contexts.get(str(item.id))) for item in items]
    return tuple(sorted(questions, key=_ordering_key))


def _require_question(item: ApprovalItem) -> None:
    """Refuse an approval that is not a parked agent question.

    Runs before the pending check and before any write, so an out-of-scope
    approval is refused without being touched.

    Raises:
        ResourceNotFoundError: When the approval is not a parked question.
    """
    if item.action_type not in QUESTION_ACTION_TYPES:
        logger.warning(
            META_CHAT_QUESTION_NOT_FOUND,
            approval_id=str(item.id),
            reason="not_a_question",
        )
        raise ResourceNotFoundError(_NOT_FOUND_MESSAGE)


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
    response = await apply_approval(
        app_state,
        request,
        approval_id,
        comment=data.answer,
        chosen_option_id=data.chosen_option_id,
        require=_require_question,
    )
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
        require=_require_question,
    )
    logger.info(
        META_CHAT_QUESTION_ANSWERED,
        approval_id=approval_id,
        declined=True,
        chose_option=False,
    )
    return _to_result(response)


__all__ = [
    "CLARIFY_ACTION_TYPE",
    "DECISION_ACTION_TYPE",
    "DECLINE_REASON",
    "QUESTION_ACTION_TYPES",
    "answer_question",
    "decline_question",
    "list_open_questions",
]
