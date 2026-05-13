"""SQLite-backed ontology versioning factory.

Builds a :class:`VersioningService[EntityDefinition]` against a SQLite
``entity_definition_versions`` table. The deserializer helpers live in
:mod:`synthorg.ontology.versioning` because they are pure functions
over :class:`EntityDefinition`; the dependency arrow points
persistence -> ontology, never the other way around.
"""

import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any

from synthorg.ontology.versioning import _safe_deserialize_snapshot_json
from synthorg.persistence.sqlite.version_repo import SQLiteVersionRepository
from synthorg.versioning.service import VersioningService

if TYPE_CHECKING:
    from synthorg.ontology.models import EntityDefinition
    from synthorg.persistence.version_protocol import VersionRepository

type WriteContextFn = Callable[[], AbstractAsyncContextManager[None]]


def create_ontology_version_repo(
    db: Any,
    *,
    write_context: WriteContextFn,
) -> VersionRepository[EntityDefinition]:
    """Create a SQLite-backed VersionRepository for EntityDefinition.

    Args:
        db: An open aiosqlite connection produced by the persistence
            backend. Accepted as ``Any`` because importing
            ``aiosqlite`` outside ``persistence/`` would violate the
            boundary linter; the handle is passed through to the
            repository.
        write_context: The backend's ``write_context`` callable,
            forwarded to the repository so writes serialize with
            sibling repos on the shared aiosqlite connection.

    Returns:
        A repository targeting the ``entity_definition_versions`` table.
    """
    return SQLiteVersionRepository(
        db,
        table_name="entity_definition_versions",
        serialize_snapshot=lambda m: json.dumps(
            m.model_dump(mode="json"),
        ),
        deserialize_snapshot=_safe_deserialize_snapshot_json,
        write_context=write_context,
    )


def create_ontology_versioning(
    db: Any,
    *,
    write_context: WriteContextFn,
) -> VersioningService[EntityDefinition]:
    """Create a SQLite-backed VersioningService for EntityDefinition.

    Args:
        db: An open aiosqlite connection (see :func:`create_ontology_version_repo`).
        write_context: The backend's ``write_context`` callable,
            forwarded to the underlying version repository.

    Returns:
        A versioning service for entity definitions.
    """
    repo = create_ontology_version_repo(db, write_context=write_context)
    return VersioningService(repo)
