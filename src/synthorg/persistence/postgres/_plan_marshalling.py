# module-kind: declarative
"""Column list and row/model marshalling for the Postgres plan repository.

Split from the repository so the column enumeration and the two functions
bound to its order sit together: ``row_params`` writes positionally in
``COLUMNS`` order, and every INSERT/UPDATE/UPSERT fragment is derived from the
same list rather than hand-maintained beside it.
"""

from typing import Final
from uuid import UUID

from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from synthorg.core.decomposition_progress import DecompositionProgress
from synthorg.core.plan import (
    Plan,
    PlanItem,
    PlanVersionSnapshot,
)
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.plan_review import PlanReview
from synthorg.core.task_enums import CoordinationTopology, TaskStructure
from synthorg.persistence._shared import coerce_row_timestamp

COLUMNS: Final[str] = (
    "id, project, project_name, objective_id, objective_title, parent_task_id, items, "
    "task_structure, coordination_topology, status, failure_reason, forecast_id, "
    "review, open_questions, assumptions, objective_criteria, version_history, "
    "replan_generation, version, created_at, updated_at, planning_strategy, "
    "review_absent_reason, decomposition_progress"
)
COLUMN_NAMES: Final[tuple[str, ...]] = tuple(
    name.strip() for name in COLUMNS.split(",")
)
# Derive placeholders + SET clauses from the single column list so the arity can
# never drift from ``row_params`` (the sqlite repo drift-proofs the same way).
INSERT_PLACEHOLDERS: Final[str] = "(" + ", ".join("%s" for _ in COLUMN_NAMES) + ")"
UPDATE_SET: Final[str] = ", ".join(
    f"{name}=%s" for name in COLUMN_NAMES if name != "id"
)
UPSERT_SET: Final[str] = ", ".join(
    f"{name}=EXCLUDED.{name}" for name in COLUMN_NAMES if name != "id"
)


def row_to_plan(row: DictRow) -> Plan:
    """Reconstruct a ``Plan`` from a Postgres dict_row.

    ``items`` arrives as a Python list of dicts (JSONB auto-deserialized by
    psycopg); ``created_at`` / ``updated_at`` arrive as aware ``datetime``
    values from their ``TIMESTAMPTZ`` columns. ``dict_row`` yields a fresh
    mutable ``dict`` per row, so the coercions rewrite it in place.

    Returns:
        Validated ``Plan`` model instance.
    """
    row["items"] = tuple(PlanItem.model_validate(item) for item in (row["items"] or []))
    row["task_structure"] = TaskStructure(row["task_structure"])
    row["coordination_topology"] = CoordinationTopology(row["coordination_topology"])
    row["status"] = PlanStatus(row["status"])
    forecast_id = row["forecast_id"]
    row["forecast_id"] = UUID(forecast_id) if forecast_id else None
    review = row["review"]
    row["review"] = PlanReview.model_validate(review) if review else None
    row["open_questions"] = tuple(row["open_questions"] or [])
    row["assumptions"] = tuple(row["assumptions"] or [])
    row["objective_criteria"] = tuple(row["objective_criteria"] or [])
    row["version_history"] = tuple(
        PlanVersionSnapshot.model_validate(snapshot)
        for snapshot in (row["version_history"] or [])
    )
    progress = row["decomposition_progress"]
    # Presence, not truthiness: psycopg hands back a dict here and the sibling
    # backend hands back TEXT, so an empty object is falsy on one and truthy on
    # the other. Tested the same way, the two agree.
    row["decomposition_progress"] = (
        DecompositionProgress.model_validate(progress) if progress is not None else None
    )
    row["created_at"] = coerce_row_timestamp(row["created_at"])
    row["updated_at"] = coerce_row_timestamp(row["updated_at"])
    return Plan.model_validate(row)


def row_params(plan: Plan) -> tuple[object, ...]:
    """Serialise a plan into positional SQL params in column order.

    Returns:
        Scalar param values for INSERT/UPDATE in the fixed column order.
    """
    return (
        str(plan.id),
        plan.project,
        plan.project_name,
        plan.objective_id,
        plan.objective_title,
        plan.parent_task_id,
        Jsonb([item.model_dump(mode="json") for item in plan.items]),
        plan.task_structure.value,
        plan.coordination_topology.value,
        plan.status.value,
        plan.failure_reason,
        str(plan.forecast_id) if plan.forecast_id is not None else None,
        Jsonb(plan.review.model_dump(mode="json")) if plan.review else None,
        Jsonb(list(plan.open_questions)),
        Jsonb(list(plan.assumptions)),
        Jsonb(list(plan.objective_criteria)),
        Jsonb([snap.model_dump(mode="json") for snap in plan.version_history]),
        plan.replan_generation,
        plan.version,
        plan.created_at,
        plan.updated_at,
        plan.planning_strategy,
        plan.review_absent_reason,
        serialise_progress(plan.decomposition_progress),
    )


def serialise_progress(progress: DecompositionProgress | None) -> Jsonb | None:
    """Render a progress snapshot for the column, or ``None`` for absent.

    Returns:
        The JSONB wrapper, or ``None``.
    """
    if progress is None:
        return None
    return Jsonb(progress.model_dump(mode="json"))


__all__ = [
    "COLUMNS",
    "COLUMN_NAMES",
    "INSERT_PLACEHOLDERS",
    "UPDATE_SET",
    "UPSERT_SET",
    "row_params",
    "row_to_plan",
    "serialise_progress",
]
