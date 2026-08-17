# module-kind: code
"""Shared task-filter WHERE-clause builder for the task repositories.

Both backends' ``query`` and ``count`` translate the same
:class:`TaskFilterSpec` into identical ``status``/``assigned_to``/
``project`` predicates; only the placeholder style (``?`` vs ``%s``)
differs. Building the clause list here keeps the four call sites from
drifting on which columns are filterable.
"""

from typing import LiteralString

from synthorg.persistence.task_protocol import TaskFilterSpec


def live_task_predicate(
    terminal_statuses: frozenset[str],
    placeholder: LiteralString,
) -> tuple[LiteralString, tuple[str, ...]]:
    """Build the predicate selecting a plan's unfinished tasks.

    Shared by both backends' guarded plan delete, which asks the same
    question twice (once inside the conditional ``DELETE``, once to report
    how many tasks blocked it) and must ask it identically each time.

    An empty *terminal_statuses* yields a predicate matching every task of
    the plan: nothing has been declared finished, so nothing may be assumed
    finished.

    Args:
        terminal_statuses: Task status values that count as finished.
        placeholder: The backend bound-parameter token (``"?"`` for SQLite,
            ``"%s"`` for Postgres).

    Returns:
        A ``(clause, params)`` pair. The clause expects the plan id as its
        FIRST bound parameter, ahead of *params*. The clause is a
        ``LiteralString``: it is assembled from literals and the caller's
        placeholder token alone, never from a status value, so no caller can
        interpolate data into SQL through it.
    """
    if not terminal_statuses:
        return f"plan_id = {placeholder}", ()
    ordered = tuple(sorted(terminal_statuses))
    holders = ", ".join(placeholder for _ in ordered)
    return f"plan_id = {placeholder} AND status NOT IN ({holders})", ordered


def build_task_filter_clauses(
    filter_spec: TaskFilterSpec,
    *,
    placeholder: str,
) -> tuple[list[str], list[object]]:
    """Build the WHERE-clause fragments and bound params for a task filter.

    Args:
        filter_spec: The task filter to translate.
        placeholder: The backend bound-parameter token (``"?"`` for
            SQLite, ``"%s"`` for Postgres).

    Returns:
        A ``(clauses, params)`` pair: the clause fragments to join with
        ``AND`` and the positional parameters in matching order.
    """
    clauses: list[str] = []
    params: list[object] = []
    if filter_spec.status is not None:
        clauses.append(f"status = {placeholder}")
        params.append(filter_spec.status.value)
    if filter_spec.assigned_to is not None:
        clauses.append(f"assigned_to = {placeholder}")
        params.append(filter_spec.assigned_to)
    if filter_spec.project is not None:
        clauses.append(f"project = {placeholder}")
        params.append(filter_spec.project)
    if filter_spec.plan is not None:
        clauses.append(f"plan_id = {placeholder}")
        params.append(str(filter_spec.plan))
    if filter_spec.blocked_reason is not None:
        clauses.append(f"blocked_reason = {placeholder}")
        params.append(filter_spec.blocked_reason.value)
    if filter_spec.after_id is not None:
        clauses.append(f"id > {placeholder}")
        params.append(filter_spec.after_id)
    if filter_spec.ids is not None:
        # The spec refuses an empty tuple, so this never degenerates to
        # ``IN ()``, which is a syntax error on both backends.
        placeholders = ", ".join(placeholder for _ in filter_spec.ids)
        clauses.append(f"id IN ({placeholders})")
        params.extend(filter_spec.ids)
    return clauses, params
