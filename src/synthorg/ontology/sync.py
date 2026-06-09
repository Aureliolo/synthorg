"""Ontology-to-OrgMemory synchronisation service.

Publishes entity definitions as ``OrgFact`` entries with
``OrgFactCategory.ENTITY_DEFINITION``.  One-way sync (ontology
to OrgMemory), idempotent via SHA-256 content hashing.
"""

import hashlib

from synthorg.core.types import NotBlankStr
from synthorg.hr.seniority import SeniorityLevel
from synthorg.memory.enums import OrgFactCategory
from synthorg.memory.org.protocol import OrgMemoryBackend
from synthorg.observability import get_logger
from synthorg.observability.events.ontology import (
    ONTOLOGY_SYNC_PUBLISHED,
    ONTOLOGY_SYNC_SKIPPED,
)
from synthorg.ontology.config import OntologySyncConfig
from synthorg.ontology.injection.prompt import format_entity
from synthorg.ontology.models import EntityDefinition
from synthorg.persistence.ontology_protocol import OntologyEntityRepository

logger = get_logger(__name__)


def _content_hash(text: str) -> str:
    """SHA-256 hex digest of text content.

    Args:
        text: Text to hash.

    Returns:
        64-character lowercase hex digest.
    """
    return hashlib.sha256(text.encode()).hexdigest()


class OntologyOrgMemorySync:
    """Sync entity definitions to organizational memory.

    Publishes entity definitions as OrgFacts, using content hashing
    to skip unchanged definitions.  Authored at ``SeniorityLevel.SENIOR``
    so definition writes carry senior write authority.

    Args:
        ontology: Ontology backend for entity retrieval.
        org_memory: OrgMemory backend for fact publishing.
        config: Sync configuration.
    """

    __slots__ = ("_config", "_hashes", "_ontology", "_org_memory")

    def __init__(
        self,
        *,
        ontology: OntologyEntityRepository,
        org_memory: OrgMemoryBackend,
        config: OntologySyncConfig,
    ) -> None:
        self._ontology = ontology
        self._org_memory = org_memory
        self._config = config
        self._hashes: dict[str, str] = {}

    async def sync_entity(self, entity: EntityDefinition) -> bool:
        """Publish a single entity as an OrgFact.

        Returns ``True`` if published, ``False`` if content unchanged.

        Args:
            entity: Entity definition to publish.

        Returns:
            Whether the entity was published.
        """
        from synthorg.memory.org.models import (  # noqa: PLC0415
            OrgFactAuthor,
            OrgFactWriteRequest,
        )

        content = format_entity(entity)
        new_hash = _content_hash(content)

        if self._hashes.get(entity.name) == new_hash:
            logger.debug(
                ONTOLOGY_SYNC_SKIPPED,
                entity_name=entity.name,
                reason="content_unchanged",
            )
            return False

        request = OrgFactWriteRequest(
            content=content,
            category=OrgFactCategory.ENTITY_DEFINITION,
            tags=("entity", entity.name, f"tier:{entity.tier.value}"),
        )
        author = OrgFactAuthor(
            agent_id=NotBlankStr("system-ontology-sync"),
            seniority=SeniorityLevel.SENIOR,
        )
        await self._org_memory.write(request, author=author)
        self._hashes[entity.name] = new_hash

        logger.info(
            ONTOLOGY_SYNC_PUBLISHED,
            entity_name=entity.name,
            tier=entity.tier.value,
        )
        return True

    async def sync_all(self) -> int:
        """Sync all registered entity definitions.

        Returns:
            Number of newly published entities.
        """
        entities = await self._ontology.list_entities()
        published = 0
        for entity in entities:
            if await self.sync_entity(entity):
                published += 1
        return published
