"""Versioning integration for the ontology subsystem.

Provides factories that compose ``VersioningService[EntityDefinition]``
on top of the persistence layer's version repositories (SQLite or
Postgres).  After A5 consolidation the DB handle is sourced from the
shared :class:`PersistenceBackend` rather than a standalone ontology
backend; the caller picks the right factory for the active backend.
"""

import json
from typing import Any

from pydantic import ValidationError

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.ontology import (
    ONTOLOGY_VERSION_SNAPSHOT_DESERIALIZATION_FAILED,
)
from synthorg.ontology.errors import OntologyError
from synthorg.ontology.models import EntityDefinition
from synthorg.persistence.postgres.version_repo import PostgresVersionRepository
from synthorg.persistence.sqlite._shared import WriteContext  # noqa: TC001
from synthorg.persistence.sqlite.version_repo import SQLiteVersionRepository
from synthorg.persistence.version_protocol import (
    VersionRepository,  # noqa: TC001 -- documented return type
)
from synthorg.versioning.service import VersioningService

logger = get_logger(__name__)


def _safe_deserialize_snapshot_json(raw: str) -> EntityDefinition:
    """Deserialize a JSON text snapshot, wrapping validation errors."""
    try:
        return EntityDefinition.model_validate_json(raw)
    except ValidationError as exc:
        msg = "Corrupted entity definition version snapshot"
        logger.warning(
            ONTOLOGY_VERSION_SNAPSHOT_DESERIALIZATION_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise OntologyError(msg) from exc


def _safe_deserialize_snapshot_dict(data: object) -> EntityDefinition:
    """Deserialize a parsed JSONB snapshot, wrapping validation errors."""
    try:
        return EntityDefinition.model_validate(data)
    except ValidationError as exc:
        msg = "Corrupted entity definition version snapshot"
        logger.warning(
            ONTOLOGY_VERSION_SNAPSHOT_DESERIALIZATION_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise OntologyError(msg) from exc


def create_ontology_version_repo(
    db: Any,
    *,
    write_context: WriteContext,
) -> VersionRepository[EntityDefinition]:
    """Create a SQLite-backed VersionRepository for EntityDefinition.

    Args:
        db: An open aiosqlite connection produced by the persistence
            backend.  Accepted as ``Any`` because importing
            ``aiosqlite`` outside ``persistence/`` would violate the
            boundary linter; the actual handle is passed straight
            through to the repository.
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
    write_context: WriteContext,
) -> VersioningService[EntityDefinition]:
    """Create a SQLite-backed VersioningService for EntityDefinition.

    Args:
        db: An open aiosqlite connection (see above note on the type).
        write_context: The backend's ``write_context`` callable,
            forwarded to the underlying version repository.

    Returns:
        A versioning service for entity definitions.
    """
    repo = create_ontology_version_repo(db, write_context=write_context)
    return VersioningService(repo)


def create_postgres_ontology_version_repo(
    pool: Any,
) -> VersionRepository[EntityDefinition]:
    """Create a Postgres-backed VersionRepository for EntityDefinition.

    Args:
        pool: An open ``psycopg_pool.AsyncConnectionPool`` produced by
            the persistence backend.  Typed as ``Any`` so this module
            stays inside the persistence boundary linter's Python-level
            rules; the handle is forwarded straight through.

    Returns:
        A repository targeting the ``entity_definition_versions`` table.
    """
    return PostgresVersionRepository(
        pool=pool,
        table_name="entity_definition_versions",
        serialize_snapshot=lambda m: m.model_dump(mode="json"),
        deserialize_snapshot=_safe_deserialize_snapshot_dict,
    )


def create_postgres_ontology_versioning(
    pool: Any,
) -> VersioningService[EntityDefinition]:
    """Create a Postgres-backed VersioningService for EntityDefinition.

    Args:
        pool: An open psycopg async connection pool (see note above).

    Returns:
        A versioning service for entity definitions.
    """
    repo = create_postgres_ontology_version_repo(pool)
    return VersioningService(repo)
