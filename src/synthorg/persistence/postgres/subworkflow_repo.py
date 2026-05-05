"""Postgres repository implementation for subworkflows.

Postgres-native port of ``synthorg.persistence.sqlite.subworkflow_repo``.
Uses JSONB for node/edge/IO columns and TIMESTAMPTZ for timestamps.
"""

import hashlib
from collections.abc import Iterable  # noqa: TC003
from typing import TYPE_CHECKING, Any, Literal

import psycopg
from packaging.version import InvalidVersion, Version
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from synthorg.core.enums import WorkflowNodeType, WorkflowType
from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIODeclaration,
    WorkflowNode,
)
from synthorg.engine.workflow.subworkflow_models import (
    ParentReference,
    SubworkflowSummary,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_SUBWORKFLOW_DELETE_FAILED,
    PERSISTENCE_SUBWORKFLOW_DESERIALIZE_FAILED,
    PERSISTENCE_SUBWORKFLOW_FETCH_FAILED,
    PERSISTENCE_SUBWORKFLOW_FETCHED,
    PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
    PERSISTENCE_SUBWORKFLOW_LISTED,
    PERSISTENCE_SUBWORKFLOW_SAVE_FAILED,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)


_SELECT_COLUMNS = """\
subworkflow_id, semver, name, description, workflow_type,
inputs, outputs, nodes, edges, created_by, created_at, updated_at"""


def _semver_sort_key(version: str) -> Version:
    """Parse a semver string to a :class:`packaging.version.Version` key."""
    try:
        return Version(version)
    except InvalidVersion:
        return Version("0.0.0")


def _deserialize_row(
    row: dict[str, Any],
    context_id: str,
) -> WorkflowDefinition:
    """Reconstruct a ``WorkflowDefinition`` from a Postgres dict_row.

    Postgres returns JSONB as native Python objects (no json.loads
    needed) and TIMESTAMPTZ as timezone-aware datetime.
    """
    try:
        nodes = tuple(WorkflowNode.model_validate(n) for n in (row.get("nodes") or []))
        edges = tuple(WorkflowEdge.model_validate(e) for e in (row.get("edges") or []))
        inputs = tuple(
            WorkflowIODeclaration.model_validate(i) for i in (row.get("inputs") or [])
        )
        outputs = tuple(
            WorkflowIODeclaration.model_validate(o) for o in (row.get("outputs") or [])
        )
        return WorkflowDefinition(
            id=str(row["subworkflow_id"]),
            name=str(row["name"]),
            description=str(row["description"]),
            workflow_type=WorkflowType(row["workflow_type"]),
            version=str(row["semver"]),
            inputs=inputs,
            outputs=outputs,
            is_subworkflow=True,
            nodes=nodes,
            edges=edges,
            created_by=str(row["created_by"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            revision=1,
        )
    except (ValueError, ValidationError, KeyError, TypeError) as exc:
        msg = f"Failed to deserialize subworkflow {context_id!r}"
        logger.warning(
            PERSISTENCE_SUBWORKFLOW_DESERIALIZE_FAILED,
            subworkflow_id=context_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


def _extract_references(  # noqa: PLR0913, C901
    rows: Iterable[dict[str, Any]],
    subworkflow_id: str,
    version: str | None,
    *,
    parent_type: Literal["workflow_definition", "subworkflow"],
    id_column: str,
    version_column: str | None = None,
    references: list[ParentReference],
) -> None:
    """Scan rows for SUBWORKFLOW nodes matching the coordinate."""
    for row in rows:
        parent_id = str(row[id_column])
        parent_name = str(row["name"])
        parent_ver = str(row[version_column]) if version_column else None
        nodes = row.get("nodes") or []
        if not isinstance(nodes, list):
            msg = f"nodes field is not a list in {parent_type} {parent_id!r}"
            logger.warning(
                PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
                parent_id=parent_id,
                parent_type=parent_type,
                error=msg,
            )
            raise QueryError(msg)
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("type") != WorkflowNodeType.SUBWORKFLOW.value:
                continue
            config = node.get("config")
            if not isinstance(config, dict):
                msg = f"Malformed SUBWORKFLOW config in {parent_type} {parent_id!r}"
                logger.warning(
                    PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
                    parent_id=parent_id,
                    parent_type=parent_type,
                    error=msg,
                )
                raise QueryError(msg)
            if config.get("subworkflow_id") != subworkflow_id:
                continue
            pinned = str(config.get("version") or "")
            if not pinned:
                # Intentionally unpinned subworkflow ref -- skip.
                continue
            if version is not None and pinned != version:
                continue
            node_id = node.get("id")
            if not isinstance(node_id, str):
                msg = (
                    f"Malformed SUBWORKFLOW node in"
                    f" {parent_type} {parent_id!r}: missing id"
                )
                logger.warning(
                    PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
                    parent_id=parent_id,
                    parent_type=parent_type,
                    error=msg,
                )
                raise QueryError(msg)
            references.append(
                ParentReference(
                    parent_id=parent_id,
                    parent_name=parent_name,
                    pinned_version=pinned,
                    node_id=node_id,
                    parent_type=parent_type,
                    parent_version=parent_ver,
                ),
            )


class PostgresSubworkflowRepository:
    """Postgres-backed subworkflow repository.

    Stores versioned subworkflows keyed by ``(subworkflow_id, semver)``.
    INSERT-only semantics -- duplicate coordinates are rejected.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, definition: WorkflowDefinition) -> None:
        """Insert a new subworkflow version row."""
        nodes = [n.model_dump(mode="json") for n in definition.nodes]
        edges = [e.model_dump(mode="json") for e in definition.edges]
        inputs = [i.model_dump(mode="json") for i in definition.inputs]
        outputs = [o.model_dump(mode="json") for o in definition.outputs]
        try:
            async with self._pool.connection() as conn:
                await conn.execute(
                    """\
INSERT INTO subworkflows
    (subworkflow_id, semver, name, description, workflow_type,
     inputs, outputs, nodes, edges, created_by, created_at, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        definition.id,
                        definition.version,
                        definition.name,
                        definition.description,
                        definition.workflow_type.value,
                        Jsonb(inputs),
                        Jsonb(outputs),
                        Jsonb(nodes),
                        Jsonb(edges),
                        definition.created_by,
                        definition.created_at,
                        definition.updated_at,
                    ),
                )
        except psycopg.errors.UniqueViolation as exc:
            msg = (
                f"Subworkflow {definition.id!r} version "
                f"{definition.version!r} already exists"
            )
            logger.warning(
                PERSISTENCE_SUBWORKFLOW_SAVE_FAILED,
                subworkflow_id=definition.id,
                version=definition.version,
                error=msg,
            )
            raise DuplicateRecordError(msg) from exc
        except psycopg.Error as exc:
            msg = (
                f"Failed to save subworkflow {definition.id!r} version "
                f"{definition.version!r}"
            )
            logger.warning(
                PERSISTENCE_SUBWORKFLOW_SAVE_FAILED,
                subworkflow_id=definition.id,
                version=definition.version,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(
        self,
        subworkflow_id: NotBlankStr,
        version: NotBlankStr,
    ) -> WorkflowDefinition | None:
        """Fetch a specific subworkflow version."""
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM subworkflows"  # noqa: S608
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
        return _deserialize_row(row, f"{subworkflow_id}@{version}")

    async def list_versions(
        self,
        subworkflow_id: NotBlankStr,
    ) -> tuple[NotBlankStr, ...]:
        """List all semver strings for a subworkflow, newest first."""
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

        versions = [str(r["semver"]) for r in rows]
        versions.sort(key=_semver_sort_key, reverse=True)
        return tuple(versions)

    async def list_summaries(self) -> tuple[SubworkflowSummary, ...]:
        """Return a summary for every unique subworkflow."""
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM subworkflows",  # noqa: S608
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

        return self._build_summaries_from_rows(rows)

    async def search(
        self,
        query: NotBlankStr,
    ) -> tuple[SubworkflowSummary, ...]:
        """Search subworkflows by name or description substring."""
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM subworkflows"  # noqa: S608
                    " WHERE name ILIKE %s OR description ILIKE %s",
                    (pattern, pattern),
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

        return self._build_summaries_from_rows(rows)

    async def delete(
        self,
        subworkflow_id: NotBlankStr,
        version: NotBlankStr,
    ) -> bool:
        """Delete a subworkflow version, returning True on success."""
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

    async def delete_if_unreferenced(
        self,
        subworkflow_id: NotBlankStr,
        version: NotBlankStr,
    ) -> tuple[bool, tuple[ParentReference, ...]]:
        """Atomically check-and-delete inside a single transaction.

        Uses a Postgres advisory lock keyed on the subworkflow
        coordinate to serialize with concurrent writers and prevent
        TOCTOU races under READ COMMITTED isolation.
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
    ) -> tuple[ParentReference, ...]:
        """Return workflows referencing a subworkflow.

        Scans both ``workflow_definitions`` and ``subworkflows`` tables.
        """
        try:
            async with self._pool.connection() as conn:
                return await self._find_parents_with_conn(
                    conn,
                    subworkflow_id,
                    version,
                )
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
        conn: psycopg.AsyncConnection[Any],
        subworkflow_id: str,
        version: str | None,
    ) -> tuple[ParentReference, ...]:
        """Shared find_parents logic usable within an existing connection."""
        references: list[ParentReference] = []

        # Scan workflow_definitions table.
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, name, nodes FROM workflow_definitions",
            )
            wf_rows = await cur.fetchall()
        _extract_references(
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
        _extract_references(
            sub_rows,
            subworkflow_id,
            version,
            parent_type="subworkflow",
            id_column="subworkflow_id",
            version_column="semver",
            references=references,
        )

        return tuple(references)

    def _build_summaries_from_rows(
        self,
        rows: Iterable[dict[str, Any]],
    ) -> tuple[SubworkflowSummary, ...]:
        """Group rows by subworkflow_id and build summaries."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            sid = str(row["subworkflow_id"])
            grouped.setdefault(sid, []).append(row)

        summaries: list[SubworkflowSummary] = []
        for sub_id, items in grouped.items():
            items.sort(
                key=lambda r: _semver_sort_key(str(r["semver"])),
                reverse=True,
            )
            latest = items[0]
            inputs = latest.get("inputs") or []
            outputs = latest.get("outputs") or []
            summaries.append(
                SubworkflowSummary(
                    subworkflow_id=sub_id,
                    latest_version=str(latest["semver"]),
                    name=str(latest["name"]),
                    description=str(latest.get("description") or ""),
                    input_count=len(inputs),
                    output_count=len(outputs),
                    version_count=len(items),
                ),
            )
        summaries.sort(key=lambda s: s.subworkflow_id)
        logger.debug(
            PERSISTENCE_SUBWORKFLOW_LISTED,
            count=len(summaries),
        )
        return tuple(summaries)
