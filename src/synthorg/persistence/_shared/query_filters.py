"""Shared SQL filter-clause builders for repository queries.

Keeps a filter's clause + parameter construction in one place so the paired
``query()`` / ``count()`` methods (and both backends) cannot drift apart.
"""


def task_ids_in_clause(
    task_ids: frozenset[str] | None, placeholder: str
) -> tuple[list[str], list[str]]:
    """Build the ``task_id IN (...)`` condition + ordered params for a set filter.

    Args:
        task_ids: The task-id set to match, or ``None`` for no filter.
        placeholder: The backend parameter placeholder (``?`` SQLite, ``%s``
            Postgres).

    Returns:
        A ``(conditions, params)`` pair to extend the caller's clause / param
        lists: empty when ``task_ids`` is ``None``; a never-true ``1 = 0`` (no
        params) for an empty set so it matches nothing rather than everything;
        otherwise a single ``task_id IN (...)`` clause with sorted params for
        deterministic ordering.
    """
    if task_ids is None:
        return [], []
    ordered = sorted(task_ids)
    if not ordered:
        return ["1 = 0"], []
    placeholders = ", ".join(placeholder for _ in ordered)
    return [f"task_id IN ({placeholders})"], list(ordered)
