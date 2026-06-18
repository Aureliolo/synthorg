# module-kind: declarative
"""Shared task-filter WHERE-clause builder for the task repositories.

Both backends' ``query`` and ``count`` translate the same
:class:`TaskFilterSpec` into identical ``status``/``assigned_to``/
``project`` predicates; only the placeholder style (``?`` vs ``%s``)
differs. Building the clause list here keeps the four call sites from
drifting on which columns are filterable.
"""

from synthorg.persistence.task_protocol import TaskFilterSpec


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
    return clauses, params
