# module-kind: declarative
"""SQL constants and row-shaping helpers for the SQLite decision repo.

Append-only: version numbers for ``(task_id, version)`` are computed
atomically in SQL via a subquery to eliminate the TOCTOU race that a
read-then-write pattern would create under concurrent review gate
decisions.
"""

import json
import sqlite3
from datetime import UTC
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

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
    :id, :task_id, :approval_id, :executing_agent_id, :reviewer_agent_id,
    :decision, :reason, :criteria_snapshot, :recorded_at,
    (SELECT COALESCE(MAX(version), 0) + 1
       FROM decision_records WHERE task_id = :task_id),
    :metadata
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

    Normalizes ``recorded_at`` to UTC (ISO 8601 with ``+00:00`` offset)
    so lexicographic ordering of the ``recorded_at`` column is
    equivalent to chronological ordering across mixed-timezone callers.

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
        "criteria_snapshot": json.dumps(list(criteria_snapshot)),
        "recorded_at": recorded_at.astimezone(UTC).isoformat(),
        # ``metadata`` may contain ``MappingProxyType`` (from the draft
        # record's frozen view) at arbitrary nesting depth; unwrap
        # recursively so ``json.dumps`` only sees plain dicts and
        # lists.
        "metadata": json.dumps(_unfreeze_for_json(metadata)),
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


def _is_structural_constraint_error(exc: sqlite3.IntegrityError) -> bool:
    """Return True for CHECK / FOREIGN KEY / NOT NULL constraint violations.

    These represent schema-level invariants that the application
    relies on (e.g. ``reviewer_agent_id != executing_agent_id``).
    Masking them as generic ``QueryError`` would hide programming
    errors or schema drift; letting the original
    ``sqlite3.IntegrityError`` propagate keeps the structural
    failure visible to operators and to the review-gate service's
    narrowed ``except (QueryError, DuplicateRecordError)`` catch.

    Returns:
        ``True`` for CHECK / FOREIGN KEY / NOT NULL violations, ``False`` otherwise.
    """
    return exc.sqlite_errorname in {
        "SQLITE_CONSTRAINT_CHECK",
        "SQLITE_CONSTRAINT_FOREIGNKEY",
        "SQLITE_CONSTRAINT_NOTNULL",
        "SQLITE_CONSTRAINT_TRIGGER",
    }
