# module-kind: controller
"""Parked agent questions, answerable in the unified conversation.

The org asks; this is where the operator answers, in the chat they are already
in rather than in the approvals queue. Every write delegates to the same
decision path the approvals endpoints use.

Deliberately NOT gated on ``chief_of_staff.turn_router_enabled``: answering a
parked question is not a chat turn, it is a decision a running agent is blocked
on, and toggling the chat router off must not strand it. This follows the
invite-consent precedent, which is ungated for the same reason.
"""

import hashlib
from typing import Annotated, Final

from litestar import Controller, Request, get, post
from litestar.datastructures import State
from litestar.params import HeaderParameter

from synthorg.api.controllers._chat_idempotency import (
    chat_request_fingerprint,
    run_chat_idempotent,
)
from synthorg.api.controllers._chat_question_models import (
    AnswerQuestionRequest,
    ParkedQuestion,
    QuestionDecisionResult,
)
from synthorg.api.controllers._chat_questions import (
    answer_question,
    decline_question,
    list_open_questions,
)
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_approval_roles, require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.core.actor_context import require_actor
from synthorg.core.types import NotBlankStr

_DEFAULT_LIMIT: Final[int] = 50

# The durable idempotency-key column is bounded at 255 chars and these routes
# store the composite ``f"{approval_id}:{idempotency_key}"`` (a 36-char UUID
# plus a ":" separator), so the caller's raw key must stay within 218.
_MAX_IDEMPOTENCY_KEY_LEN: Final[int] = 218

_QuestionIdempotencyKeyHeader = Annotated[
    NotBlankStr,
    HeaderParameter(
        name="Idempotency-Key",
        description=(
            "RFC-style retry-safe key. Required: an identical key returns the "
            "cached decision instead of re-answering, so a 5xx-driven client "
            "retry cannot double-fire the resume signal."
        ),
        required=True,
        min_length=1,
        max_length=_MAX_IDEMPOTENCY_KEY_LEN,
    ),
]


def _decline_fingerprint(approval_id: str) -> str:
    """Fingerprint for the bodyless decline, so its replay check has a payload.

    Returns:
        Hex SHA-256 digest binding the fingerprint to the target question.
    """
    return hashlib.sha256(f"decline:{approval_id}".encode()).hexdigest()


class ChatQuestionsController(Controller):
    """Read and answer the questions the organisation is waiting on."""

    path = "/meta/chat/questions"
    tags = ("meta",)
    guards = (require_read_access,)

    @get(
        "/",
        guards=[per_op_rate_limit_from_policy("meta.chat.questions.list", key="user")],
    )
    async def list_questions(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[ParkedQuestion]:
        """List the questions currently waiting on a human.

        The chat page's hydrate-on-mount source, so a reload never loses a
        waiting question. Read access is enough to see that the organisation is
        blocked; answering needs the approval roles.

        Args:
            state: Application state.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.

        Returns:
            The open questions, hard-to-reverse first then oldest first.
        """
        app_state: AppState = state.app_state
        questions = await list_open_questions(app_state)
        page, meta = paginate_cursor(
            questions,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )
        return PaginatedResponse(data=page, pagination=meta)

    @post(
        "/{approval_id:str}/answer",
        status_code=200,
        guards=[
            require_approval_roles,
            per_op_rate_limit_from_policy("meta.chat.questions.answer", key="user"),
        ],
    )
    async def answer(
        self,
        state: State,
        approval_id: PathId,
        data: AnswerQuestionRequest,
        request: Request[object, object, State],
        idempotency_key: _QuestionIdempotencyKeyHeader,
    ) -> ApiResponse[QuestionDecisionResult]:
        """Answer a parked question, resuming the agent with the answer.

        Guarded by the same roles as the approvals decision endpoints it
        delegates to: the thread the question arrived on proves which approval
        is being answered, never that this human may answer it.

        Args:
            state: Application state.
            approval_id: The question to answer.
            data: The answer, and the chosen option for a project decision.
            request: The incoming HTTP request.
            idempotency_key: Required caller-supplied retry token.

        Returns:
            What was recorded, so the transcript echoes the persisted text.

        Raises:
            ResourceNotFoundError: When the id is unknown or is not a question.
            ConflictError: When the question was already decided, or a
                concurrent decision holds the same idempotency key.
        """
        app_state: AppState = state.app_state
        actor = require_actor()

        async def _build() -> ApiResponse[QuestionDecisionResult]:
            result = await answer_question(app_state, request, approval_id, data=data)
            return ApiResponse[QuestionDecisionResult](data=result)

        dumped = await run_chat_idempotent(
            app_state,
            scope="meta.chat.questions.answer",
            actor_id=actor.actor_id,
            # Bind the question into the key so a token reused against a
            # different question cannot return this one's cached decision.
            key=f"{approval_id}:{idempotency_key}",
            endpoint="/meta/chat/questions/answer",
            request_fingerprint=chat_request_fingerprint(data),
            build=_build,
        )
        return ApiResponse[QuestionDecisionResult].model_validate(dumped)

    @post(
        "/{approval_id:str}/decline",
        status_code=200,
        guards=[
            require_approval_roles,
            per_op_rate_limit_from_policy("meta.chat.questions.decline", key="user"),
        ],
    )
    async def decline(
        self,
        state: State,
        approval_id: PathId,
        request: Request[object, object, State],
        idempotency_key: _QuestionIdempotencyKeyHeader,
    ) -> ApiResponse[QuestionDecisionResult]:
        """Decline to answer, resuming the agent on its own judgement.

        Bodyless by design: an optional operator note here would reintroduce
        free text on the "I am not answering" path and give two injection
        surfaces where there should be one. The resume text is a constant.

        Args:
            state: Application state.
            approval_id: The question to decline.
            request: The incoming HTTP request.
            idempotency_key: Required caller-supplied retry token.

        Returns:
            What was recorded, carrying the fixed decline text.

        Raises:
            ResourceNotFoundError: When the id is unknown or is not a question.
            ConflictError: When the question was already decided, or a
                concurrent decision holds the same idempotency key.
        """
        app_state: AppState = state.app_state
        actor = require_actor()

        async def _build() -> ApiResponse[QuestionDecisionResult]:
            result = await decline_question(app_state, request, approval_id)
            return ApiResponse[QuestionDecisionResult](data=result)

        dumped = await run_chat_idempotent(
            app_state,
            scope="meta.chat.questions.decline",
            actor_id=actor.actor_id,
            key=f"{approval_id}:{idempotency_key}",
            endpoint="/meta/chat/questions/decline",
            request_fingerprint=_decline_fingerprint(approval_id),
            build=_build,
        )
        return ApiResponse[QuestionDecisionResult].model_validate(dumped)


__all__ = ["ChatQuestionsController"]
