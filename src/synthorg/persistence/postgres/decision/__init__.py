"""Postgres decision repository, composed from per-aspect mixins.

The repository is append-only: records can be appended and queried but
never updated or deleted, preserving audit integrity.  Version numbers
for ``(task_id, version)`` are computed atomically via an
``INSERT ... SELECT`` against ``decision_records``, so concurrent
review-gate decisions cannot race on the same ``(task_id, version)``
slot.

The implementation is split by aspect so each file stays cohesive and
under the repository LOC cap:

- ``_cas``: ``append_with_next_version`` (atomic next-version insert
  with the ``UNIQUE(task_id, version)`` version-race retry loop).
- ``_audit``: ``append`` (the ``AppendOnlyRepository`` interface).
- ``_query``: ``query`` / ``get`` / ``list_by_task`` / ``list_by_agent``.

All three share ``_DecisionRepoBase`` (the connection-pool seam) so the
public ``PostgresDecisionRepository`` is a single object implementing
the whole ``DecisionRepository`` protocol.
"""

from synthorg.persistence.postgres.decision._audit import _AuditMixin
from synthorg.persistence.postgres.decision._cas import _CasMixin
from synthorg.persistence.postgres.decision._query import _QueryMixin


class PostgresDecisionRepository(_CasMixin, _AuditMixin, _QueryMixin):
    """Postgres implementation of the ``DecisionRepository`` protocol.

    Append-only: decision records are immutable audit entries.
    ``recorded_at`` is normalized to UTC before storage; reads via
    ``get`` / ``list_by_task`` / ``list_by_agent`` therefore always
    return UTC timestamps.

    Args:
        pool: An open ``psycopg_pool.AsyncConnectionPool``.
    """


__all__ = ["PostgresDecisionRepository"]
