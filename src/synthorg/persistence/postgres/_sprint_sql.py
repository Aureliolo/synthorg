# module-kind: declarative
"""The Postgres sprint statements, and why each is shaped as it is.

Split from the repository because they are two different kinds of thing.
These are the dialect: what JSONB offers, where a bound value goes, which
predicate holds which invariant. The repository beside them is behaviour:
pool handling, error translation, marshalling. Keeping the SQL here also
lets the rationale each statement needs sit next to it without crowding out
the code that runs it.

The SQLite sibling is ``sqlite/_sprint_sql.py``. The two express the same
guards in different dialects, so a difference between them is a real
difference in what the two backends admit and is worth seeing side by side.
"""

from synthorg.persistence._shared.sprint_marshalling import (
    SPRINT_COLUMNS,
    open_status_placeholders,
)

_OPEN_STATUS_SLOTS = open_status_placeholders("%s")

ORDER_BY = "ORDER BY sprint_number DESC, id DESC"

UPSERT_SQL = f"""
    INSERT INTO sprints ({SPRINT_COLUMNS})
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO UPDATE SET
        project = EXCLUDED.project,
        name = EXCLUDED.name,
        goal = EXCLUDED.goal,
        status = EXCLUDED.status,
        sprint_number = EXCLUDED.sprint_number,
        duration_days = EXCLUDED.duration_days,
        start_date = EXCLUDED.start_date,
        end_date = EXCLUDED.end_date,
        task_ids = EXCLUDED.task_ids,
        completed_task_ids = EXCLUDED.completed_task_ids,
        task_points = EXCLUDED.task_points,
        story_points_committed = EXCLUDED.story_points_committed,
        story_points_completed = EXCLUDED.story_points_completed
"""  # noqa: S608 -- column list is a compile-time constant

TRANSITION_SQL = (
    "UPDATE sprints SET "
    "status = %s, "
    "start_date = COALESCE(%s, start_date), "
    "end_date = COALESCE(%s, end_date) "
    "WHERE id = %s AND status = %s"
)

# The backlog append, guarded in the statement rather than by a prior read:
# two callers each adding a different task would otherwise read one
# pre-image and the second would write a backlog that never saw the first.
#
# ``JSONB_BUILD_OBJECT`` takes the task id as a bound value, so an id
# carrying JSON metacharacters is a key rather than a syntax accident.
# ``story_points_committed`` is re-totalled from that same merged mapping,
# so it is a pure function of ``task_points`` rather than a running sum that
# can disagree with it.
#
# The backlog cap is a predicate here rather than a check the caller makes
# first, for the reason the rest of the guard exists: two callers reading
# one pre-image both find room and both append, and the cap is service
# configuration that no column CHECK holds, so the over-cap row is durable.
# Measured against the row's own current length, so the answer is the one
# true at write time.
ADD_TASK_SQL = f"""
    UPDATE sprints
    SET task_ids = task_ids || TO_JSONB(%s::TEXT),
        task_points = task_points
            || JSONB_BUILD_OBJECT(%s::TEXT, %s::DOUBLE PRECISION),
        story_points_committed = (
            SELECT COALESCE(SUM(points.value::DOUBLE PRECISION), 0.0)
            FROM JSONB_EACH_TEXT(
                sprints.task_points
                || JSONB_BUILD_OBJECT(%s::TEXT, %s::DOUBLE PRECISION)
            ) AS points(key, value)
        )
    WHERE id = %s
      AND status = %s
      AND NOT (task_ids @> TO_JSONB(%s::TEXT))
      AND JSONB_ARRAY_LENGTH(task_ids) < %s
    RETURNING {SPRINT_COLUMNS}
"""  # noqa: S608 -- column list is a compile-time constant

# The completion append, guarded in the statement rather than by a prior
# read. Concatenating a JSONB scalar onto a JSONB array appends it, and
# ``@>`` against a scalar asks whether the array contains it, so the
# backlog-membership and not-already-completed invariants are held against
# the row's own current value.
#
# Under READ COMMITTED (Postgres' default, and what this pool runs) an
# UPDATE that blocks on a concurrent writer re-evaluates this WHERE against
# the newer row version, so two simultaneous completions serialise instead
# of one overwriting the other. Under REPEATABLE READ or SERIALIZABLE the
# second would instead take a 40001 serialisation failure: correct, since
# nothing is lost, but a raise rather than a result, and the caller's retry
# ladder is what covers it. Neither statement here depends on the weaker
# level for CORRECTNESS; it decides only whether contention costs a wait or
# an exception.
#
# ``story_points_completed`` is re-derived over the resulting completed set
# rather than incremented, so it has no addition order to disagree with the
# one ``story_points_committed`` was folded in; the LEAST pins it to the
# invariant the table's own CHECK asserts, which floating point cannot be
# relied on to reach exactly from two different directions.
COMPLETE_TASK_SQL = f"""
    UPDATE sprints
    SET completed_task_ids = completed_task_ids || TO_JSONB(%s::TEXT),
        story_points_completed = LEAST(
            story_points_committed,
            (
                SELECT COALESCE(SUM(points.value::DOUBLE PRECISION), 0.0)
                FROM JSONB_EACH_TEXT(sprints.task_points) AS points(key, value)
                WHERE points.key = %s
                   OR sprints.completed_task_ids @> TO_JSONB(points.key)
            )
        )
    WHERE id = %s
      AND status IN ({_OPEN_STATUS_SLOTS})
      AND task_ids @> TO_JSONB(%s::TEXT)
      AND NOT (completed_task_ids @> TO_JSONB(%s::TEXT))
    RETURNING {SPRINT_COLUMNS}
"""  # noqa: S608 -- column list is a compile-time constant

DELETE_SQL = "DELETE FROM sprints WHERE id = %s"

GET_SQL = f"SELECT {SPRINT_COLUMNS} FROM sprints WHERE id = %s"  # noqa: S608

LIST_SQL = (
    f"SELECT {SPRINT_COLUMNS} FROM sprints "  # noqa: S608
    f"{ORDER_BY} LIMIT %s OFFSET %s"
)


__all__ = [
    "ADD_TASK_SQL",
    "COMPLETE_TASK_SQL",
    "DELETE_SQL",
    "GET_SQL",
    "LIST_SQL",
    "ORDER_BY",
    "TRANSITION_SQL",
    "UPSERT_SQL",
]
