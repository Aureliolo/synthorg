# module-kind: repository
"""SQLite repository implementation for WorkflowDefinition.

Nodes/edges/inputs/outputs are stored as JSON arrays and timestamps as
ISO TEXT. Row <-> model marshalling is shared with the Postgres sibling
via :mod:`synthorg.persistence._shared.workflow_definition_marshalling`;
the SQL statements live in
:mod:`synthorg.persistence.sqlite._workflow_definition_sql`.
"""

import sqlite3
from datetime import UTC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

    from synthorg.persistence.workflow_definition_protocol import (
        WorkflowDefinitionFilterSpec,
    )

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
    row_to_workflow_definition,
    serialize_definition_columns,
)
from synthorg.persistence.sqlite._shared import WriteContext
from synthorg.persistence.sqlite._workflow_definition_sql import (
    INSERT_IGNORE_SQL,
    UPDATE_SQL,
    UPSERT_SQL,
)

logger = get_logger(__name__)


async def _rollback_quietly(db: aiosqlite.Connection) -> None:
    """Roll back the shared aiosqlite connection, swallowing any errors.

    The repository's write paths share a single connection, so an error
    between ``execute`` and ``commit`` leaves the transaction open. Call
    this from every ``except sqlite3.Error`` handler to avoid handing
    the next caller a poisoned transaction. Rollback errors are logged
    but not re-raised -- the outer handler is already raising a
    ``QueryError`` that carries the original failure context.
    """
    try:
        await db.rollback()
    except sqlite3.Error as rollback_exc:
        logger.debug(
            PERSISTENCE_WORKFLOW_DEF_SAVE_FAILED,
            stage="rollback_suppressed",
            error_type=type(rollback_exc).__name__,
            error=safe_error_description(rollback_exc),
        )


class SQLiteWorkflowDefinitionRepository:
    """SQLite-backed workflow definition repository.

    Provides CRUD operations for ``WorkflowDefinition`` models using
    a shared ``aiosqlite.Connection``.  Nodes and edges are stored as
    JSON arrays.  All write operations commit immediately.

    Args:
        db: An open aiosqlite connection with ``row_factory``
            set to ``aiosqlite.Row``.
        write_context: Async context manager that serializes writes on
            the shared connection. Supplied by
            ``SQLitePersistenceBackend.write_context`` in production;
            tests can pass
            ``tests._shared.persistence.make_private_write_context()``
            for standalone construction.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    def _require_valid_revision(self, definition: WorkflowDefinition) -> None:
        """Reject obviously-invalid revisions before hitting the DB.

        Shared between :meth:`save`, :meth:`update_if_exists`, and
        :meth:`create_if_absent` so every write path fails fast with a
        descriptive ``QueryError`` rather than bubbling a generic SQLite
        CHECK-constraint error to the caller.

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
                definition_id=definition.id,
                error=msg,
            )
            raise QueryError(msg)

    async def update_if_exists(self, definition: WorkflowDefinition) -> bool:
        """Conditional UPDATE, returning ``False`` if the row is missing.

        See :meth:`WorkflowDefinitionRepository.update_if_exists`.
        Enforces the same optimistic-concurrency rule as
        :meth:`save`: the UPDATE only applies when the stored row's
        ``revision`` equals ``definition.revision - 1``; otherwise a
        ``PersistenceVersionConflictError`` is raised so callers
        distinguish "row missing" (``False``) from "row changed
        concurrently".

        Returns:
            True when the operation succeeded, False otherwise.

        Raises:
            PersistenceVersionConflictError: If the row version no longer matches.
            QueryError: If the database query fails.
        """
        self._require_valid_revision(definition)
        nodes_json, edges_json, inputs_json, outputs_json = (
            serialize_definition_columns(definition)
        )
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    UPDATE_SQL,
                    (
                        definition.name,
                        definition.description,
                        definition.workflow_type.value,
                        definition.version,
                        inputs_json,
                        outputs_json,
                        1 if definition.is_subworkflow else 0,
                        nodes_json,
                        edges_json,
                        definition.updated_at.astimezone(UTC).isoformat(),
                        definition.revision,
                        definition.id,
                        definition.revision - 1,
                    ),
                )
                if cursor.rowcount == 0:
                    # Distinguish "row missing" from "row exists with a
                    # different revision" so callers get a precise error.
                    probe = await self._db.execute(
                        "SELECT revision FROM workflow_definitions WHERE id = ?",
                        (definition.id,),
                    )
                    existing = await probe.fetchone()
                    await self._db.rollback()
                    if existing is None:
                        return False
                    current = existing["revision"]
                    msg = (
                        f"Version conflict updating workflow definition"
                        f" {definition.id!r}: current revision is {current},"
                        f" incoming revision is {definition.revision}"
                    )
                    logger.warning(
                        PERSISTENCE_WORKFLOW_DEF_SAVE_FAILED,
                        definition_id=definition.id,
                        error=msg,
                    )
                    raise PersistenceVersionConflictError(msg)
                await self._db.commit()
            except sqlite3.Error as exc:
                # Roll back the aiosqlite transaction so the shared
                # connection cannot be poisoned for the next borrower.
                await _rollback_quietly(self._db)
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

        Returns:
            True when the row was inserted, False when an existing row blocked it.

        Raises:
            QueryError: If the database query fails.
        """
        self._require_valid_revision(definition)
        nodes_json, edges_json, inputs_json, outputs_json = (
            serialize_definition_columns(definition)
        )
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    INSERT_IGNORE_SQL,
                    (
                        definition.id,
                        definition.name,
                        definition.description,
                        definition.workflow_type.value,
                        definition.version,
                        inputs_json,
                        outputs_json,
                        1 if definition.is_subworkflow else 0,
                        nodes_json,
                        edges_json,
                        definition.created_by,
                        definition.created_at.astimezone(UTC).isoformat(),
                        definition.updated_at.astimezone(UTC).isoformat(),
                        definition.revision,
                    ),
                )
                await self._db.commit()
            except sqlite3.Error as exc:
                await _rollback_quietly(self._db)
                msg = f"Failed to create workflow definition {definition.id!r}"
                logger.warning(
                    PERSISTENCE_WORKFLOW_DEF_SAVE_FAILED,
                    definition_id=definition.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return cursor.rowcount > 0

    async def save(self, entity: WorkflowDefinition) -> None:
        """Persist a workflow definition via upsert.

        The upsert enforces optimistic concurrency: updates only
        succeed when the existing row's version is exactly one
        behind the incoming version.

        Args:
            entity: Workflow definition model to persist.

        Raises:
            QueryError: If the database operation fails or the
                ``revision`` value is invalid (see
                :meth:`_require_valid_revision`).
            PersistenceVersionConflictError: If the row version no longer matches.
        """
        self._require_valid_revision(entity)
        nodes_json, edges_json, inputs_json, outputs_json = (
            serialize_definition_columns(entity)
        )
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    UPSERT_SQL,
                    (
                        entity.id,
                        entity.name,
                        entity.description,
                        entity.workflow_type.value,
                        entity.version,
                        inputs_json,
                        outputs_json,
                        1 if entity.is_subworkflow else 0,
                        nodes_json,
                        edges_json,
                        entity.created_by,
                        entity.created_at.astimezone(UTC).isoformat(),
                        entity.updated_at.astimezone(UTC).isoformat(),
                        entity.revision,
                    ),
                )
                if cursor.rowcount == 0:
                    # Zero rows affected means the ON CONFLICT WHERE clause
                    # did not match -- the existing row has a different
                    # revision than expected.
                    check = await self._db.execute(
                        "SELECT revision FROM workflow_definitions WHERE id = ?",
                        (entity.id,),
                    )
                    existing = await check.fetchone()
                    await self._db.rollback()
                    current = existing["revision"] if existing else "N/A"
                    msg = (
                        f"Version conflict saving workflow definition"
                        f" {entity.id!r}: current revision is"
                        f" {current}, incoming revision is"
                        f" {entity.revision}"
                    )
                    logger.warning(
                        PERSISTENCE_WORKFLOW_DEF_SAVE_FAILED,
                        definition_id=entity.id,
                        error=msg,
                    )
                    raise PersistenceVersionConflictError(msg)
                await self._db.commit()
            except sqlite3.Error as exc:
                await _rollback_quietly(self._db)
                msg = f"Failed to save workflow definition {entity.id!r}"
                logger.warning(
                    PERSISTENCE_WORKFLOW_DEF_SAVE_FAILED,
                    definition_id=entity.id,
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
            cursor = await self._db.execute(
                f"SELECT {WORKFLOW_DEFINITION_COLUMNS} FROM workflow_definitions WHERE id = ?",  # noqa: S608, E501
                (definition_id,),
            )
            row = await cursor.fetchone()
        except sqlite3.Error as exc:
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
            filter_spec, placeholder="?"
        )
        sql = (
            f"SELECT {WORKFLOW_DEFINITION_COLUMNS} FROM workflow_definitions"  # noqa: S608
            f"{where_clause} ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])

        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
        except sqlite3.Error as exc:
            msg = "Failed to list workflow definitions"
            logger.warning(
                PERSISTENCE_WORKFLOW_DEF_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        definitions = tuple(
            row_to_workflow_definition(dict(row), str(dict(row).get("id", "?")))
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
            "ORDER BY id ASC LIMIT ? OFFSET ?"
        )
        try:
            cursor = await self._db.execute(sql, (limit, offset))
            rows = await cursor.fetchall()
        except sqlite3.Error as exc:
            msg = "Failed to list workflow definitions"
            logger.warning(
                PERSISTENCE_WORKFLOW_DEF_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        return tuple(
            row_to_workflow_definition(dict(row), str(dict(row).get("id", "?")))
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
            filter_spec, placeholder="?"
        )
        sql = (
            f"SELECT COUNT(*) FROM workflow_definitions"  # noqa: S608
            f"{where_clause}"
        )
        try:
            cursor = await self._db.execute(sql, params)
            row = await cursor.fetchone()
        except sqlite3.Error as exc:
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
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM workflow_definitions WHERE id = ?",
                    (definition_id,),
                )
                await self._db.commit()
            except sqlite3.Error as exc:
                await _rollback_quietly(self._db)
                msg = f"Failed to delete workflow definition {definition_id!r}"
                logger.warning(
                    PERSISTENCE_WORKFLOW_DEF_DELETE_FAILED,
                    definition_id=definition_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

        return cursor.rowcount > 0


__all__ = ["SQLiteWorkflowDefinitionRepository"]
