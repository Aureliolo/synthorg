"""Postgres approval repository, composed from per-aspect mixins.

Sibling of :class:`SQLiteApprovalRepository` backed by
``psycopg_pool.AsyncConnectionPool``.  Uses native ``JSONB`` for the
``evidence_package`` and ``metadata`` columns and ``TIMESTAMPTZ`` for
all timestamps -- matching the schema in
``persistence/postgres/schema.sql``.

The implementation is split by aspect so each module stays under the
repository LOC cap:

- ``_sql``: SQL constants (``SELECT_COLS``, ``APPROVALS_UPSERT_SQL``).
- ``_marshalling``: ``row_to_item`` / ``item_save_params``.
- ``_write``: save / save_many / expire_if_pending / transition_if /
  consume_if_approved / delete.
- ``_read``: get / get_many / list_items / query / count.

All mixins share ``_ApprovalRepoBase`` (the connection-pool seam) so the
public ``PostgresApprovalRepository`` is a single object satisfying the
:class:`ApprovalRepository` protocol structurally.
"""

from synthorg.persistence.postgres.approval_repo._read import _ReadMixin
from synthorg.persistence.postgres.approval_repo._write import _WriteMixin


class PostgresApprovalRepository(_WriteMixin, _ReadMixin):
    """Postgres-backed approval item repository.

    Provides CRUD operations for approval items using a shared
    ``psycopg_pool.AsyncConnectionPool``.  Satisfies the
    :class:`ApprovalRepository` protocol structurally.

    Args:
        pool: An open psycopg async connection pool.
    """


__all__ = ["PostgresApprovalRepository"]
