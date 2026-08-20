# module-kind: code
"""One operator action, one request: deleting a selected set.

A dashboard bulk delete was a loop of single-row DELETEs, and every one of the
three destructive operations it drives is rate limited per user (five projects a
minute, five plans, twenty tasks). Selecting more than that had the limiter
refuse the tail, so the action reported a partial failure for a reason that had
nothing to do with the rows: an operator clearing a round's residue got five
deletions and a wall of 5001s.

The limiter is not the thing to loosen. It bounds destructive throughput, and a
bulk delete is ONE destructive decision the operator took once, so it is one
call with its own budget rather than N calls against a per-row one.

Every row is still deleted by the same code path its own DELETE takes, so the
cascade, the tombstone and the WebSocket event are identical; what differs is
that a refusal is collected rather than raised, because one row that cannot go
must not decide the fate of the others.
"""

from collections.abc import Awaitable, Callable
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.domain_errors import DomainError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_BULK_DELETE_PARTIAL,
    API_BULK_DELETE_ROW_REFUSED,
)

logger = get_logger(__name__)

#: How many rows one call may carry, and the real bound on how much one
#: request can destroy: the rate limit meters requests, so the per-user ceiling
#: is this number times that budget rather than the budget alone.
#:
#: Held at the largest page the dashboard can render, because selection is
#: scoped to the rows on screen and no operator action can produce a bigger
#: set. A larger selection is two actions, which the operator can see and stop
#: between.
MAX_BULK_DELETE_IDS: Final[int] = 100


class BulkDeleteRequest(BaseModel):
    """The rows an operator selected for deletion.

    Attributes:
        ids: The identifiers to delete, in any order. Duplicates are collapsed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    ids: tuple[NotBlankStr, ...] = Field(
        min_length=1,
        max_length=MAX_BULK_DELETE_IDS,
        description="Identifiers of the rows to delete.",
    )


class BulkDeleteFailure(BaseModel):
    """One row that could not be deleted, and why.

    Attributes:
        id: The row the operator selected. It is echoed rather than resolved to
            a name because the caller selected it from a list it is holding, so
            it labels its own row; nothing renders this value.
        reason: The refusing error's own operator-facing message. Deliberately
            not the log description: that one preserves the exception class
            name and whatever identifier the raise interpolated, and this field
            is rendered in a toast, where an internal class name and a database
            key are both things no operator surface may print. The specific
            cause is kept on ``API_BULK_DELETE_ROW_REFUSED`` instead.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Identifier of the row that remains.")
    reason: NotBlankStr = Field(description="Why this row could not be deleted.")


class BulkDeleteResult(BaseModel):
    """What one bulk delete actually did.

    Both halves are reported, because a partial outcome is neither a success
    nor a failure and telling an operator only about the failures is how they
    conclude that nothing happened to an action they cannot undo.

    Attributes:
        deleted: Identifiers that were removed.
        failed: The rows that remain, each naming what refused.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    deleted: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Identifiers that were removed.",
    )
    failed: tuple[BulkDeleteFailure, ...] = Field(
        default=(),
        description="Rows that remain, each with the reason it does.",
    )


async def run_bulk_delete(
    ids: tuple[NotBlankStr, ...],
    delete_one: Callable[[str], Awaitable[None]],
    *,
    entity: str,
) -> BulkDeleteResult:
    """Delete each of *ids* through *delete_one*, collecting refusals.

    Sequential rather than concurrent: each of these cascades across plans,
    tasks and approvals, and running them together on one operator click would
    interleave writes over rows that reference each other.

    Args:
        ids: The selected identifiers; duplicates are collapsed, order kept.
        delete_one: The same removal the row's own DELETE performs.
        entity: What is being deleted, for the log.

    Returns:
        What was removed and what remains.
    """
    seen: set[str] = set()
    deleted: list[NotBlankStr] = []
    failed: list[BulkDeleteFailure] = []
    for entity_id in ids:
        if entity_id in seen:
            continue
        seen.add(entity_id)
        try:
            await delete_one(entity_id)
        except DomainError as exc:
            # A typed refusal is an outcome for THIS row (still building, no
            # longer there, an approval decided mid-flight). Anything else is
            # not about the row and takes the request down as it would a
            # single delete.
            logger.warning(
                API_BULK_DELETE_ROW_REFUSED,
                entity=entity,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            failed.append(
                BulkDeleteFailure(
                    id=NotBlankStr(entity_id),
                    reason=NotBlankStr(exc.default_message),
                )
            )
            continue
        deleted.append(NotBlankStr(entity_id))
    if failed:
        logger.warning(
            API_BULK_DELETE_PARTIAL,
            entity=entity,
            deleted_count=len(deleted),
            failed_count=len(failed),
        )
    return BulkDeleteResult(deleted=tuple(deleted), failed=tuple(failed))


__all__ = [
    "MAX_BULK_DELETE_IDS",
    "BulkDeleteFailure",
    "BulkDeleteRequest",
    "BulkDeleteResult",
    "run_bulk_delete",
]
