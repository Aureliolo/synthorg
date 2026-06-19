# ruff: noqa: EM101
# module-kind: service
"""Ontology facade over ``OntologyService``."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    INTEGRATIONS_CAPABILITY_UNSUPPORTED,
)

logger = get_logger(__name__)

if TYPE_CHECKING:
    # OntologyService is a concrete collaborator injected via a SimpleNamespace
    # fake in tests; a runtime import would make typeguard reject the fake.
    from synthorg.ontology.service import OntologyService


class OntologyFacadeService:
    """Facade over :class:`OntologyService`."""

    def __init__(self, *, ontology: OntologyService) -> None:
        self._ontology = cast("object", ontology)

    async def list_entities(self) -> Sequence[object]:
        """List all ontology entities.

        Returns:
            A tuple of all ontology entities.

        Raises:
            CapabilityNotSupportedError: If the backing ``OntologyService``
                does not expose ``list_entities``.
        """
        fn = getattr(self._ontology, "list_entities", None)
        if not callable(fn):
            logger.warning(
                INTEGRATIONS_CAPABILITY_UNSUPPORTED,
                capability="ontology_list_entities",
                error_type=CapabilityNotSupportedError.__name__,
            )
            raise CapabilityNotSupportedError(
                "ontology_list_entities",
                "OntologyService does not expose list_entities",
            )
        return tuple(await fn())

    async def get_entity(
        self,
        entity_id: NotBlankStr,
    ) -> object | None:
        """Fetch a single ontology entity by ID.

        Returns:
            The matching entity, or ``None`` when no entity has the given
            ID.

        Raises:
            CapabilityNotSupportedError: If the backing ``OntologyService``
                does not expose ``get_entity``.
        """
        fn = getattr(self._ontology, "get_entity", None)
        if not callable(fn):
            logger.warning(
                INTEGRATIONS_CAPABILITY_UNSUPPORTED,
                capability="ontology_get_entity",
                error_type=CapabilityNotSupportedError.__name__,
            )
            raise CapabilityNotSupportedError(
                "ontology_get_entity",
                "OntologyService does not expose get_entity",
            )
        return cast("object | None", await fn(entity_id))

    async def get_relationships(
        self,
        entity_id: NotBlankStr,
    ) -> Sequence[object]:
        """List an entity's relationships.

        Returns:
            A tuple of relationships for the given entity.

        Raises:
            CapabilityNotSupportedError: If the backing ``OntologyService``
                does not expose ``get_relationships``.
        """
        fn = getattr(self._ontology, "get_relationships", None)
        if not callable(fn):
            logger.warning(
                INTEGRATIONS_CAPABILITY_UNSUPPORTED,
                capability="ontology_get_relationships",
                error_type=CapabilityNotSupportedError.__name__,
            )
            raise CapabilityNotSupportedError(
                "ontology_get_relationships",
                "OntologyService does not expose get_relationships",
            )
        return tuple(await fn(entity_id))

    async def search(
        self,
        query: NotBlankStr,
    ) -> Sequence[object]:
        """Search ontology entities by query string.

        Returns:
            A tuple of entities matching the query.

        Raises:
            CapabilityNotSupportedError: If the backing ``OntologyService``
                does not expose ``search``.
        """
        fn = getattr(self._ontology, "search", None)
        if not callable(fn):
            logger.warning(
                INTEGRATIONS_CAPABILITY_UNSUPPORTED,
                capability="ontology_search",
                error_type=CapabilityNotSupportedError.__name__,
            )
            raise CapabilityNotSupportedError(
                "ontology_search",
                "OntologyService does not expose search",
            )
        return tuple(await fn(query))
