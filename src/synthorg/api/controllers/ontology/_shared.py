"""Shared service factory, entity serializer, and page-size for ontology.

Holds the per-request :class:`OntologyService` accessor and the
``EntityDefinition -> EntityResponse`` mapping used by the entity-CRUD
and versioning controllers, plus the default page size shared by the
list endpoints.
"""

from typing import Final

from synthorg._core.features import require_service
from synthorg.api.dto_ontology import (
    EntityFieldResponse,
    EntityRelationResponse,
    EntityResponse,
)
from synthorg.api.state import AppState
from synthorg.ontology.models import EntityDefinition
from synthorg.ontology.service import OntologyService
from synthorg.ontology.state import OntologyStateSlice

_DEFAULT_LIMIT: Final[int] = 50


def _ontology_service(app_state: AppState) -> OntologyService:
    """Return the ontology service from its slice or raise 503 when unwired.

    Returns:
        The wired ``OntologyService``.
    """
    return require_service(
        app_state.slice(OntologyStateSlice).service, "Ontology Service"
    )


def _entity_to_response(entity: EntityDefinition) -> EntityResponse:
    """Convert an EntityDefinition to an EntityResponse.

    Returns:
        ``EntityResponse`` instance.
    """
    return EntityResponse(
        name=entity.name,
        tier=entity.tier,
        source=entity.source,
        definition=entity.definition,
        fields=tuple(
            EntityFieldResponse(
                name=f.name,
                type_hint=f.type_hint,
                description=f.description,
            )
            for f in entity.fields
        ),
        constraints=entity.constraints,
        disambiguation=entity.disambiguation,
        relationships=tuple(
            EntityRelationResponse(
                target=r.target,
                relation=r.relation,
                description=r.description,
            )
            for r in entity.relationships
        ),
        created_by=entity.created_by,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )
