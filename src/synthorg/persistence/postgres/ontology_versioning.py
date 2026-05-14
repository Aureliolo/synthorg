"""Postgres-backed ontology versioning factory.

Builds a :class:`VersioningService[EntityDefinition]` against a Postgres
``entity_definition_versions`` table. The deserializer helpers live in
:mod:`synthorg.ontology.versioning` because they are pure functions
over :class:`EntityDefinition`; the dependency arrow points
persistence -> ontology, never the other way around.
"""

from typing import TYPE_CHECKING, Any

from synthorg.ontology.versioning import _safe_deserialize_snapshot_dict
from synthorg.persistence.postgres.version_repo import PostgresVersionRepository
from synthorg.versioning.service import VersioningService

if TYPE_CHECKING:
    from synthorg.ontology.models import EntityDefinition
    from synthorg.persistence.version_protocol import VersionRepository


def create_postgres_ontology_version_repo(
    pool: Any,
) -> VersionRepository[EntityDefinition]:
    """Create a Postgres-backed VersionRepository for EntityDefinition.

    Args:
        pool: An open ``psycopg_pool.AsyncConnectionPool`` produced by
            the persistence backend. Typed as ``Any`` so this module
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
        pool: An open psycopg async connection pool (see
            :func:`create_postgres_ontology_version_repo`).

    Returns:
        A versioning service for entity definitions.
    """
    repo = create_postgres_ontology_version_repo(pool)
    return VersioningService(repo)
