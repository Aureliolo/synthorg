"""Postgres subworkflow repository, composed from per-aspect mixins.

Postgres-native port of ``synthorg.persistence.sqlite.subworkflow_repo``.
Uses JSONB for node/edge/IO columns and TIMESTAMPTZ for timestamps.
Stores versioned subworkflows keyed by ``(subworkflow_id, semver)``;
INSERT-only semantics -- duplicate coordinates are rejected.

The implementation is split by aspect so each module stays under the
repository LOC cap:

- ``_marshalling``: ``deserialize_row`` / ``extract_references`` /
  ``build_summaries_from_rows`` / ``semver_sort_key`` + SQL columns.
- ``_crud``: save / get / list_items / list_versions / list_summaries /
  search / delete.
- ``_references``: find_parents / delete_if_unreferenced (the
  referential-integrity scan over both workflow tables).

Both mixins share ``_SubworkflowRepoBase`` (the connection-pool seam).
"""

from synthorg.persistence.postgres.subworkflow_repo._crud import _CrudMixin
from synthorg.persistence.postgres.subworkflow_repo._references import _ReferencesMixin


class PostgresSubworkflowRepository(_CrudMixin, _ReferencesMixin):
    """Postgres-backed subworkflow repository.

    Stores versioned subworkflows keyed by ``(subworkflow_id, semver)``.
    INSERT-only semantics -- duplicate coordinates are rejected.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """


__all__ = ["PostgresSubworkflowRepository"]
