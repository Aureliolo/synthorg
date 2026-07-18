"""Plan-comment service layer.

Thin facade over :class:`PlanItemCommentRepository` so the comment controller
does not reach into ``persistence.plan_comments`` directly. It validates that a
comment targets a real plan and one of its items before writing (an unchecked
append would strand orphaned rows), and emits the ``API_PLAN_COMMENT_ADDED``
audit event a posted comment otherwise lacks, mirroring :class:`PlanService`.
"""

from typing import Final
from uuid import UUID, uuid4

from synthorg.api.responses import require_resource_or_404
from synthorg.core.clock import Clock
from synthorg.core.plan_comment import CommentAuthorKind, PlanItemComment
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_PLAN_COMMENT_ADDED,
    API_RESOURCE_NOT_FOUND,
)
from synthorg.persistence.plan_comment_protocol import (
    PlanItemCommentFilterSpec,
    PlanItemCommentRepository,
)
from synthorg.persistence.plan_protocol import PlanRepository

logger = get_logger(__name__)

#: A plan's whole thread is loaded at once (threads are naturally bounded); the
#: cap guards against an unbounded materialisation on a pathological plan.
MAX_THREAD: Final[int] = 500


class PlanCommentService:
    """Read and post plan-item comments with target validation + audit logging.

    Args:
        comments: The append-only plan-item comment repository.
        plans: The plan repository, used to validate a comment's target plan
            and item exist before writing.
        clock: Time seam; a posted comment stamps ``created_at`` from it.
    """

    __slots__ = ("_clock", "_comments", "_plans")

    def __init__(
        self,
        *,
        comments: PlanItemCommentRepository,
        plans: PlanRepository,
        clock: Clock,
    ) -> None:
        self._comments = comments
        self._plans = plans
        self._clock = clock

    async def list_comments(
        self,
        plan_id: NotBlankStr,
        *,
        item_id: NotBlankStr | None = None,
        limit: int = MAX_THREAD,
    ) -> tuple[PlanItemComment, ...]:
        """List a plan's comments oldest-first, optionally for one item.

        Returns:
            The plan's comments in ``(created_at ASC, id ASC)`` order.

        Raises:
            QueryError: Repository read failure (logged before propagating).
        """
        return await self._comments.query(
            PlanItemCommentFilterSpec(plan_id=plan_id, item_id=item_id),
            limit=limit,
        )

    async def add_comment(  # noqa: PLR0913 -- comment payload fields
        self,
        *,
        plan_id: NotBlankStr,
        item_id: NotBlankStr,
        author: NotBlankStr,
        body: NotBlankStr,
        author_kind: CommentAuthorKind = "human",
        author_agent_id: NotBlankStr | None = None,
        reply_to_id: UUID | None = None,
    ) -> PlanItemComment:
        """Post a comment on a plan item after validating the target exists.

        Args:
            plan_id: The plan the item belongs to.
            item_id: The item being commented on.
            author: The comment author's display name.
            body: The comment text.
            author_kind: Whether a human or an agent wrote it.
            author_agent_id: The responding agent's id for an agent comment.
            reply_to_id: The comment this one answers, when a reply.

        Returns:
            The persisted :class:`PlanItemComment`.

        Raises:
            NotFoundError: No plan with ``plan_id`` exists, or ``item_id`` is
                not one of its items (so the comment would be orphaned).
            DuplicateRecordError: A comment with the generated id already exists.
            QueryError: Repository write failure (logged before propagating).
        """
        await self._require_target(plan_id, item_id)
        if reply_to_id is not None:
            await self._require_reply_target(plan_id, item_id, reply_to_id)
        comment = PlanItemComment(
            id=uuid4(),
            plan_id=plan_id,
            item_id=item_id,
            author=author,
            author_kind=author_kind,
            author_agent_id=author_agent_id,
            reply_to_id=reply_to_id,
            body=body,
            created_at=self._clock.now(),
        )
        await self._comments.append(comment)
        logger.info(
            API_PLAN_COMMENT_ADDED,
            plan_id=plan_id,
            item_id=item_id,
            comment_id=str(comment.id),
            author=author,
            author_kind=author_kind,
        )
        return comment

    async def _require_target(
        self,
        plan_id: NotBlankStr,
        item_id: NotBlankStr,
    ) -> None:
        """Reject a comment whose plan or item does not exist (404).

        Raises:
            NotFoundError: The plan is missing, or ``item_id`` names no item
                on it, so the comment would be orphaned.
        """
        plan = require_resource_or_404(
            await self._plans.get(plan_id),
            resource_type="Plan",
            identifier=plan_id,
            log_event=API_RESOURCE_NOT_FOUND,
            operation="comment",
        )
        target = item_id if item_id in {item.id for item in plan.items} else None
        require_resource_or_404(
            target,
            resource_type="Plan item",
            identifier=item_id,
            log_event=API_RESOURCE_NOT_FOUND,
            operation="comment",
        )

    async def _require_reply_target(
        self,
        plan_id: NotBlankStr,
        item_id: NotBlankStr,
        reply_to_id: UUID,
    ) -> None:
        """Reject a reply whose parent is not a comment on the same item (404).

        The item is the thread, so a reply may only answer a comment already on
        that same item; a parent on another item (or none at all) would strand
        the reply outside any readable thread.

        Raises:
            NotFoundError: ``reply_to_id`` names no comment on this item.
        """
        siblings = await self._comments.query(
            PlanItemCommentFilterSpec(plan_id=plan_id, item_id=item_id),
            limit=MAX_THREAD,
        )
        parent = reply_to_id if reply_to_id in {c.id for c in siblings} else None
        require_resource_or_404(
            parent,
            resource_type="Parent comment",
            identifier=str(reply_to_id),
            log_event=API_RESOURCE_NOT_FOUND,
            operation="reply",
        )
