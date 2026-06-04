"""Marshalling + reference-scanning helpers for Postgres subworkflows."""

from collections.abc import Iterable
from typing import Literal

from packaging.version import InvalidVersion, Version
from psycopg.rows import DictRow
from pydantic import ValidationError

from synthorg.core.enums import WorkflowNodeType, WorkflowType
from synthorg.core.persistence_errors import QueryError
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
    PERSISTENCE_SUBWORKFLOW_DESERIALIZE_FAILED,
    PERSISTENCE_SUBWORKFLOW_LIST_FAILED,
    PERSISTENCE_SUBWORKFLOW_LISTED,
)

logger = get_logger(__name__)

SUBWORKFLOW_COLUMNS = """\
subworkflow_id, semver, name, description, workflow_type,
inputs, outputs, nodes, edges, created_by, created_at, updated_at"""


def semver_sort_key(version: str) -> Version:
    """Parse a semver string to a :class:`packaging.version.Version` key.

    Returns:
        Result of type ``Version`` (``0.0.0`` for unparseable input).
    """
    try:
        return Version(version)
    except InvalidVersion:
        return Version("0.0.0")


def deserialize_row(row: DictRow, context_id: str) -> WorkflowDefinition:
    """Reconstruct a ``WorkflowDefinition`` from a Postgres dict_row.

    Postgres returns JSONB as native Python objects (no json.loads
    needed) and TIMESTAMPTZ as timezone-aware datetime.

    Returns:
        Result of type ``WorkflowDefinition``.

    Raises:
        QueryError: If row parsing or validation fails.
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


def extract_references(  # noqa: PLR0913
    rows: Iterable[DictRow],
    subworkflow_id: str,
    version: str | None,
    *,
    parent_type: Literal["workflow_definition", "subworkflow"],
    id_column: str,
    version_column: str | None = None,
    references: list[ParentReference],
) -> None:
    """Scan rows for SUBWORKFLOW nodes matching the coordinate.

    Appends matching :class:`ParentReference` entries to *references*.

    Raises:
        QueryError: If a row's ``nodes`` / node config is malformed.
    """
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


def build_summaries_from_rows(
    rows: Iterable[DictRow],
) -> tuple[SubworkflowSummary, ...]:
    """Group rows by subworkflow_id and build latest-version summaries.

    Returns:
        The matching collection.
    """
    grouped: dict[str, list[DictRow]] = {}
    for row in rows:
        sid = str(row["subworkflow_id"])
        grouped.setdefault(sid, []).append(row)

    summaries: list[SubworkflowSummary] = []
    for sub_id, items in grouped.items():
        items.sort(key=lambda r: semver_sort_key(str(r["semver"])), reverse=True)
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
    logger.debug(PERSISTENCE_SUBWORKFLOW_LISTED, count=len(summaries))
    return tuple(summaries)


__all__ = [
    "SUBWORKFLOW_COLUMNS",
    "build_summaries_from_rows",
    "deserialize_row",
    "extract_references",
    "semver_sort_key",
]
