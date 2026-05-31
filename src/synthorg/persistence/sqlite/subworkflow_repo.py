# module-kind: complex_service
"""SQLite repository implementation for subworkflows.

Subworkflows are first-class versioned workflow definitions living
in their own table keyed by ``(subworkflow_id, semver)``.  See
``src/synthorg/persistence/subworkflow_repo.py`` for the protocol.
"""

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, cast

from packaging.version import InvalidVersion, Version
from pydantic import ValidationError

if TYPE_CHECKING:
    import aiosqlite

from synthorg.core.enums import WorkflowNodeType, WorkflowType
from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.core.types import NotBlankStr
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
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.persistence import (
    PERSISTENCE_SUBWORKFLOW_DELETE_FAILED,
    PERSISTENCE_SUBWORKFLOW_DESERIALIZE_FAILED,
    PERSISTENCE_SUBWORKFLOW_FETCH_FAILED,
    PERSISTENCE_SUBWORKFLOW_FETCHED,
    PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
    PERSISTENCE_SUBWORKFLOW_LISTED,
    PERSISTENCE_SUBWORKFLOW_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)


_SUBWORKFLOW_SELECT = """\
subworkflow_id, semver, name, description, workflow_type, inputs, outputs,
nodes, edges, created_by, created_at, updated_at"""


def _semver_sort_key(version: str) -> Version:
    """Parse a semver string to a :class:`packaging.version.Version` key.

    Returns:
        Result of type ``Version``.
    """
    try:
        return Version(version)
    except InvalidVersion:
        # Unparseable strings sort last by using the lowest version.
        return Version("0.0.0")


def _parse_created_at(value: object) -> datetime:
    """Parse an ISO timestamp, forcing UTC.

    Returns:
        Result of type ``datetime``.
    """
    dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _deserialize_row(
    row: aiosqlite.Row,
    context_id: str,
) -> WorkflowDefinition:
    """Reconstruct a ``WorkflowDefinition`` from a subworkflows row.

    Returns:
        Result of type ``WorkflowDefinition``.

    Raises:
        QueryError: If the database query fails.
    """
    try:
        data = dict(row)
        nodes = tuple(WorkflowNode.model_validate(n) for n in json.loads(data["nodes"]))
        edges = tuple(WorkflowEdge.model_validate(e) for e in json.loads(data["edges"]))
        inputs = tuple(
            WorkflowIODeclaration.model_validate(i) for i in json.loads(data["inputs"])
        )
        outputs = tuple(
            WorkflowIODeclaration.model_validate(o) for o in json.loads(data["outputs"])
        )
        created_at = _parse_created_at(data["created_at"])
        updated_at = _parse_created_at(data["updated_at"])
        return WorkflowDefinition(
            id=str(data["subworkflow_id"]),
            name=str(data["name"]),
            description=str(data["description"]),
            workflow_type=WorkflowType(data["workflow_type"]),
            version=str(data["semver"]),
            inputs=inputs,
            outputs=outputs,
            is_subworkflow=True,
            nodes=nodes,
            edges=edges,
            created_by=str(data["created_by"]),
            created_at=created_at,
            updated_at=updated_at,
            revision=1,
        )
    except (ValueError, ValidationError, json.JSONDecodeError, KeyError) as exc:
        msg = f"Failed to deserialize subworkflow {context_id!r}"
        logger.warning(
            PERSISTENCE_SUBWORKFLOW_DESERIALIZE_FAILED,
            subworkflow_id=context_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


def _extract_references(  # noqa: PLR0913
    rows: Iterable[aiosqlite.Row],
    subworkflow_id: str,
    version: str | None,
    *,
    parent_type: Literal["workflow_definition", "subworkflow"],
    id_column: str,
    version_column: str | None = None,
    references: list[ParentReference],
) -> None:
    """Scan rows for SUBWORKFLOW nodes referencing the given coordinate.

    Mutates *references* in place, appending one ``ParentReference``
    per matching node found.

    Raises:
        QueryError: If the database query fails.
    """
    for row in rows:
        parent_id = str(row[id_column])
        parent_name = str(row["name"])
        parent_ver = str(row[version_column]) if version_column else None
        try:
            nodes = json.loads(row["nodes"])
        except json.JSONDecodeError:
            msg = f"Corrupted nodes JSON in {parent_type} {parent_id!r}"
            logger.warning(
                PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
                parent_id=parent_id,
                error=msg,
            )
            raise QueryError(msg) from None
        if not isinstance(nodes, list):
            msg = f"nodes field is not a list in {parent_type} {parent_id!r}"
            logger.warning(
                PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
                parent_id=parent_id,
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


class SQLiteSubworkflowRepository:
    """SQLite-backed subworkflow repository.

    Stores versioned subworkflows keyed by ``(subworkflow_id, semver)``.
    Unlike the main workflow definition repo, there is no optimistic
    concurrency -- every ``save`` is an INSERT, and duplicate
    coordinates are rejected.

    Args:
        db: An open aiosqlite connection.
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

    async def save(self, definition: WorkflowDefinition) -> None:
        """Insert a new subworkflow version row.

        Args:
            definition: The workflow definition to publish.  Its ``id``
                becomes the ``subworkflow_id`` and its ``version`` the
                semver coordinate.

        Raises:
            DuplicateRecordError: If ``(id, version)`` already exists.
            QueryError: On any other database failure.
        """
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
                await self._db.execute(
                    """\
INSERT INTO subworkflows
    (subworkflow_id, semver, name, description, workflow_type,
     inputs, outputs, nodes, edges, created_by, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        definition.id,
                        definition.version,
                        definition.name,
                        definition.description,
                        definition.workflow_type.value,
                        inputs_json,
                        outputs_json,
                        nodes_json,
                        edges_json,
                        definition.created_by,
                        definition.created_at.astimezone(UTC).isoformat(),
                        definition.updated_at.astimezone(UTC).isoformat(),
                    ),
                )
                await self._db.commit()
            except sqlite3.IntegrityError as exc:
                await self._db.rollback()
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
            except sqlite3.Error as exc:
                await self._db.rollback()
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
            cursor = await self._db.execute(
                f"SELECT {_SUBWORKFLOW_SELECT} FROM subworkflows "  # noqa: S608
                "WHERE subworkflow_id = ? AND semver = ?",
                (subworkflow_id, version),
            )
            row = await cursor.fetchone()
        except sqlite3.Error as exc:
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
            logger.debug(
                PERSISTENCE_SUBWORKFLOW_FETCHED,
                subworkflow_id=subworkflow_id,
                version=version,
                found=False,
            )
            return None

        definition = _deserialize_row(row, subworkflow_id)
        logger.debug(
            PERSISTENCE_SUBWORKFLOW_FETCHED,
            subworkflow_id=subworkflow_id,
            version=version,
            found=True,
        )
        return definition

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[WorkflowDefinition, ...]:
        """List subworkflows by composite key in ascending order (paginated).

        Ordering is ``(subworkflow_id, semver)`` as a SQL string
        comparison -- lexicographic, not semantic-version order (so
        ``1.10.0`` sorts before ``1.2.0``). This is deliberate: the
        contract here is a stable, deterministic pagination window, not
        a semver ranking. Callers that need semantic ordering use
        :meth:`list_versions`, which sorts versions client-side.

        Raises:
            QueryError: If the database query fails.

        Returns:
            The matching entities.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_SUBWORKFLOW_LIST_FAILED
        )
        try:
            cursor = await self._db.execute(
                f"SELECT {_SUBWORKFLOW_SELECT} FROM subworkflows "  # noqa: S608
                "ORDER BY subworkflow_id, semver LIMIT ? OFFSET ?",
                (limit, offset),
            )
            rows = await cursor.fetchall()
        except sqlite3.Error as exc:
            msg = "Failed to list subworkflows"
            logger.warning(
                PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(_deserialize_row(row, str(row["subworkflow_id"])) for row in rows)

    async def list_versions(
        self,
        subworkflow_id: NotBlankStr,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[str, ...]:
        """List semver strings for a subworkflow, newest first.

        Bounded by *limit* (default :data:`DEFAULT_PAGE_SIZE`). The
        SQL fetches every matching row; client-side semver sorting
        then orders them descending and the page size is applied
        last so the "newest first" contract is honoured against
        semver order rather than ``created_at`` order.

        Raises:
            QueryError: If the database query or pagination validation
                fails.

        Returns:
            The matching entities.
        """
        limit = validate_pagination_args(
            limit, 0, event=PERSISTENCE_SUBWORKFLOW_LIST_FAILED
        )
        try:
            cursor = await self._db.execute(
                "SELECT semver FROM subworkflows WHERE subworkflow_id = ?",
                (subworkflow_id,),
            )
            rows = await cursor.fetchall()
        except sqlite3.Error as exc:
            msg = f"Failed to list versions for subworkflow {subworkflow_id!r}"
            logger.warning(
                PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
                subworkflow_id=subworkflow_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        versions = [str(row["semver"]) for row in rows]
        versions.sort(key=_semver_sort_key, reverse=True)
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

        Raises:
            QueryError: If the database query or pagination validation
                fails.

        Returns:
            The matching entities.
        """
        limit = validate_pagination_args(
            limit, 0, event=PERSISTENCE_SUBWORKFLOW_LIST_FAILED
        )
        try:
            cursor = await self._db.execute(
                f"SELECT {_SUBWORKFLOW_SELECT} FROM subworkflows "  # noqa: S608
                "WHERE subworkflow_id IN ("
                "SELECT DISTINCT subworkflow_id FROM subworkflows "
                "ORDER BY subworkflow_id LIMIT ?"
                ") ORDER BY subworkflow_id, created_at DESC",
                (limit,),
            )
            rows = await cursor.fetchall()
        except sqlite3.Error as exc:
            msg = "Failed to list subworkflows"
            logger.warning(
                PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        summaries = self._build_summaries_from_rows(rows)
        logger.debug(
            PERSISTENCE_SUBWORKFLOW_LISTED,
            count=len(summaries),
        )
        return summaries

    async def search(
        self,
        query: NotBlankStr,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[SubworkflowSummary, ...]:
        """Return a bounded page of summaries matching a substring.

        Summaries are ``(subworkflow_id, latest_version)``-ordered so
        a cursor walk is stable; callers that need every match drain
        via :func:`synthorg.persistence._shared.collect_all`.

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
        # A summary aggregates every version row of a subworkflow into
        # one entry, so the page boundary is the distinct
        # ``subworkflow_id`` set, not raw rows. Page the ids at the DB
        # first, then fetch only that page's rows: this bounds both scan
        # cost and the rows materialised in memory to roughly
        # ``limit * versions_per_subworkflow``.
        try:
            id_cursor = await self._db.execute(
                "SELECT subworkflow_id FROM subworkflows "
                "WHERE name LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "OR description LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "GROUP BY subworkflow_id "
                "ORDER BY subworkflow_id LIMIT ? OFFSET ?",
                (pattern, pattern, limit, offset),
            )
            page_ids = [
                str(row["subworkflow_id"]) for row in await id_cursor.fetchall()
            ]
        except sqlite3.Error as exc:
            msg = f"Failed to search subworkflows with query {query!r}"
            logger.warning(
                PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
                query=query,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        if not page_ids:
            return ()
        placeholders = ", ".join("?" for _ in page_ids)
        try:
            full_cursor = await self._db.execute(
                f"SELECT {_SUBWORKFLOW_SELECT} FROM subworkflows "  # noqa: S608
                f"WHERE subworkflow_id IN ({placeholders})",
                tuple(page_ids),
            )
            full_rows = await full_cursor.fetchall()
        except sqlite3.Error as exc:
            msg = "Failed to load full versions for search results"
            logger.warning(
                PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
                query=query,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        return self._build_summaries_from_rows(full_rows)

    async def delete(
        self,
        entity_id: tuple[NotBlankStr, NotBlankStr],
    ) -> bool:
        """Delete a subworkflow version by composite key (``True`` on success).

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        subworkflow_id, version = entity_id
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM subworkflows WHERE subworkflow_id = ? AND semver = ?",
                    (subworkflow_id, version),
                )
                await self._db.commit()
            except sqlite3.Error as exc:
                await self._db.rollback()
                msg = f"Failed to delete subworkflow {subworkflow_id!r}@{version!r}"
                logger.warning(
                    PERSISTENCE_SUBWORKFLOW_DELETE_FAILED,
                    subworkflow_id=subworkflow_id,
                    version=version,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

        return cursor.rowcount > 0

    async def delete_if_unreferenced(
        self,
        subworkflow_id: NotBlankStr,
        version: NotBlankStr,
    ) -> tuple[bool, tuple[ParentReference, ...]]:
        """Atomically check-and-delete inside a single transaction.

        Returns:
            ``(deleted, parents)``: True if removed, plus the referencing parents tuple.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                # find_parents already uses self._db so we wrap the
                # whole check + delete in an explicit transaction.
                await self._db.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as exc:
                msg = (
                    "Failed to begin transaction for"
                    f" delete_if_unreferenced {subworkflow_id!r}@{version!r}"
                )
                logger.warning(
                    PERSISTENCE_SUBWORKFLOW_DELETE_FAILED,
                    subworkflow_id=subworkflow_id,
                    version=version,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

            try:
                parents = await self._find_parents_unpaged(subworkflow_id, version)
                if parents:
                    await self._db.rollback()
                    return False, parents

                cursor = await self._db.execute(
                    "DELETE FROM subworkflows WHERE subworkflow_id = ? AND semver = ?",
                    (subworkflow_id, version),
                )
                await self._db.commit()
            except Exception as exc:
                logger.warning(
                    PERSISTENCE_SUBWORKFLOW_DELETE_FAILED,
                    subworkflow_id=subworkflow_id,
                    version=version,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                try:
                    await self._db.rollback()
                except sqlite3.Error as rollback_exc:
                    log_exception_redacted(
                        logger,
                        PERSISTENCE_SUBWORKFLOW_DELETE_FAILED,
                        rollback_exc,
                        subworkflow_id=subworkflow_id,
                        version=version,
                        primary_error_type=type(exc).__name__,
                        primary_error=safe_error_description(exc),
                        note="Rollback failed after primary error",
                    )
                raise

        deleted = cursor.rowcount > 0
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

        Scans both ``workflow_definitions.nodes`` and
        ``subworkflows.nodes`` so that nested subworkflow references
        (a subworkflow pinning another subworkflow) are discovered.
        References page in
        ``(parent_type, parent_id, node_id, pinned_version)`` order so
        a cursor walk is stable. The referential-integrity path
        (:meth:`delete_if_unreferenced`) bypasses pagination via
        :meth:`_find_parents_unpaged`; a truncated parent set would let
        a still-referenced version be deleted.

        Returns:
            Tuple of matching rows; empty when no rows match.
        """
        limit = validate_pagination_args(
            limit,
            offset,
            event=PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
            subworkflow_id=subworkflow_id,
        )
        references = await self._find_parents_unpaged(subworkflow_id, version)
        return tuple(references[offset : offset + limit])

    async def _find_parents_unpaged(
        self,
        subworkflow_id: NotBlankStr,
        version: NotBlankStr | None = None,
    ) -> tuple[ParentReference, ...]:
        """Return every reference to a subworkflow, sorted, unpaged.

        Backs both :meth:`find_parents` (which slices a page off this
        result) and :meth:`delete_if_unreferenced` (which must see the
        complete set so a still-referenced version is never deleted).

        Returns:
            The matching collection.
        """
        references: list[ParentReference] = []

        # Scan workflow_definitions table.
        wf_rows = await self._fetch_parent_rows(
            "SELECT id, name, nodes FROM workflow_definitions",
            subworkflow_id,
        )
        _extract_references(
            wf_rows,
            subworkflow_id,
            version,
            parent_type="workflow_definition",
            id_column="id",
            references=references,
        )

        # Scan subworkflows table for nested references.
        sub_rows = await self._fetch_parent_rows(
            "SELECT subworkflow_id, name, semver, nodes FROM subworkflows",
            subworkflow_id,
        )
        _extract_references(
            sub_rows,
            subworkflow_id,
            version,
            parent_type="subworkflow",
            id_column="subworkflow_id",
            version_column="semver",
            references=references,
        )

        # The reference scan walks JSON node arrays in both
        # ``workflow_definitions`` and ``subworkflows``; true SQL-level
        # pagination needs a normalized references table (a schema
        # change tracked separately). Sorting the full set in memory is
        # acceptable because the referential-integrity caller needs
        # every reference anyway, so per-page DB bounding saves nothing.
        references.sort(
            key=lambda r: (
                r.parent_type,
                r.parent_id,
                r.node_id,
                r.pinned_version,
            ),
        )
        return tuple(references)

    async def _fetch_parent_rows(
        self,
        query: str,
        subworkflow_id: str,
    ) -> Iterable[aiosqlite.Row]:
        """Execute a SELECT and return all rows, with error handling.

        Returns:
            Result of type ``Iterable[aiosqlite.Row]``.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            cursor = await self._db.execute(query)
            return await cursor.fetchall()
        except sqlite3.Error as exc:
            msg = f"Failed to find parents for subworkflow {subworkflow_id!r}"
            logger.warning(
                PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
                subworkflow_id=subworkflow_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    def _build_summaries_from_rows(
        self,
        rows: Iterable[aiosqlite.Row],
    ) -> tuple[SubworkflowSummary, ...]:
        """Group rows by subworkflow and emit a summary for the latest one.

        Returns:
            The matching collection.

        Raises:
            QueryError: If the database query fails.
        """
        grouped: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            data = dict(row)
            grouped.setdefault(str(data["subworkflow_id"]), []).append(data)

        summaries: list[SubworkflowSummary] = []
        for sub_id, versions in grouped.items():
            versions.sort(
                key=lambda d: _semver_sort_key(str(d["semver"])),
                reverse=True,
            )
            latest = versions[0]
            try:
                inputs = json.loads(cast("str", latest["inputs"]))
                outputs = json.loads(cast("str", latest["outputs"]))
            except json.JSONDecodeError as exc:
                msg = f"Corrupted I/O JSON in subworkflow {sub_id!r}"
                logger.warning(
                    PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
                    subworkflow_id=sub_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            if not isinstance(inputs, list) or not isinstance(outputs, list):
                msg = f"I/O fields are not lists in subworkflow {sub_id!r}"
                logger.warning(
                    PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
                    subworkflow_id=sub_id,
                    error=msg,
                )
                raise QueryError(msg)
            summaries.append(
                SubworkflowSummary(
                    subworkflow_id=sub_id,
                    latest_version=str(latest["semver"]),
                    name=str(latest["name"]),
                    description=str(latest["description"]),
                    input_count=len(inputs),
                    output_count=len(outputs),
                    version_count=len(versions),
                ),
            )
        summaries.sort(key=lambda s: s.subworkflow_id)
        return tuple(summaries)
