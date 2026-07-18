# module-kind: controller
"""Plan-item comment threads -- read and post per-item discussion.

The discussion surface for the plan-review workspace: a reader lists a plan's
comments (optionally narrowed to one item) and a reviewer posts a comment on an
item. Comments are immutable and written independently of the version-guarded
plan row, so a comment never conflicts with a concurrent rework. Each post is
broadcast on the shared ``plans`` WebSocket channel so an open workspace sees it
live.

When a reply model is configured, an operator's comment is answered inline by
the responsible role (the item's owner, else the Chief of Staff): a grounded,
attributed agent reply is appended and broadcast on the same channel. The reply
is best-effort and loop-safe -- only a human comment is answered -- so a failed
reply never blocks the operator's comment.
"""

from typing import Annotated

from litestar import Controller, Request, get, post
from litestar.datastructures import State
from litestar.params import QueryParameter

from synthorg.api.auth.controller_helpers import require_authenticated_user
from synthorg.api.channels import CHANNEL_PLANS, publish_ws_event
from synthorg.api.dto import ApiResponse
from synthorg.api.dto_plans import PlanCommentPayload
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.path_params import QUERY_MAX_LENGTH, PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.services.plan_comment_service import PlanCommentService
from synthorg.api.state import AppState
from synthorg.api.ws_models import WsEventType
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan_comment import PlanItemComment
from synthorg.core.types import NotBlankStr
from synthorg.engine.state import EngineStateSlice
from synthorg.hr.state import agent_registry_of
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.plan_review import PLAN_REVIEW_REPLY_FAILED
from synthorg.persistence.state import persistence_of
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.kill_switch import resolve_bool_with_fallback
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)


def _service(state: State) -> PlanCommentService:
    """Build the per-request :class:`PlanCommentService`.

    Returns:
        A service bound to this backend's comment + plan repositories and clock.
    """
    persistence = persistence_of(state.app_state)
    return PlanCommentService(
        comments=persistence.plan_comments,
        plans=persistence.plans,
        clock=state.app_state.clock,
    )


def _publish_comment(
    request: Request[object, object, State], comment: PlanItemComment
) -> None:
    """Broadcast a posted comment on the shared plans WebSocket channel.

    The event is a refresh signal, not the comment itself: a subscriber
    refetches the item's thread (picking up authorship and any reply link
    from the comment DTOs), so the wire payload stays the minimal locator it
    always was even though a comment now carries a kind and a reply link.
    """
    publish_ws_event(
        request,
        WsEventType.PLAN_COMMENT_ADDED,
        CHANNEL_PLANS,
        {
            "plan_id": comment.plan_id,
            "item_id": comment.item_id,
            "comment_id": str(comment.id),
            "author": comment.author,
        },
    )


async def _maybe_agent_reply(
    request: Request[object, object, State],
    state: State,
    *,
    human_comment: PlanItemComment,
) -> None:
    """Answer a human plan-item comment as the responsible role, best-effort.

    Runs only when the reply service is wired and ``plan_review_reply_enabled``
    is live-true (opt-out, default on). Loop-safe: only a human comment reaches
    here. Never raises: any failure is logged and swallowed so the operator's
    comment still returns 201.
    """
    app_state: AppState = state.app_state
    service = app_state.slice(EngineStateSlice).plan_item_reply_service
    if service is None:
        return
    enabled = await resolve_bool_with_fallback(
        resolver=config_resolver_of(app_state),
        namespace=SettingNamespace.COORDINATION,
        key="plan_review_reply_enabled",
        fallback=True,
    )
    if not enabled:
        return
    try:
        plan = await persistence_of(app_state).plans.get(human_comment.plan_id)
        if plan is None:
            return
        item = next((i for i in plan.items if i.id == human_comment.item_id), None)
        if item is None:
            return
        active = tuple(await agent_registry_of(app_state).list_active())
        reply = await service.reply(
            plan=plan,
            item=item,
            comment_body=human_comment.body,
            active=active,
        )
        if reply is None:
            return
        agent_comment = await _service(state).add_comment(
            plan_id=human_comment.plan_id,
            item_id=human_comment.item_id,
            author=reply.author,
            body=reply.body,
            author_kind="agent",
            author_agent_id=reply.author_agent_id,
            reply_to_id=human_comment.id,
        )
        _publish_comment(request, agent_comment)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised; reply is optional
        reraise_critical(exc)
        logger.warning(
            PLAN_REVIEW_REPLY_FAILED,
            detail="reply_append_failed",
            plan_id=human_comment.plan_id,
            item_id=human_comment.item_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


PlanCommentItemFilter = Annotated[
    NotBlankStr | None,
    QueryParameter(
        required=False,
        max_length=QUERY_MAX_LENGTH,
        description="Narrow the thread to a single plan item",
    ),
]


class PlanCommentController(Controller):
    """Read and post per-item plan comment threads."""

    path = "/plans/{plan_id:str}/comments"
    tags = ("plans",)

    @get(guards=[require_read_access])
    async def list_comments(
        self,
        state: State,
        plan_id: PathId,
        item_id: PlanCommentItemFilter = None,
    ) -> ApiResponse[list[PlanItemComment]]:
        """List a plan's comments oldest-first, optionally for one item.

        Args:
            state: Application state.
            plan_id: The plan whose thread to read.
            item_id: Narrow to a single item's thread when set.

        Returns:
            The plan's comments, oldest first.
        """
        comments = await _service(state).list_comments(plan_id, item_id=item_id)
        return ApiResponse(data=list(comments))

    @post(
        "/items/{item_id:str}",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("plans.comment", key="user"),
        ],
        status_code=201,
    )
    async def add_comment(
        self,
        request: Request[object, object, State],
        state: State,
        plan_id: PathId,
        item_id: PathId,
        data: PlanCommentPayload,
    ) -> ApiResponse[PlanItemComment]:
        """Post a comment on a plan item; the author is the authenticated user.

        The operator's comment is returned as soon as it is persisted; when a
        reply model is configured the responsible role's grounded answer is
        appended and broadcast separately (best-effort), never delaying or
        failing this response.

        Args:
            request: The incoming request (carries the authenticated user).
            state: Application state.
            plan_id: The plan the item belongs to.
            item_id: The item being commented on.
            data: The comment body and optional reply target.

        Returns:
            The persisted comment.

        Raises:
            UnauthorizedError: If the user is missing from the request scope.
            NotFoundError: The plan or item does not exist (404).
        """
        auth_user = require_authenticated_user(request)
        comment = await _service(state).add_comment(
            plan_id=plan_id,
            item_id=item_id,
            author=NotBlankStr(auth_user.username),
            body=data.body,
            reply_to_id=data.reply_to_id,
        )
        _publish_comment(request, comment)
        await _maybe_agent_reply(request, state, human_comment=comment)
        return ApiResponse(data=comment)
