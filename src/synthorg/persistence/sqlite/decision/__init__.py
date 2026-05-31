"""SQLite decision repository, composed from per-aspect mixins.

The repository is append-only: records can be appended and queried but
never updated or deleted, preserving audit integrity.  Version numbers
for ``(task_id, version)`` are computed atomically in SQL via a subquery
to eliminate the TOCTOU race that a read-then-write pattern would create
under concurrent review gate decisions.

The implementation is split by aspect so each file stays cohesive and
under the repository LOC cap:

- ``_cas``: ``append_with_next_version`` (the atomic next-version path).
- ``_audit``: ``append`` (the ``AppendOnlyRepository`` interface).
- ``_query``: ``query`` / ``get`` / ``list_by_task`` / ``list_by_agent``.

All three share ``_DecisionRepoBase`` (the connection + ``write_context``
seam) so the public ``SQLiteDecisionRepository`` is a single object
implementing the whole ``DecisionRepository`` protocol.
"""

from synthorg.persistence.sqlite.decision._audit import _AuditMixin
from synthorg.persistence.sqlite.decision._cas import _CasMixin
from synthorg.persistence.sqlite.decision._query import _QueryMixin


class SQLiteDecisionRepository(_CasMixin, _AuditMixin, _QueryMixin):
    """SQLite implementation of the ``DecisionRepository`` protocol.

    Append-only: decision records are immutable audit entries of
    review gate decisions.  Timestamps are normalized to UTC before
    storage for consistent lexicographic ordering.

    The backend's ``write_context`` serializes the multi-statement
    INSERT -> SELECT -> commit/rollback sequence in
    ``append_with_next_version`` so concurrent coroutines cannot
    interleave their statements or have one coroutine's rollback
    wipe another's in-flight INSERT.  Production callers receive the
    shared backend write context so this repository coordinates with
    OTHER repositories that mutate the same underlying
    ``aiosqlite.Connection``; tests can pass
    ``tests._shared.persistence.make_private_write_context()`` for
    standalone construction.

    Args:
        db: An open aiosqlite connection.
        write_context: Async context manager that serializes
            multi-statement transactions on ``db``.
    """


__all__ = ["SQLiteDecisionRepository"]
