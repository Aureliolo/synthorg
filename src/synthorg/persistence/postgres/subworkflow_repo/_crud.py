"""CRUD + query mixin for the Postgres subworkflow repository."""

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.definition import WorkflowDefinition
from synthorg.engine.workflow.subworkflow_models import SubworkflowSummary
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.subworkflow import (
    PERSISTENCE_SUBWORKFLOW_DELETE_FAILED,
    PERSISTENCE_SUBWORKFLOW_FETCH_FAILED,
    PERSISTENCE_SUBWORKFLOW_FETCHED,
    PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
    PERSISTENCE_SUBWORKFLOW_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.postgres.subworkflow_repo._base import _SubworkflowRepoBase
from synthorg.persistence.postgres.subworkflow_repo._marshalling import (
    SUBWORKFLOW_COLUMNS,
    build_summaries_from_rows,
    deserialize_row,
    semver_sort_key,
)

logger = get_logger(__name__)


class _CrudMixin(_SubworkflowRepoBase):
    """Insert / fetch / list / search / delete for subworkflow versions."""

    async def save(self, entity: WorkflowDefinition) -> None:
        """Insert a new subworkflow version row.

        Raises:
            DuplicateRecordError: If a row with the same key already exists.
            QueryError: If the database query fails.
        """
        nodes = [n.model_dump(mode="json") for n in entity.nodes]
        edges = [e.model_dump(mode="json") for e in entity.edges]
        inputs = [i.model_dump(mode="json") for i in entity.inputs]
        outputs = [o.model_dump(mode="json") for o in entity.outputs]
        try:
            async with self._pool.connection() as conn:
                await conn.execute(
                    """\
INSERT INTO subworkflows
    (subworkflow_id, semver, name, description, workflow_type,
     inputs, outputs, nodes, edges, created_by, created_at, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        str(entity.id),
                        entity.version,
                        entity.name,
                        entity.description,
                        entity.workflow_type.value,
                        Jsonb(inputs),
                        Jsonb(outputs),
                        Jsonb(nodes),
                        Jsonb(edges),
                        entity.created_by,
                        entity.created_at,
                        entity.updated_at,
                    ),
                )
        except psycopg.errors.UniqueViolation as exc:
            msg = f"Subworkflow {entity.id!r} version {entity.version!r} already exists"
            logger.warning(
                PERSISTENCE_SUBWORKFLOW_SAVE_FAILED,
                subworkflow_id=str(entity.id),
                version=entity.version,
                error=msg,
            )
            raise DuplicateRecordError(msg) from exc
        except psycopg.Error as exc:
            msg = f"Failed to save subworkflow {entity.id!r} version {entity.version!r}"
            logger.warning(
                PERSISTENCE_SUBWORKFLOW_SAVE_FAILED,
                subworkflow_id=str(entity.id),
                version=entity.version,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(
        self,
        entity_id: tuple[NotBlankStr, NotBlankStr],
    ) -> WorkflowDefinition | None:
        """Fetch a specific subworkflow version by composite key.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        subworkflow_id, version = entity_id
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {SUBWORKFLOW_COLUMNS} FROM subworkflows"  # noqa: S608
                    " WHERE subworkflow_id = %s AND semver = %s",
                    (subworkflow_id, version),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch subworkflow {subworkflow_id!r}@{version!r}"
            logger.warning(
                PERSISTENCE_SUBWORKFLOW_FETCH_FAILED,
                subworkflow_id=subworkflow_id,
                version=version,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        if row is None:
            return None
        logger.debug(
            PERSISTENCE_SUBWORKFLOW_FETCHED,
            subworkflow_id=subworkflow_id,
            version=version,
        )
        return deserialize_row(row, f"{subworkflow_id}@{version}")

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[WorkflowDefinition, ...]:
        """List subworkflows by composite key in ascending order (paginated).

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_SUBWORKFLOW_LIST_FAILED
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {SUBWORKFLOW_COLUMNS} FROM subworkflows "  # noqa: S608
                    "ORDER BY subworkflow_id, semver LIMIT %s OFFSET %s",
                    (limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list subworkflows"
            logger.warning(
                PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(deserialize_row(row, str(row["subworkflow_id"])) for row in rows)

    async def list_versions(
        self,
        subworkflow_id: NotBlankStr,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[NotBlankStr, ...]:
        """List semver strings for a subworkflow, newest first.

        Bounded by *limit* (default :data:`DEFAULT_PAGE_SIZE`).

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, 0, event=PERSISTENCE_SUBWORKFLOW_LIST_FAILED
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT semver FROM subworkflows WHERE subworkflow_id = %s",
                    (subworkflow_id,),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = f"Failed to list versions for {subworkflow_id!r}"
            logger.warning(
                PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
                subworkflow_id=subworkflow_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        # Sort by semver in Python so the "newest first" contract holds
        # against semver order (which can diverge from created_at order),
        # then apply the caller's page size.
        versions = [str(r["semver"]) for r in rows]
        versions.sort(key=semver_sort_key, reverse=True)
        return tuple(versions[:limit])

    async def list_summaries(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[SubworkflowSummary, ...]:
        """Return summaries (latest version per subworkflow).

        Bounded by *limit* distinct subworkflow ids. The subquery
        selects the first *limit* unique subworkflow_ids; the outer
        SELECT then fetches every version row for those ids so the
        client-side aggregator still sees the full version set per
        included subworkflow.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, 0, event=PERSISTENCE_SUBWORKFLOW_LIST_FAILED
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {SUBWORKFLOW_COLUMNS} FROM subworkflows "  # noqa: S608
                    "WHERE subworkflow_id IN ("
                    "SELECT DISTINCT subworkflow_id FROM subworkflows "
                    "ORDER BY subworkflow_id LIMIT %s"
                    ")",
                    (limit,),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list subworkflow summaries"
            logger.warning(
                PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        return build_summaries_from_rows(rows)

    async def search(
        self,
        query: NotBlankStr,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[SubworkflowSummary, ...]:
        """Return a bounded page of summaries matching a substring.

        Summaries page in ``subworkflow_id`` order so a cursor walk is
        stable; callers needing every match drain via
        :func:`synthorg.persistence._shared.collect_all`.

        Returns:
            The matching collection.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_SUBWORKFLOW_LIST_FAILED, query=query
        )
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                # A summary aggregates every version row of a subworkflow
                # into one entry, so the page boundary is the distinct
                # ``subworkflow_id`` set, not raw rows. Page the ids at
                # the DB first, then fetch only that page's rows: this
                # bounds both scan cost and the rows materialised in
                # memory to roughly ``limit * versions_per_subworkflow``.
                await cur.execute(
                    "SELECT subworkflow_id FROM subworkflows"
                    " WHERE name ILIKE %s ESCAPE '\\'"
                    " OR description ILIKE %s ESCAPE '\\'"
                    " GROUP BY subworkflow_id"
                    " ORDER BY subworkflow_id LIMIT %s OFFSET %s",
                    (pattern, pattern, limit, offset),
                )
                page_ids = [str(r["subworkflow_id"]) for r in await cur.fetchall()]
                if not page_ids:
                    return ()
                await cur.execute(
                    f"SELECT {SUBWORKFLOW_COLUMNS} FROM subworkflows"  # noqa: S608
                    " WHERE subworkflow_id = ANY(%s)",
                    (page_ids,),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = f"Failed to search subworkflows for {query!r}"
            logger.warning(
                PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
                query=query,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        return build_summaries_from_rows(rows)

    async def delete(
        self,
        entity_id: tuple[NotBlankStr, NotBlankStr],
    ) -> bool:
        """Delete a subworkflow version by composite key.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        subworkflow_id, version = entity_id
        try:
            async with self._pool.connection() as conn:
                result = await conn.execute(
                    "DELETE FROM subworkflows"
                    " WHERE subworkflow_id = %s AND semver = %s",
                    (subworkflow_id, version),
                )
        except psycopg.Error as exc:
            msg = f"Failed to delete subworkflow {subworkflow_id!r}@{version!r}"
            logger.warning(
                PERSISTENCE_SUBWORKFLOW_DELETE_FAILED,
                subworkflow_id=subworkflow_id,
                version=version,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        return result.rowcount > 0


__all__ = ["_CrudMixin"]
