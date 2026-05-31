# module-kind: declarative
"""SQL constants and bound-parameter helpers for the Postgres decision repo.

Append-only: version numbers for ``(task_id, version)`` are computed via a
``SELECT COALESCE(MAX(version), 0) + 1`` subquery inside the INSERT.
psycopg uses Postgres' default READ COMMITTED isolation (not
SERIALIZABLE), so two concurrent writers can race and compute the same
next version.  The ``UNIQUE(task_id, version)`` constraint guarantees
only one wins; the loser retries (see ``_cas._execute_insert``).
"""

from datetime import UTC
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from psycopg.types.json import Jsonb
from pydantic import AwareDatetime

from synthorg.core.enums import DecisionOutcome

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr

_MAX_PAGE_LIMIT: int = 1_000


_COLS = (
    "id, task_id, approval_id, executing_agent_id, reviewer_agent_id, "
    "decision, reason, criteria_snapshot, recorded_at, version, metadata"
)

# Maps ``DecisionRole`` Literal values to their corresponding column
# name.  Keeps the dynamic-column SQL in ``list_by_agent`` bounded to a
# closed set of identifiers that are never user-supplied.
_ROLE_TO_COLUMN: Final[dict[str, str]] = {
    "executor": "executing_agent_id",
    "reviewer": "reviewer_agent_id",
}

_INSERT_SQL: Final[str] = """\
INSERT INTO decision_records (
    id, task_id, approval_id, executing_agent_id, reviewer_agent_id,
    decision, reason, criteria_snapshot, recorded_at, version, metadata
) VALUES (
    %(id)s, %(task_id)s, %(approval_id)s, %(executing_agent_id)s,
    %(reviewer_agent_id)s, %(decision)s, %(reason)s,
    %(criteria_snapshot)s, %(recorded_at)s,
    (SELECT COALESCE(MAX(version), 0) + 1
       FROM decision_records WHERE task_id = %(task_id)s),
    %(metadata)s
)"""


def _build_insert_params(  # noqa: PLR0913
    *,
    record_id: NotBlankStr,
    task_id: NotBlankStr,
    approval_id: NotBlankStr | None,
    executing_agent_id: NotBlankStr,
    reviewer_agent_id: NotBlankStr,
    decision: DecisionOutcome,
    reason: str | None,
    criteria_snapshot: tuple[NotBlankStr, ...],
    recorded_at: AwareDatetime,
    metadata: dict[str, object],
) -> dict[str, object]:
    """Shape the bound-parameter dict for the INSERT statement.

    Normalizes ``recorded_at`` to UTC so ordering of the ``recorded_at``
    column is equivalent to chronological ordering across
    mixed-timezone callers.

    In Postgres, JSONB columns accept Python dicts/lists directly
    (psycopg converts them). We wrap with ``Jsonb()`` for clarity.

    Returns:
        Result of type ``dict[str, object]``.
    """
    return {
        "id": record_id,
        "task_id": task_id,
        "approval_id": approval_id,
        "executing_agent_id": executing_agent_id,
        "reviewer_agent_id": reviewer_agent_id,
        "decision": decision.value,
        "reason": reason,
        "criteria_snapshot": Jsonb(list(criteria_snapshot)),
        "recorded_at": recorded_at.astimezone(UTC),
        # ``metadata`` may contain ``MappingProxyType`` (from the draft
        # record's frozen view) at arbitrary nesting depth; unwrap
        # recursively so only plain dicts and lists are stored.
        "metadata": Jsonb(_unfreeze_for_json(metadata)),
    }


def _unfreeze_for_json(value: object) -> object:
    """Recursively convert MappingProxyType/tuple/frozenset to JSON primitives.

    Returns:
        Result of type ``object``.
    """
    if isinstance(value, MappingProxyType):
        return {k: _unfreeze_for_json(v) for k, v in value.items()}
    if isinstance(value, dict):
        return {k: _unfreeze_for_json(v) for k, v in value.items()}
    if isinstance(value, tuple | list):
        return [_unfreeze_for_json(item) for item in value]
    if isinstance(value, frozenset | set):
        return [_unfreeze_for_json(item) for item in value]
    return value
