# module-kind: declarative
"""Backend-agnostic SQL fragments and helpers for the decision repos.

The SQLite and Postgres decision repositories share the same column
list, page-limit cap, role-to-column mapping, role validation, and the
recursive JSON-unfreezing used when persisting frozen metadata views.
Those pieces live here once so the two backends cannot drift; only the
placeholder style and JSON wrapper (``json.dumps`` vs ``Jsonb``) stay in
the per-backend ``_sql`` modules.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger
from synthorg.observability.events.persistence.decision_record import (
    PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
)

logger = get_logger(__name__)

DECISION_MAX_PAGE_LIMIT: Final[int] = 1_000

DECISION_COLS: Final[str] = (
    "id, task_id, approval_id, executing_agent_id, reviewer_agent_id, "
    "decision, reason, criteria_snapshot, recorded_at, version, metadata"
)

# Maps ``DecisionRole`` Literal values to their corresponding column
# name.  Keeps the dynamic-column SQL in ``list_by_agent`` bounded to a
# closed set of identifiers that are never user-supplied.
ROLE_TO_COLUMN: Final[Mapping[str, str]] = MappingProxyType(
    {
        "executor": "executing_agent_id",
        "reviewer": "reviewer_agent_id",
    }
)


def resolve_role_column(role: object, *, agent_id: str) -> str:
    """Resolve a decision ``role`` to its agent-id column name.

    Defends against untyped callers that defeat the ``Literal`` type:
    a non-string or out-of-set role raises :class:`QueryError` rather
    than allowing an unbounded identifier into the dynamic SQL.

    Args:
        role: The caller-supplied role; must be ``"executor"`` or
            ``"reviewer"``.
        agent_id: The agent being queried, for logging context.

    Returns:
        The closed-set column name for *role*.

    Raises:
        QueryError: If *role* is not a string or not in the closed set.
    """
    role_obj: object = role
    if not isinstance(role_obj, str):
        got = type(role_obj).__name__
        msg = f"role must be 'executor' or 'reviewer', got {got}"
        logger.warning(
            PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
            agent_id=agent_id,
            role_type=got,
            error=msg,
        )
        raise QueryError(msg)
    try:
        return ROLE_TO_COLUMN[role_obj]
    except KeyError as exc:
        msg = f"role must be 'executor' or 'reviewer', got {role_obj!r}"
        logger.warning(
            PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
            agent_id=agent_id,
            role=role_obj,
            error=msg,
        )
        raise QueryError(msg) from exc


def unfreeze_for_json(value: object) -> object:
    """Recursively convert MappingProxyType/tuple/frozenset to JSON primitives.

    Metadata may carry a ``MappingProxyType`` (from a draft record's
    frozen view) at arbitrary nesting depth; unwrap recursively so the
    backend JSON encoder only ever sees plain dicts and lists.

    Returns:
        The value with all frozen views replaced by plain dicts/lists.
    """
    if isinstance(value, Mapping):
        return {k: unfreeze_for_json(v) for k, v in value.items()}
    if isinstance(value, tuple | list):
        return [unfreeze_for_json(item) for item in value]
    if isinstance(value, frozenset | set):
        return [unfreeze_for_json(item) for item in value]
    return value
