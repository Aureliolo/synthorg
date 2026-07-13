# module-kind: declarative
"""Persistence event constants for the plan sub-domain."""

from typing import Final

PERSISTENCE_PLAN_SAVED: Final[str] = "persistence.plan.saved"
PERSISTENCE_PLAN_SAVE_FAILED: Final[str] = "persistence.plan.save_failed"
PERSISTENCE_PLAN_FETCHED: Final[str] = "persistence.plan.fetched"
PERSISTENCE_PLAN_FETCH_FAILED: Final[str] = "persistence.plan.fetch_failed"
PERSISTENCE_PLAN_LISTED: Final[str] = "persistence.plan.listed"
PERSISTENCE_PLAN_LIST_FAILED: Final[str] = "persistence.plan.list_failed"
PERSISTENCE_PLAN_DELETED: Final[str] = "persistence.plan.deleted"
PERSISTENCE_PLAN_DELETE_FAILED: Final[str] = "persistence.plan.delete_failed"
PERSISTENCE_PLAN_DESERIALIZE_FAILED: Final[str] = "persistence.plan.deserialize_failed"

# Per-item comment thread (``plan_item_comments``), a table distinct from
# ``plans``; its own events keep comment-write/list/purge failures attributable
# separately from the plan row's.
PERSISTENCE_PLAN_COMMENT_SAVE_FAILED: Final[str] = (
    "persistence.plan_comment.save_failed"
)
PERSISTENCE_PLAN_COMMENT_LIST_FAILED: Final[str] = (
    "persistence.plan_comment.list_failed"
)
PERSISTENCE_PLAN_COMMENT_DESERIALIZE_FAILED: Final[str] = (
    "persistence.plan_comment.deserialize_failed"
)
PERSISTENCE_PLAN_COMMENT_PURGE_FAILED: Final[str] = (
    "persistence.plan_comment.purge_failed"
)
