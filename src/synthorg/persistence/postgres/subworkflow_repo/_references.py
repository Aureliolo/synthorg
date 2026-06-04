"""Referential-integrity scanning mixin for Postgres subworkflows."""

import hashlib
from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import dict_row

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.subworkflow_models import ParentReference
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_SUBWORKFLOW_DELETE_FAILED,
    PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.postgres.subworkflow_repo._base import _SubworkflowRepoBase
from synthorg.persistence.postgres.subworkflow_repo._marshalling import (
    extract_references,
)

if TYPE_CHECKING:
    from psycopg.rows import TupleRow

logger = get_logger(__name__)


class _ReferencesMixin(_SubworkflowRepoBase):
    """Parent-reference discovery + referenced-aware delete."""

    async def delete_if_unreferenced(
        self,
        subworkflow_id: NotBlankStr,
        version: NotBlankStr,
    ) -> tuple[bool, tuple[ParentReference, ...]]:
        """Atomically check-and-delete inside a single transaction.

        Uses a Postgres advisory lock keyed on the subworkflow
        coordinate to serialize with concurrent writers and prevent
        TOCTOU races under READ COMMITTED isolation.

        Returns:
            ``(deleted, parents)``: True if removed, plus referencing parents.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.transaction(),
            ):
                lock_key = (
                    int.from_bytes(
                        hashlib.sha256(f"{subworkflow_id}:{version}".encode()).digest()[
                            :4
                        ],
                        "big",
                    )
                    & 0x7FFFFFFF
                )
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    (lock_key,),
                )
                parents = await self._find_parents_with_conn(
                    conn,
                    subworkflow_id,
                    version,
                )
                if parents:
                    return False, parents
                result = await conn.execute(
                    "DELETE FROM subworkflows"
                    " WHERE subworkflow_id = %s AND semver = %s",
                    (subworkflow_id, version),
                )
        except psycopg.Error as exc:
            msg = f"Failed to delete_if_unreferenced {subworkflow_id!r}@{version!r}"
            logger.warning(
                PERSISTENCE_SUBWORKFLOW_DELETE_FAILED,
                subworkflow_id=subworkflow_id,
                version=version,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        deleted = result.rowcount > 0
        return deleted, ()

    async def find_parents(
        self,
        subworkflow_id: NotBlankStr,
        version: NotBlankStr | None = None,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ParentReference, ...]:
        """Return a bounded page of workflows referencing a subworkflow.

        Scans both ``workflow_definitions`` and ``subworkflows`` tables.
        References page in
        ``(parent_type, parent_id, node_id, pinned_version)`` order so
        a cursor walk is stable. Referential-integrity callers (the
        delete-if-unreferenced path) MUST drain every page via
        :func:`synthorg.persistence._shared.collect_all`; a truncated
        parent set would let a still-referenced version be deleted.

        Returns:
            Tuple of matching rows; empty when no rows match.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit,
            offset,
            event=PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
            subworkflow_id=subworkflow_id,
        )
        try:
            async with self._pool.connection() as conn:
                refs = await self._find_parents_with_conn(
                    conn,
                    subworkflow_id,
                    version,
                )
                # The reference scan walks JSON node arrays in both
                # ``workflow_definitions`` and ``subworkflows``; true
                # SQL-level pagination needs a normalized references
                # table (a schema change tracked separately). Paging in
                # memory is acceptable here because referential-
                # integrity callers MUST drain every page anyway, so
                # bounding per-page DB cost would yield no real saving.
                ordered = sorted(
                    refs,
                    key=lambda r: (
                        r.parent_type,
                        r.parent_id,
                        r.node_id,
                        r.pinned_version,
                    ),
                )
                return tuple(ordered[offset : offset + limit])
        except psycopg.Error as exc:
            msg = f"Failed to find parents for subworkflow {subworkflow_id!r}"
            logger.warning(
                PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
                subworkflow_id=subworkflow_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def _find_parents_with_conn(
        self,
        conn: psycopg.AsyncConnection[TupleRow],
        subworkflow_id: str,
        version: str | None,
    ) -> tuple[ParentReference, ...]:
        """Shared find_parents logic usable within an existing connection.

        Returns:
            The matching collection.
        """
        references: list[ParentReference] = []

        # Scan workflow_definitions table.
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, name, nodes FROM workflow_definitions",
            )
            wf_rows = await cur.fetchall()
        extract_references(
            wf_rows,
            subworkflow_id,
            version,
            parent_type="workflow_definition",
            id_column="id",
            references=references,
        )

        # Scan subworkflows table for nested references.
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT subworkflow_id, name, semver, nodes FROM subworkflows",
            )
            sub_rows = await cur.fetchall()
        extract_references(
            sub_rows,
            subworkflow_id,
            version,
            parent_type="subworkflow",
            id_column="subworkflow_id",
            version_column="semver",
            references=references,
        )

        return tuple(references)


__all__ = ["_ReferencesMixin"]
