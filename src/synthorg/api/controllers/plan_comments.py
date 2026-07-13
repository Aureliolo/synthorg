# module-kind: controller
"""Plan-item comment threads -- read and post per-item discussion.

The discussion surface for the plan-review workspace: a reader lists a plan's
comments (optionally narrowed to one item) and a reviewer posts a comment on an
item. Comments are immutable and written independently of the version-guarded
plan row, so a comment never conflicts with a concurrent rework. Each post is
broadcast on the shared ``plans`` WebSocket channel so an open workspace sees it
live.
"""

from typing import Annotated, Final
from uuid import uuid4

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
from synthorg.api.ws_models import WsEventType
from synthorg.core.plan_comment import PlanItemComment
from synthorg.core.types import NotBlankStr
from synthorg.persistence.plan_comment_protocol import PlanItemCommentFilterSpec
from synthorg.persistence.state import persistence_of

#: A plan's whole thread is loaded at once (threads are naturally bounded); the
#: cap guards against an unbounded materialisation on a pathological plan.
_MAX_THREAD: Final[int] = 500

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
        comments = await persistence_of(state.app_state).plan_comments.query(
            PlanItemCommentFilterSpec(plan_id=plan_id, item_id=item_id),
            limit=_MAX_THREAD,
        )
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

        Args:
            request: The incoming request (carries the authenticated user).
            state: Application state.
            plan_id: The plan the item belongs to.
            item_id: The item being commented on.
            data: The comment body.

        Returns:
            The persisted comment.

        Raises:
            UnauthorizedError: If the user is missing from the request scope.
        """
        auth_user = require_authenticated_user(request)
        comment = PlanItemComment(
            id=uuid4(),
            plan_id=plan_id,
            item_id=item_id,
            author=NotBlankStr(auth_user.username),
            body=data.body,
            created_at=state.app_state.clock.now(),
        )
        await persistence_of(state.app_state).plan_comments.append(comment)
        publish_ws_event(
            request,
            WsEventType.PLAN_COMMENT_ADDED,
            CHANNEL_PLANS,
            {
                "plan_id": plan_id,
                "item_id": item_id,
                "comment_id": str(comment.id),
                "author": comment.author,
            },
        )
        return ApiResponse(data=comment)
