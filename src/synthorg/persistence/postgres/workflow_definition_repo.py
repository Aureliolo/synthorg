# module-kind: repository
"""Postgres repository implementation for WorkflowDefinition.

Postgres-native port of ``synthorg.persistence.sqlite.workflow_definition_repo``.
Uses native JSONB for ``nodes`` / ``edges`` and native TIMESTAMPTZ for
``created_at`` / ``updated_at``. Row <-> model marshalling is shared with
the SQLite sibling via
:mod:`synthorg.persistence._shared.workflow_definition_marshalling`.
"""

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import (
    PersistenceVersionConflictError,
    QueryError,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.definition import WorkflowDefinition
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.workflow_def import (
    PERSISTENCE_WORKFLOW_DEF_DELETE_FAILED,
    PERSISTENCE_WORKFLOW_DEF_FETCH_FAILED,
    PERSISTENCE_WORKFLOW_DEF_FETCHED,
    PERSISTENCE_WORKFLOW_DEF_LIST_FAILED,
    PERSISTENCE_WORKFLOW_DEF_LISTED,
    PERSISTENCE_WORKFLOW_DEF_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence._shared.workflow_definition_marshalling import (
    WORKFLOW_DEFINITION_COLUMNS,
    build_workflow_definition_where,
    definition_jsonb_payloads,
    row_to_workflow_definition,
)
from synthorg.persistence.workflow_definition_protocol import (
    WorkflowDefinitionFilterSpec,
)

logger = get_logger(__name__)

_UPDATE_SQL = """
UPDATE workflow_definitions SET
    name=%s, description=%s, workflow_type=%s, version=%s, inputs=%s, outputs=%s,
    is_subworkflow=%s, nodes=%s, edges=%s, updated_at=%s, revision=%s
WHERE id = %s AND revision = %s
"""

_INSERT_SQL = """
INSERT INTO workflow_definitions
    (id, name, description, workflow_type, version, inputs, outputs,
     is_subworkflow, nodes, edges, created_by, created_at, updated_at, revision)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT(id) DO NOTHING
"""


def _jsonb_columns(
    definition: WorkflowDefinition,
) -> tuple[Jsonb, Jsonb, Jsonb, Jsonb]:
    """Wrap the node/edge/input/output payloads as ``Jsonb`` params.

    Returns:
        ``(nodes, edges, inputs, outputs)`` as psycopg ``Jsonb`` values.
    """
    nodes, edges, inputs, outputs = definition_jsonb_payloads(definition)
    return Jsonb(nodes), Jsonb(edges), Jsonb(inputs), Jsonb(outputs)


class PostgresWorkflowDefinitionRepository:
    """Postgres-backed workflow definition repository.

    Provides CRUD operations for ``WorkflowDefinition`` models using
    a shared ``psycopg_pool.AsyncConnectionPool``. Nodes and edges are
    stored as JSONB. All write operations commit immediately.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    def _require_valid_revision(self, definition: WorkflowDefinition) -> None:
        """Reject obviously-invalid revisions before hitting the DB.

        Shared between :meth:`save`, :meth:`update_if_exists`, and
        :meth:`create_if_absent` so all three write paths fail fast with
        the same ``QueryError`` instead of hitting the ``revision >= 1``
        CHECK constraint and surfacing a generic driver error.

        Raises:
            QueryError: If ``definition.revision`` is less than 1.
        """
        if definition.revision < 1:
            msg = (
                f"Workflow definition revision must be >= 1, got"
                f" {definition.revision} for {definition.id!r}"
            )
            logger.warning(
                PERSISTENCE_WORKFLOW_DEF_SAVE_FAILED,
                definition_id=str(definition.id),
                error=msg,
            )
            raise QueryError(msg)

    async def update_if_exists(self, definition: WorkflowDefinition) -> bool:
        """Conditional UPDATE, returning ``False`` if the row is missing.

        See :meth:`WorkflowDefinitionRepository.update_if_exists`.
        Same optimistic-concurrency rule as :meth:`save`: UPDATE only
        applies when the stored row's ``revision`` equals
        ``definition.revision - 1``.

        Returns:
            True when the operation succeeded, False otherwise.

        Raises:
            QueryError: If the database query fails.
            PersistenceVersionConflictError: If the row version no longer matches.
        """
        self._require_valid_revision(definition)
        nodes, edges, inputs, outputs = _jsonb_columns(definition)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    _UPDATE_SQL,
                    (
                        definition.name,
                        definition.description,
                        definition.workflow_type.value,
                        definition.version,
                        inputs,
                        outputs,
                        definition.is_subworkflow,
                        nodes,
                        edges,
                        definition.updated_at,
                        definition.revision,
                        str(definition.id),
                        definition.revision - 1,
                    ),
                )
                if cur.rowcount == 0:
                    # Row either missing or at a different revision.
                    # Probe to distinguish the two cases so callers can
                    # surface precise errors (404 vs 409).
                    await cur.execute(
                        "SELECT revision FROM workflow_definitions WHERE id = %s",
                        (str(definition.id),),
                    )
                    probe = await cur.fetchone()
                    if probe is None:
                        await conn.rollback()
                        return False
                    msg = (
                        f"Version conflict updating workflow definition"
                        f" {definition.id!r}: current revision is {probe[0]},"
                        f" incoming revision is {definition.revision}"
                    )
                    await conn.rollback()
                    logger.warning(
                        PERSISTENCE_WORKFLOW_DEF_SAVE_FAILED,
                        definition_id=str(definition.id),
                        error=msg,
                    )
                    raise PersistenceVersionConflictError(msg)
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to update workflow definition {definition.id!r}"
            logger.warning(
                PERSISTENCE_WORKFLOW_DEF_SAVE_FAILED,
                definition_id=str(definition.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return True

    async def create_if_absent(self, definition: WorkflowDefinition) -> bool:
        """Atomic create-or-skip via ``INSERT ... ON CONFLICT DO NOTHING``.

        See :meth:`WorkflowDefinitionRepository.create_if_absent`.

        Returns:
            True when the row was inserted, False when an existing row blocked it.

        Raises:
            QueryError: If the database query fails.
        """
        self._require_valid_revision(definition)
        nodes, edges, inputs, outputs = _jsonb_columns(definition)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    _INSERT_SQL,
                    (
                        str(definition.id),
                        definition.name,
                        definition.description,
                        definition.workflow_type.value,
                        definition.version,
                        inputs,
                        outputs,
                        definition.is_subworkflow,
                        nodes,
                        edges,
                        definition.created_by,
                        definition.created_at,
                        definition.updated_at,
                        definition.revision,
                    ),
                )
                inserted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to create workflow definition {definition.id!r}"
            logger.warning(
                PERSISTENCE_WORKFLOW_DEF_SAVE_FAILED,
                definition_id=str(definition.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return inserted

    async def save(self, entity: WorkflowDefinition) -> None:
        """Persist a workflow definition via upsert.

        The upsert enforces optimistic concurrency: updates only
        succeed when the existing row's ``revision`` is exactly one
        behind the incoming ``revision``. ``version`` is a free-form
        semver string with no concurrency semantics.

        Args:
            entity: Workflow definition model to persist.

        Raises:
            QueryError: If the database operation fails.
            PersistenceVersionConflictError: If optimistic concurrency check fails.
        """
        self._require_valid_revision(entity)
        nodes, edges, inputs, outputs = _jsonb_columns(entity)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                if entity.revision > 1:
                    # Update path: optimistic concurrency via WHERE
                    # revision = incoming_revision - 1.  If no row exists
                    # at all this is also a revision conflict (you can't
                    # "update" a non-existent definition).
                    await cur.execute(
                        _UPDATE_SQL,
                        (
                            entity.name,
                            entity.description,
                            entity.workflow_type.value,
                            entity.version,
                            inputs,
                            outputs,
                            entity.is_subworkflow,
                            nodes,
                            edges,
                            entity.updated_at,
                            entity.revision,
                            str(entity.id),
                            entity.revision - 1,
                        ),
                    )
                else:
                    # Create path: revision == 1.  ON CONFLICT DO NOTHING
                    # so a duplicate create attempt sets rowcount to 0.
                    await cur.execute(
                        _INSERT_SQL,
                        (
                            str(entity.id),
                            entity.name,
                            entity.description,
                            entity.workflow_type.value,
                            entity.version,
                            inputs,
                            outputs,
                            entity.is_subworkflow,
                            nodes,
                            edges,
                            entity.created_by,
                            entity.created_at,
                            entity.updated_at,
                            entity.revision,
                        ),
                    )
                if cur.rowcount == 0:
                    if entity.revision > 1:
                        msg = (
                            f"Revision conflict saving workflow definition"
                            f" {entity.id!r}: expected revision"
                            f" {entity.revision - 1}, not found"
                        )
                    else:
                        msg = (
                            f"Workflow definition {entity.id!r} already"
                            f" exists: cannot create revision 1 over an"
                            f" existing row"
                        )
                    logger.warning(
                        PERSISTENCE_WORKFLOW_DEF_SAVE_FAILED,
                        definition_id=str(entity.id),
                        error=msg,
                    )
                    raise PersistenceVersionConflictError(msg)
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to save workflow definition {entity.id!r}"
            logger.warning(
                PERSISTENCE_WORKFLOW_DEF_SAVE_FAILED,
                definition_id=str(entity.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(
        self,
        definition_id: NotBlankStr,
    ) -> WorkflowDefinition | None:
        """Retrieve a workflow definition by primary key.

        Args:
            definition_id: Unique workflow definition identifier.

        Returns:
            The matching definition, or ``None`` if not found.

        Raises:
            QueryError: If the database query or deserialization fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {WORKFLOW_DEFINITION_COLUMNS} FROM workflow_definitions WHERE id = %s",  # noqa: S608, E501
                    (definition_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch workflow definition {definition_id!r}"
            logger.warning(
                PERSISTENCE_WORKFLOW_DEF_FETCH_FAILED,
                definition_id=definition_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        if row is None:
            logger.debug(
                PERSISTENCE_WORKFLOW_DEF_FETCHED,
                definition_id=definition_id,
                found=False,
            )
            return None

        definition = row_to_workflow_definition(dict(row), definition_id)
        logger.debug(
            PERSISTENCE_WORKFLOW_DEF_FETCHED,
            definition_id=definition_id,
            found=True,
        )
        return definition

    async def query(
        self,
        filter_spec: WorkflowDefinitionFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[WorkflowDefinition, ...]:
        """List workflow definitions matching the filter spec.

        Args:
            filter_spec: Carries optional ``workflow_type`` filter.
            limit: Maximum definitions to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Matching definitions, ordered by ``updated_at`` descending.

        Raises:
            QueryError: If the database query, deserialization, or
                pagination validation fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_WORKFLOW_DEF_LIST_FAILED
        )
        where_clause, params = build_workflow_definition_where(
            filter_spec, placeholder="%s"
        )
        sql = (
            f"SELECT {WORKFLOW_DEFINITION_COLUMNS} FROM workflow_definitions"  # noqa: S608
            f"{where_clause} ORDER BY updated_at DESC, id DESC "
            "LIMIT %s OFFSET %s"
        )
        params.extend([limit, offset])

        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list workflow definitions"
            logger.warning(
                PERSISTENCE_WORKFLOW_DEF_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        definitions = tuple(
            row_to_workflow_definition(dict(row), str(row.get("id", "?")))
            for row in rows
        )
        logger.debug(
            PERSISTENCE_WORKFLOW_DEF_LISTED,
            count=len(definitions),
        )
        return definitions

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[WorkflowDefinition, ...]:
        """List workflow definitions in ascending id order.

        The :class:`IdKeyedRepository` contract requires a deterministic
        id ordering; ``query`` uses recency (``updated_at DESC``) so
        this cannot delegate to it.

        Raises:
            QueryError: If the database query, deserialization, or
                pagination validation fails.

        Returns:
            The matching entities.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_WORKFLOW_DEF_LIST_FAILED
        )
        sql = (
            f"SELECT {WORKFLOW_DEFINITION_COLUMNS} FROM workflow_definitions "  # noqa: S608
            "ORDER BY id ASC LIMIT %s OFFSET %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (limit, offset))
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list workflow definitions"
            logger.warning(
                PERSISTENCE_WORKFLOW_DEF_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        return tuple(
            row_to_workflow_definition(dict(row), str(row.get("id", "?")))
            for row in rows
        )

    async def count(self, filter_spec: WorkflowDefinitionFilterSpec) -> int:
        """Count workflow definitions matching the filter spec.

        Returns:
            Number of matching rows.

        Raises:
            QueryError: If the database query fails.
        """
        where_clause, params = build_workflow_definition_where(
            filter_spec, placeholder="%s"
        )
        sql = (
            f"SELECT COUNT(*) FROM workflow_definitions"  # noqa: S608
            f"{where_clause}"
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = "Failed to count workflow definitions"
            logger.warning(
                PERSISTENCE_WORKFLOW_DEF_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return int(row[0]) if row else 0

    async def delete(self, definition_id: NotBlankStr) -> bool:
        """Delete a workflow definition by primary key.

        Args:
            definition_id: Unique workflow definition identifier.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the database operation fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM workflow_definitions WHERE id = %s",
                    (definition_id,),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete workflow definition {definition_id!r}"
            logger.warning(
                PERSISTENCE_WORKFLOW_DEF_DELETE_FAILED,
                definition_id=definition_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        return deleted


__all__ = ["PostgresWorkflowDefinitionRepository"]
