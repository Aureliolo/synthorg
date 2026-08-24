# module-kind: declarative
"""The SQLite sprint statements, and why each is shaped as it is.

Split from the repository because they are two different kinds of thing.
These are the dialect: what JSON1 offers, where a bound value goes, which
predicate holds which invariant. The repository beside them is behaviour:
connection handling, error translation, marshalling. Keeping the SQL here
also lets the rationale each statement needs sit next to it without
crowding out the code that runs it.

The Postgres sibling is ``postgres/_sprint_sql.py``. The two express the
same guards in different dialects, so a difference between them is a real
difference in what the two backends admit and is worth seeing side by side.
"""

from synthorg.persistence._shared.sprint_marshalling import (
    SPRINT_COLUMNS,
    open_status_placeholders,
)

_OPEN_STATUS_SLOTS = open_status_placeholders("?")

ORDER_BY = "ORDER BY sprint_number DESC, id DESC"

UPSERT_SQL = f"""
    INSERT INTO sprints ({SPRINT_COLUMNS})
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        project = excluded.project,
        name = excluded.name,
        goal = excluded.goal,
        status = excluded.status,
        sprint_number = excluded.sprint_number,
        duration_days = excluded.duration_days,
        start_date = excluded.start_date,
        end_date = excluded.end_date,
        task_ids = excluded.task_ids,
        completed_task_ids = excluded.completed_task_ids,
        task_points = excluded.task_points,
        story_points_committed = excluded.story_points_committed,
        story_points_completed = excluded.story_points_completed
"""  # noqa: S608 -- column list is a compile-time constant

TRANSITION_SQL = (
    "UPDATE sprints SET "
    "status = ?, "
    "start_date = COALESCE(?, start_date), "
    "end_date = COALESCE(?, end_date) "
    "WHERE id = ? AND status = ?"
)

# The backlog append, guarded in the statement rather than by a prior read:
# two callers each adding a different task would otherwise read one
# pre-image and the second would write a backlog that never saw the first.
#
# ``JSON_PATCH(task_points, JSON_OBJECT(?, ?))`` rather than a JSON path
# built by concatenation: a task id is an arbitrary string, and '$.' || ?
# breaks on a '.', a '[' or a quote in it, whereas JSON_OBJECT takes the
# key as a bound value. ``story_points_committed`` is then re-totalled from
# that same merged mapping, so it is a pure function of ``task_points``
# rather than a running sum that can disagree with it.
#
# One asymmetry with the Postgres sibling, measured and left alone:
# JSON_OBJECT renders a double at 15 significant digits where the
# ``save`` path's json.dumps renders 17, so a value needing 16 or 17 is
# stored slightly differently by the two write paths and by the two
# backends. Every value a story point can realistically be round-trips
# exactly on both, so this buys nothing to chase; it is recorded because
# the difference is invisible until somebody writes a test with a
# pathological value and cannot see why it fails on one backend.
# The backlog cap is a predicate here rather than a check the caller makes
# first, for the reason the rest of the guard exists: two callers reading
# one pre-image both find room and both append, and the cap is service
# configuration that no column CHECK holds, so the over-cap row is durable.
# Measured against the row's own current length, so the answer is the one
# true at write time.
ADD_TASK_SQL = f"""
    UPDATE sprints
    SET task_ids = JSON_INSERT(task_ids, '$[#]', ?),
        task_points = JSON_PATCH(task_points, JSON_OBJECT(?, ?)),
        story_points_committed = (
            SELECT COALESCE(SUM(points.value), 0.0)
            FROM JSON_EACH(
                JSON_PATCH(sprints.task_points, JSON_OBJECT(?, ?))
            ) AS points
        )
    WHERE id = ?
      AND status = ?
      AND NOT EXISTS (SELECT 1 FROM JSON_EACH(sprints.task_ids) WHERE value = ?)
      AND JSON_ARRAY_LENGTH(sprints.task_ids) < ?
    RETURNING {SPRINT_COLUMNS}
"""  # noqa: S608 -- column list is a compile-time constant

# The completion append, guarded in the statement rather than by a prior
# read: ``'$[#]'`` is SQLite's append path, and the two EXISTS predicates
# hold the backlog membership and the not-already-completed invariants
# against the row's own current value.
#
# ``story_points_completed`` is re-derived over the resulting completed set
# rather than incremented, so it has no addition order to disagree with the
# one ``story_points_committed`` was folded in; the MIN pins it to the
# invariant the table's own CHECK asserts, which floating point cannot be
# relied on to reach exactly from two different directions.
COMPLETE_TASK_SQL = f"""
    UPDATE sprints
    SET completed_task_ids = JSON_INSERT(completed_task_ids, '$[#]', ?),
        story_points_completed = MIN(
            story_points_committed,
            (
                SELECT COALESCE(SUM(points.value), 0.0)
                FROM JSON_EACH(sprints.task_points) AS points
                WHERE points.key = ?
                   OR points.key IN (
                       SELECT done.value
                       FROM JSON_EACH(sprints.completed_task_ids) AS done
                   )
            )
        )
    WHERE id = ?
      AND status IN ({_OPEN_STATUS_SLOTS})
      AND EXISTS (SELECT 1 FROM JSON_EACH(sprints.task_ids) WHERE value = ?)
      AND NOT EXISTS (
          SELECT 1 FROM JSON_EACH(sprints.completed_task_ids) WHERE value = ?
      )
    RETURNING {SPRINT_COLUMNS}
"""  # noqa: S608 -- column list is a compile-time constant

DELETE_SQL = "DELETE FROM sprints WHERE id = ?"

GET_SQL = f"SELECT {SPRINT_COLUMNS} FROM sprints WHERE id = ?"  # noqa: S608

LIST_SQL = (
    f"SELECT {SPRINT_COLUMNS} FROM sprints "  # noqa: S608
    f"{ORDER_BY} LIMIT ? OFFSET ?"
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
