"""SQLite repository implementation for WorkflowDefinition."""

import json
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import ValidationError

if TYPE_CHECKING:
    import aiosqlite

    from synthorg.persistence.workflow_definition_protocol import (
        WorkflowDefinitionFilterSpec,
    )

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
from synthorg.persistence.sqlite._shared import WriteContext  # noqa: TC001

logger = get_logger(__name__)

_SELECT_COLUMNS = """\
id, name, description, workflow_type, version, inputs, outputs,
is_subworkflow, nodes, edges, created_by, created_at, updated_at, revision"""


def _parse_row_timestamps(data: dict[str, object]) -> None:
    """Parse ISO timestamps and ensure timezone awareness."""
    for field in ("created_at", "updated_at"):
        dt = datetime.fromisoformat(str(data[field]))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        data[field] = dt


def _deserialize_row(
    row: aiosqlite.Row,
    context_id: str,
) -> WorkflowDefinition:
    """Reconstruct a ``WorkflowDefinition`` from a database row.

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
            WorkflowNode.model_validate(n) for n in json.loads(data["nodes"])
        )
        data["edges"] = tuple(
            WorkflowEdge.model_validate(e) for e in json.loads(data["edges"])
        )
        data["inputs"] = tuple(
            WorkflowIODeclaration.model_validate(i) for i in json.loads(data["inputs"])
        )
        data["outputs"] = tuple(
            WorkflowIODeclaration.model_validate(o) for o in json.loads(data["outputs"])
        )
        data["is_subworkflow"] = bool(data["is_subworkflow"])
        _parse_row_timestamps(data)
        return WorkflowDefinition.model_validate(data)
    except (ValueError, ValidationError, json.JSONDecodeError, KeyError) as exc:
        msg = f"Failed to deserialize workflow definition {context_id!r}"
        logger.warning(
            PERSISTENCE_WORKFLOW_DEF_DESERIALIZE_FAILED,
            definition_id=context_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


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
            QueryError: If the database query fails.
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
            The updated entity.

        Raises:
            PersistenceVersionConflictError: If the row version no longer matches.
            QueryError: If the database query fails.
        """
        self._require_valid_revision(definition)
        nodes_json = json.dumps(
            [n.model_dump(mode="json") for n in definition.nodes],
        )
        edges_json = json.dumps(
            [e.model_dump(mode="json") for e in definition.edges],
        )
        inputs_json = json.dumps(
            [i.model_dump(mode="json") for i in definition.inputs],
        )
        outputs_json = json.dumps(
            [o.model_dump(mode="json") for o in definition.outputs],
        )
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    """\
UPDATE workflow_definitions SET
    name=?, description=?, workflow_type=?, version=?, inputs=?, outputs=?,
    is_subworkflow=?, nodes=?, edges=?, updated_at=?, revision=?
WHERE id = ? AND revision = ?""",
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
            The newly created entity.

        Raises:
            QueryError: If the database query fails.
        """
        self._require_valid_revision(definition)
        nodes_json = json.dumps(
            [n.model_dump(mode="json") for n in definition.nodes],
        )
        edges_json = json.dumps(
            [e.model_dump(mode="json") for e in definition.edges],
        )
        inputs_json = json.dumps(
            [i.model_dump(mode="json") for i in definition.inputs],
        )
        outputs_json = json.dumps(
            [o.model_dump(mode="json") for o in definition.outputs],
        )
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    """\
INSERT INTO workflow_definitions
    (id, name, description, workflow_type, version, inputs, outputs,
     is_subworkflow, nodes, edges, created_by, created_at, updated_at,
     revision)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO NOTHING""",
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

    async def save(self, definition: WorkflowDefinition) -> None:
        """Persist a workflow definition via upsert.

        The upsert enforces optimistic concurrency: updates only
        succeed when the existing row's version is exactly one
        behind the incoming version.

        Args:
            definition: Workflow definition model to persist.

        Raises:
            QueryError: If the database operation fails or the
                ``revision`` value is invalid (see
                :meth:`_require_valid_revision`).
            PersistenceVersionConflictError: If the row version no longer matches.
        """
        self._require_valid_revision(definition)
        nodes_json = json.dumps(
            [n.model_dump(mode="json") for n in definition.nodes],
        )
        edges_json = json.dumps(
            [e.model_dump(mode="json") for e in definition.edges],
        )
        inputs_json = json.dumps(
            [i.model_dump(mode="json") for i in definition.inputs],
        )
        outputs_json = json.dumps(
            [o.model_dump(mode="json") for o in definition.outputs],
        )
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    """\
INSERT INTO workflow_definitions
    (id, name, description, workflow_type, version, inputs, outputs,
     is_subworkflow, nodes, edges, created_by, created_at, updated_at,
     revision)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    name=excluded.name,
    description=excluded.description,
    workflow_type=excluded.workflow_type,
    version=excluded.version,
    inputs=excluded.inputs,
    outputs=excluded.outputs,
    is_subworkflow=excluded.is_subworkflow,
    nodes=excluded.nodes,
    edges=excluded.edges,
    updated_at=excluded.updated_at,
    revision=excluded.revision
WHERE workflow_definitions.revision = excluded.revision - 1""",
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
                if cursor.rowcount == 0:
                    # Zero rows affected means the ON CONFLICT WHERE clause
                    # did not match -- the existing row has a different
                    # revision than expected.
                    check = await self._db.execute(
                        "SELECT revision FROM workflow_definitions WHERE id = ?",
                        (definition.id,),
                    )
                    existing = await check.fetchone()
                    await self._db.rollback()
                    current = existing["revision"] if existing else "N/A"
                    msg = (
                        f"Version conflict saving workflow definition"
                        f" {definition.id!r}: current revision is"
                        f" {current}, incoming revision is"
                        f" {definition.revision}"
                    )
                    logger.warning(
                        PERSISTENCE_WORKFLOW_DEF_SAVE_FAILED,
                        definition_id=definition.id,
                        error=msg,
                    )
                    raise PersistenceVersionConflictError(msg)
                await self._db.commit()
            except sqlite3.Error as exc:
                await _rollback_quietly(self._db)
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
            cursor = await self._db.execute(
                f"SELECT {_SELECT_COLUMNS} FROM workflow_definitions WHERE id = ?",  # noqa: S608
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
            conditions.append("workflow_type = ?")
            params.append(filter_spec.workflow_type.value)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        sql = (
            f"SELECT {_SELECT_COLUMNS} FROM workflow_definitions"  # noqa: S608
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
            _deserialize_row(row, str(dict(row).get("id", "?"))) for row in rows
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
            f"SELECT {_SELECT_COLUMNS} FROM workflow_definitions "  # noqa: S608
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
            _deserialize_row(row, str(dict(row).get("id", "?"))) for row in rows
        )

    async def count(self, filter_spec: WorkflowDefinitionFilterSpec) -> int:
        """Count workflow definitions matching the filter spec.

        Returns:
            Number of matching rows.

        Raises:
            QueryError: If the database query fails.
        """
        conditions: list[str] = []
        params: list[object] = []
        if filter_spec.workflow_type is not None:
            conditions.append("workflow_type = ?")
            params.append(filter_spec.workflow_type.value)
        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
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
