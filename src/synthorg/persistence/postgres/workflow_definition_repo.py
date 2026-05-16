"""Postgres repository implementation for WorkflowDefinition.

Postgres-native port of ``synthorg.persistence.sqlite.workflow_definition_repo``.
Uses native JSONB for ``nodes`` and ``edges``, and native TIMESTAMPTZ for
``created_at`` / ``updated_at``. The protocol surface returns the same
Pydantic models as the SQLite backend.
"""

from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from synthorg.core.enums import WorkflowType
from synthorg.core.persistence_errors import (
    PersistenceVersionConflictError,
    QueryError,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIODeclaration,
    WorkflowNode,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_WORKFLOW_DEF_DELETE_FAILED,
    PERSISTENCE_WORKFLOW_DEF_DESERIALIZE_FAILED,
    PERSISTENCE_WORKFLOW_DEF_FETCH_FAILED,
    PERSISTENCE_WORKFLOW_DEF_FETCHED,
    PERSISTENCE_WORKFLOW_DEF_LIST_FAILED,
    PERSISTENCE_WORKFLOW_DEF_LISTED,
    PERSISTENCE_WORKFLOW_DEF_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared.pagination import (
    validate_pagination_args,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from synthorg.persistence.workflow_definition_protocol import (
        WorkflowDefinitionFilterSpec,
    )

logger = get_logger(__name__)

_SELECT_COLUMNS = """\
id, name, description, workflow_type, version, inputs, outputs,
is_subworkflow, nodes, edges, created_by, created_at, updated_at, revision"""


def _deserialize_row(
    row: dict[str, Any],
    context_id: str,
) -> WorkflowDefinition:
    """Reconstruct a ``WorkflowDefinition`` from a Postgres dict_row.

    Postgres returns JSONB as Python list/dict (no json.loads needed),
    and TIMESTAMPTZ as timezone-aware datetime. The main work is
    reconstructing the node/edge/input/output models and enums.

    Args:
        row: A single database row with workflow definition columns.
        context_id: Identifier for error context logging.

    Returns:
        Validated ``WorkflowDefinition`` model instance.

    Raises:
        QueryError: If deserialization fails.
    """
    try:
        data = dict(row)
        data["workflow_type"] = WorkflowType(data["workflow_type"])
        data["nodes"] = tuple(
            WorkflowNode.model_validate(n) for n in (data.get("nodes") or [])
        )
        data["edges"] = tuple(
            WorkflowEdge.model_validate(e) for e in (data.get("edges") or [])
        )
        data["inputs"] = tuple(
            WorkflowIODeclaration.model_validate(i) for i in (data.get("inputs") or [])
        )
        data["outputs"] = tuple(
            WorkflowIODeclaration.model_validate(o) for o in (data.get("outputs") or [])
        )
        data["is_subworkflow"] = bool(data.get("is_subworkflow", False))
        return WorkflowDefinition.model_validate(data)
    except (ValueError, ValidationError, KeyError, TypeError) as exc:
        msg = f"Failed to deserialize workflow definition {context_id!r}"
        logger.warning(
            PERSISTENCE_WORKFLOW_DEF_DESERIALIZE_FAILED,
            definition_id=context_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


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
        """
        if definition.revision < 1:
            msg = (
                f"Workflow definition revision must be >= 1, got"
                f" {definition.revision} for {definition.id!r}"
            )
            logger.warning(
                PERSISTENCE_WORKFLOW_DEF_SAVE_FAILED,
                definition_id=definition.id,
                error=msg,
            )
            raise QueryError(msg)

    async def update_if_exists(self, definition: WorkflowDefinition) -> bool:
        """Conditional UPDATE, returning ``False`` if the row is missing.

        See :meth:`WorkflowDefinitionRepository.update_if_exists`.
        Same optimistic-concurrency rule as :meth:`save`: UPDATE only
        applies when the stored row's ``revision`` equals
        ``definition.revision - 1``.
        """
        self._require_valid_revision(definition)
        nodes_jsonb = Jsonb([n.model_dump(mode="json") for n in definition.nodes])
        edges_jsonb = Jsonb([e.model_dump(mode="json") for e in definition.edges])
        inputs_jsonb = Jsonb([i.model_dump(mode="json") for i in definition.inputs])
        outputs_jsonb = Jsonb([o.model_dump(mode="json") for o in definition.outputs])
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE workflow_definitions SET
                        name=%s, description=%s, workflow_type=%s,
                        version=%s, inputs=%s, outputs=%s,
                        is_subworkflow=%s, nodes=%s, edges=%s,
                        updated_at=%s, revision=%s
                    WHERE id = %s AND revision = %s
                    """,
                    (
                        definition.name,
                        definition.description,
                        definition.workflow_type.value,
                        definition.version,
                        inputs_jsonb,
                        outputs_jsonb,
                        definition.is_subworkflow,
                        nodes_jsonb,
                        edges_jsonb,
                        definition.updated_at,
                        definition.revision,
                        definition.id,
                        definition.revision - 1,
                    ),
                )
                updated = cur.rowcount
                if updated == 0:
                    # Row either missing or at a different revision.
                    # Probe to distinguish the two cases so callers can
                    # surface precise errors (404 vs 409).
                    await cur.execute(
                        "SELECT revision FROM workflow_definitions WHERE id = %s",
                        (definition.id,),
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
                        definition_id=definition.id,
                        error=msg,
                    )
                    raise PersistenceVersionConflictError(msg)
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to update workflow definition {definition.id!r}"
            logger.warning(
                PERSISTENCE_WORKFLOW_DEF_SAVE_FAILED,
                definition_id=definition.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return True

    async def create_if_absent(self, definition: WorkflowDefinition) -> bool:
        """Atomic create-or-skip via ``INSERT ... ON CONFLICT DO NOTHING``.

        See :meth:`WorkflowDefinitionRepository.create_if_absent`.
        """
        self._require_valid_revision(definition)
        nodes_jsonb = Jsonb([n.model_dump(mode="json") for n in definition.nodes])
        edges_jsonb = Jsonb([e.model_dump(mode="json") for e in definition.edges])
        inputs_jsonb = Jsonb([i.model_dump(mode="json") for i in definition.inputs])
        outputs_jsonb = Jsonb([o.model_dump(mode="json") for o in definition.outputs])
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO workflow_definitions
                        (id, name, description, workflow_type, version,
                         inputs, outputs, is_subworkflow, nodes, edges,
                         created_by, created_at, updated_at, revision)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s)
                    ON CONFLICT(id) DO NOTHING
                    """,
                    (
                        definition.id,
                        definition.name,
                        definition.description,
                        definition.workflow_type.value,
                        definition.version,
                        inputs_jsonb,
                        outputs_jsonb,
                        definition.is_subworkflow,
                        nodes_jsonb,
                        edges_jsonb,
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
                definition_id=definition.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return inserted

    async def save(self, definition: WorkflowDefinition) -> None:
        """Persist a workflow definition via upsert.

        The upsert enforces optimistic concurrency: updates only
        succeed when the existing row's ``revision`` is exactly one
        behind the incoming ``revision``. ``version`` is a free-form
        semver string with no concurrency semantics.

        Args:
            definition: Workflow definition model to persist.

        Raises:
            QueryError: If the database operation fails.
            PersistenceVersionConflictError: If optimistic concurrency check fails.
        """
        self._require_valid_revision(definition)

        nodes_jsonb = Jsonb([n.model_dump(mode="json") for n in definition.nodes])
        edges_jsonb = Jsonb([e.model_dump(mode="json") for e in definition.edges])
        inputs_jsonb = Jsonb([i.model_dump(mode="json") for i in definition.inputs])
        outputs_jsonb = Jsonb([o.model_dump(mode="json") for o in definition.outputs])
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                if definition.revision > 1:
                    # Update path: optimistic concurrency via WHERE
                    # revision = incoming_revision - 1.  If no row exists
                    # at all this is also a revision conflict (you can't
                    # "update" a non-existent definition).
                    await cur.execute(
                        """
                        UPDATE workflow_definitions SET
                            name=%s, description=%s, workflow_type=%s,
                            version=%s, inputs=%s, outputs=%s,
                            is_subworkflow=%s, nodes=%s, edges=%s,
                            updated_at=%s, revision=%s
                        WHERE id = %s AND revision = %s
                        """,
                        (
                            definition.name,
                            definition.description,
                            definition.workflow_type.value,
                            definition.version,
                            inputs_jsonb,
                            outputs_jsonb,
                            definition.is_subworkflow,
                            nodes_jsonb,
                            edges_jsonb,
                            definition.updated_at,
                            definition.revision,
                            definition.id,
                            definition.revision - 1,
                        ),
                    )
                else:
                    # Create path: revision == 1.  ON CONFLICT DO NOTHING
                    # so a duplicate create attempt sets rowcount to 0.
                    await cur.execute(
                        """
                        INSERT INTO workflow_definitions
                            (id, name, description, workflow_type, version,
                             inputs, outputs, is_subworkflow, nodes, edges,
                             created_by, created_at, updated_at, revision)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s)
                        ON CONFLICT(id) DO NOTHING
                        """,
                        (
                            definition.id,
                            definition.name,
                            definition.description,
                            definition.workflow_type.value,
                            definition.version,
                            inputs_jsonb,
                            outputs_jsonb,
                            definition.is_subworkflow,
                            nodes_jsonb,
                            edges_jsonb,
                            definition.created_by,
                            definition.created_at,
                            definition.updated_at,
                            definition.revision,
                        ),
                    )
                if cur.rowcount == 0:
                    if definition.revision > 1:
                        msg = (
                            f"Revision conflict saving workflow definition"
                            f" {definition.id!r}: expected revision"
                            f" {definition.revision - 1}, not found"
                        )
                    else:
                        msg = (
                            f"Workflow definition {definition.id!r} already"
                            f" exists: cannot create revision 1 over an"
                            f" existing row"
                        )
                    logger.warning(
                        PERSISTENCE_WORKFLOW_DEF_SAVE_FAILED,
                        definition_id=definition.id,
                        error=msg,
                    )
                    raise PersistenceVersionConflictError(msg)
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to save workflow definition {definition.id!r}"
            logger.warning(
                PERSISTENCE_WORKFLOW_DEF_SAVE_FAILED,
                definition_id=definition.id,
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
                    f"SELECT {_SELECT_COLUMNS} FROM workflow_definitions WHERE id = %s",  # noqa: S608
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

        definition = _deserialize_row(row, definition_id)
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
        conditions: list[str] = []
        params: list[object] = []

        if filter_spec.workflow_type is not None:
            conditions.append("workflow_type = %s")
            params.append(filter_spec.workflow_type.value)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        sql = (
            f"SELECT {_SELECT_COLUMNS} FROM workflow_definitions"  # noqa: S608
            f"{where_clause} ORDER BY updated_at DESC LIMIT %s OFFSET %s"
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
            _deserialize_row(row, str(row.get("id", "?"))) for row in rows
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
        """List workflow definitions in id order (generic IdKeyed)."""
        from synthorg.persistence.workflow_definition_protocol import (  # noqa: PLC0415
            WorkflowDefinitionFilterSpec,
        )

        return await self.query(
            WorkflowDefinitionFilterSpec(),
            limit=limit,
            offset=offset,
        )

    async def count(self, filter_spec: WorkflowDefinitionFilterSpec) -> int:
        """Count workflow definitions matching the filter spec."""
        conditions: list[str] = []
        params: list[object] = []
        if filter_spec.workflow_type is not None:
            conditions.append("workflow_type = %s")
            params.append(filter_spec.workflow_type.value)
        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
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
