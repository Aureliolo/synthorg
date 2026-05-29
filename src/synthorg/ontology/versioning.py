"""Ontology versioning deserializer helpers.

Pure functions over :class:`EntityDefinition`: parse a stored snapshot
back into a typed model, wrapping any pydantic ``ValidationError`` in
:class:`OntologyError`. The concrete persistence-bound
:class:`VersioningService` factories live next to each backend
(``persistence/sqlite/ontology_versioning.py``,
``persistence/postgres/ontology_versioning.py``); they import these
helpers, never the reverse.
"""

from pydantic import ValidationError

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.ontology import (
    ONTOLOGY_VERSION_SNAPSHOT_DESERIALIZATION_FAILED,
)
from synthorg.ontology.errors import OntologyError
from synthorg.ontology.models import EntityDefinition

logger = get_logger(__name__)


def _safe_deserialize_snapshot_json(raw: str) -> EntityDefinition:
    """Deserialize a JSON text snapshot, wrapping validation errors.

    Returns:
        The parsed ``EntityDefinition``.

    Raises:
        OntologyError: When the snapshot JSON fails model validation.
    """
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
    """Deserialize a parsed JSONB snapshot, wrapping validation errors.

    Returns:
        The parsed ``EntityDefinition``.

    Raises:
        OntologyError: When the snapshot data fails model validation.
    """
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
