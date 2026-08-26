# module-kind: declarative
"""Column list and row/model marshalling for the SQLite plan repository.

Split from the repository so the column enumeration and the two functions
bound to its order sit together: ``_row_params`` writes positionally in
``COLUMNS`` order, and every INSERT/UPDATE/UPSERT fragment is derived from the
same list rather than hand-maintained beside it.
"""

import json
from typing import Final
from uuid import UUID

import aiosqlite

from synthorg.core.decomposition_progress import DecompositionProgress
from synthorg.core.plan import (
    Plan,
    PlanItem,
    PlanVersionSnapshot,
)
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.plan_review import PlanReview
from synthorg.core.task_enums import CoordinationTopology, TaskStructure
from synthorg.persistence._shared import coerce_row_timestamp, format_iso_utc

COLUMNS: Final[str] = (
    "id, project, project_name, objective_id, objective_title, parent_task_id, items, "
    "task_structure, coordination_topology, status, failure_reason, forecast_id, "
    "review, open_questions, assumptions, objective_criteria, version_history, "
    "replan_generation, version, created_at, updated_at, planning_strategy, "
    "review_absent_reason, decomposition_progress"
)

COLUMN_NAMES: Final[tuple[str, ...]] = tuple(COLUMNS.split(", "))
INSERT_PLACEHOLDERS: Final[str] = "(" + ", ".join("?" for _ in COLUMN_NAMES) + ")"
#: Every column except the ``id`` primary key, in ``COLUMNS`` order. The UPDATE
#: binds ``row_params(plan)[1:]`` in this same order, so the two stay aligned
#: from one list instead of three hand-maintained column enumerations.
WRITABLE_COLUMNS: Final[tuple[str, ...]] = tuple(
    col for col in COLUMN_NAMES if col != "id"
)
UPDATE_SET: Final[str] = ", ".join(f"{col}=?" for col in WRITABLE_COLUMNS)
UPSERT_SET: Final[str] = ", ".join(f"{col}=excluded.{col}" for col in WRITABLE_COLUMNS)


def row_to_plan(row: aiosqlite.Row) -> Plan:
    """Reconstruct a ``Plan`` from a database row.

    Returns:
        Validated ``Plan`` model instance.
    """
    data = dict(row)
    data["items"] = tuple(
        PlanItem.model_validate(item) for item in json.loads(data["items"])
    )
    data["task_structure"] = TaskStructure(data["task_structure"])
    data["coordination_topology"] = CoordinationTopology(data["coordination_topology"])
    data["status"] = PlanStatus(data["status"])
    forecast_id = data["forecast_id"]
    data["forecast_id"] = UUID(forecast_id) if forecast_id else None
    review = data["review"]
    data["review"] = PlanReview.model_validate(json.loads(review)) if review else None
    data["open_questions"] = tuple(json.loads(data["open_questions"]))
    data["assumptions"] = tuple(json.loads(data["assumptions"]))
    data["objective_criteria"] = tuple(json.loads(data["objective_criteria"]))
    data["version_history"] = tuple(
        PlanVersionSnapshot.model_validate(snapshot)
        for snapshot in json.loads(data["version_history"])
    )
    progress = data["decomposition_progress"]
    # Presence, not truthiness: the column stores TEXT, so an empty object is
    # the truthy ``"{}"`` here and the falsy ``{}`` on the backend that
    # deserialises JSONB for us. Tested the same way, the two agree.
    data["decomposition_progress"] = (
        DecompositionProgress.model_validate(json.loads(progress))
        if progress is not None
        else None
    )
    data["created_at"] = coerce_row_timestamp(data["created_at"])
    data["updated_at"] = coerce_row_timestamp(data["updated_at"])
    return Plan.model_validate(data)


def row_params(plan: Plan) -> tuple[object, ...]:
    """Serialise a plan into positional SQL params in ``COLUMNS`` order.

    Returns:
        Scalar param values for INSERT/UPDATE, aligned with ``COLUMNS``.
    """
    return (
        str(plan.id),
        plan.project,
        plan.project_name,
        plan.objective_id,
        plan.objective_title,
        plan.parent_task_id,
        json.dumps([item.model_dump(mode="json") for item in plan.items]),
        plan.task_structure.value,
        plan.coordination_topology.value,
        plan.status.value,
        plan.failure_reason,
        str(plan.forecast_id) if plan.forecast_id is not None else None,
        json.dumps(plan.review.model_dump(mode="json")) if plan.review else None,
        json.dumps(list(plan.open_questions)),
        json.dumps(list(plan.assumptions)),
        json.dumps(list(plan.objective_criteria)),
        json.dumps([snap.model_dump(mode="json") for snap in plan.version_history]),
        plan.replan_generation,
        plan.version,
        format_iso_utc(plan.created_at),
        format_iso_utc(plan.updated_at),
        plan.planning_strategy,
        plan.review_absent_reason,
        serialise_progress(plan.decomposition_progress),
    )


def serialise_progress(progress: DecompositionProgress | None) -> str | None:
    """Render a progress snapshot for the column, or ``None`` for absent.

    Returns:
        The JSON text, or ``None``.
    """
    if progress is None:
        return None
    return json.dumps(progress.model_dump(mode="json"))


__all__ = [
    "COLUMNS",
    "COLUMN_NAMES",
    "INSERT_PLACEHOLDERS",
    "UPDATE_SET",
    "UPSERT_SET",
    "WRITABLE_COLUMNS",
    "row_params",
    "row_to_plan",
    "serialise_progress",
]
