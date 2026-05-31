"""Postgres-backed ontology versioning factory.

Builds a :class:`VersioningService[EntityDefinition]` against a Postgres
``entity_definition_versions`` table. The deserializer helpers live in
:mod:`synthorg.ontology.versioning` because they are pure functions
over :class:`EntityDefinition`; the dependency arrow points
persistence -> ontology, never the other way around.
"""

from psycopg_pool import AsyncConnectionPool

from synthorg.ontology.models import EntityDefinition
from synthorg.ontology.versioning import _safe_deserialize_snapshot_dict
from synthorg.persistence.postgres.version_repo import PostgresVersionRepository
from synthorg.persistence.version_protocol import VersionRepository
from synthorg.versioning.service import VersioningService


def create_postgres_ontology_version_repo(
    pool: AsyncConnectionPool,
) -> VersionRepository[EntityDefinition]:
    """Create a Postgres-backed VersionRepository for EntityDefinition.

    Args:
        pool: An open ``psycopg_pool.AsyncConnectionPool`` produced by
            the persistence backend, forwarded straight through.

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
    pool: AsyncConnectionPool,
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
