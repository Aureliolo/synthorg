"""Ontology-aware memory backend decorator.

Wraps any ``MemoryBackend`` to auto-tag stored memories with entity
references and enrich retrieved memories with entity version info.
Fully transparent to callers -- implements the ``MemoryBackend``
protocol.
"""

import re
from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.ontology import (
    ONTOLOGY_MEMORY_DRIFT_WARNED,
    ONTOLOGY_MEMORY_ENRICHED,
    ONTOLOGY_MEMORY_ENRICHMENT_FAILED,
    ONTOLOGY_MEMORY_MANIFEST_FAILED,
    ONTOLOGY_MEMORY_TAGGED,
)

if TYPE_CHECKING:
    from synthorg.core.enums import MemoryCategory
    from synthorg.core.types import NotBlankStr
    from synthorg.memory.models import MemoryEntry, MemoryQuery, MemoryStoreRequest
    from synthorg.memory.protocol import MemoryBackend
    from synthorg.ontology.config import OntologyMemoryConfig
    from synthorg.ontology.protocol import OntologyBackend

logger = get_logger(__name__)


class OntologyAwareMemoryBackend:
    """Decorator around any ``MemoryBackend``.

    Implements the ``MemoryBackend`` protocol by delegating all
    operations to the inner backend, with two enhancements:

    - **store()**: Auto-detects entity name references in content
      and adds ``entity:<name>`` tags to metadata.  Optionally warns
      when content diverges from canonical definitions.
    - **retrieve()**: Enriches returned entry metadata with
      ``entity_version:<name>=<version>`` tags for any tagged entities.

    Entity detection uses case-insensitive word-boundary matching
    against registered entity names.  The entity name cache is
    refreshed on each ``store()`` call.

    Args:
        inner: The wrapped memory backend.
        ontology: Ontology backend for entity lookups.
        config: Ontology memory configuration.
    """

    __slots__ = ("_config", "_entity_names", "_inner", "_ontology")

    def __init__(
        self,
        inner: MemoryBackend,
        ontology: OntologyBackend,
        config: OntologyMemoryConfig,
    ) -> None:
        self._inner = inner
        self._ontology = ontology
        self._config = config
        self._entity_names: tuple[str, ...] = ()

    # ── Lifecycle (passthrough) ────────────────────────────────────

    async def connect(self) -> None:
        """Connect the inner backend."""
        await self._inner.connect()

    async def disconnect(self) -> None:
        """Disconnect the inner backend."""
        await self._inner.disconnect()

    async def health_check(self) -> bool:
        """Health check on the inner backend."""
        return await self._inner.health_check()

    @property
    def is_connected(self) -> bool:
        """Whether the inner backend is connected."""
        return self._inner.is_connected

    @property
    def backend_name(self) -> NotBlankStr:
        """Backend name prefixed with ``ontology:``."""
        return f"ontology:{self._inner.backend_name}"

    # ── Enhanced operations ────────────────────────────────────────

    async def store(
        self,
        agent_id: NotBlankStr,
        request: MemoryStoreRequest,
    ) -> NotBlankStr:
        """Store memory with auto-tagging of entity references.

        Detects entity names in the content text and adds
        ``entity:<name>`` tags to the metadata.

        Args:
            agent_id: Owning agent identifier.
            request: Memory content and metadata.

        Returns:
            The backend-assigned memory ID.
        """
        try:
            if self._config.auto_tag or self._config.warn_on_drift:
                await self._refresh_entity_names()
                found = self._detect_entities(request.content)
            else:
                found = ()
        except Exception:
            logger.warning(
                ONTOLOGY_MEMORY_ENRICHMENT_FAILED,
                agent_id=agent_id,
            )
            found = ()

        if found and self._config.auto_tag:
            new_tags = tuple(
                f"entity:{name}"
                for name in found
                if f"entity:{name}" not in request.metadata.tags
            )
            if new_tags:
                all_tags = (*request.metadata.tags, *new_tags)
                new_metadata = request.metadata.model_copy(
                    update={"tags": all_tags},
                )
                request = request.model_copy(
                    update={"metadata": new_metadata},
                )
                logger.debug(
                    ONTOLOGY_MEMORY_TAGGED,
                    agent_id=agent_id,
                    entities=found,
                    tag_count=len(new_tags),
                )

        if found and self._config.warn_on_drift:
            await self._warn_on_drift(agent_id, request.content, found)

        return await self._inner.store(agent_id, request)

    async def retrieve(  # noqa: C901
        self,
        agent_id: NotBlankStr,
        query: MemoryQuery,
    ) -> tuple[MemoryEntry, ...]:
        """Retrieve memories with entity version enrichment.

        Entries tagged with ``entity:<name>`` get additional
        ``entity_version:<name>=<version>`` tags reflecting the
        current canonical version.

        Args:
            agent_id: Owning agent identifier.
            query: Retrieval parameters.

        Returns:
            Enriched memory entries.
        """
        entries = await self._inner.retrieve(agent_id, query)
        if not entries:
            return entries

        try:
            manifest = await self._ontology.get_version_manifest()
        except Exception:
            logger.warning(
                ONTOLOGY_MEMORY_MANIFEST_FAILED,
                agent_id=agent_id,
            )
            return entries
        if not manifest:
            return entries

        enriched: list[MemoryEntry] = []
        enriched_count = 0
        for entry in entries:
            entity_tags = tuple(
                t for t in entry.metadata.tags if t.startswith("entity:")
            )
            if not entity_tags:
                enriched.append(entry)
                continue

            version_tags: list[str] = []
            for tag in entity_tags:
                entity_name = tag.removeprefix("entity:")
                if entity_name in manifest:
                    version_tags.append(
                        f"entity_version:{entity_name}={manifest[entity_name]}",
                    )

            if version_tags:
                existing = entry.metadata.tags
                entity_names = tuple(tag.removeprefix("entity:") for tag in entity_tags)
                filtered_existing = tuple(
                    t
                    for t in existing
                    if not any(
                        t.startswith(f"entity_version:{name}=") for name in entity_names
                    )
                )
                new_tags = tuple(t for t in version_tags if t not in filtered_existing)
                if new_tags:
                    all_tags = (*filtered_existing, *new_tags)
                    new_metadata = entry.metadata.model_copy(
                        update={"tags": all_tags},
                    )
                    entry = entry.model_copy(update={"metadata": new_metadata})  # noqa: PLW2901
                    enriched_count += 1

            enriched.append(entry)

        if enriched_count > 0:
            logger.debug(
                ONTOLOGY_MEMORY_ENRICHED,
                agent_id=agent_id,
                enriched_count=enriched_count,
            )

        return tuple(enriched)

    # ── Passthrough operations ─────────────────────────────────────

    async def get(
        self,
        agent_id: NotBlankStr,
        memory_id: NotBlankStr,
    ) -> MemoryEntry | None:
        """Get a specific memory entry by ID."""
        return await self._inner.get(agent_id, memory_id)

    async def delete(
        self,
        agent_id: NotBlankStr,
        memory_id: NotBlankStr,
    ) -> bool:
        """Delete a specific memory entry."""
        return await self._inner.delete(agent_id, memory_id)

    async def count(
        self,
        agent_id: NotBlankStr,
        *,
        category: MemoryCategory | None = None,
    ) -> int:
        """Count memory entries for an agent."""
        return await self._inner.count(agent_id, category=category)

    # ── Internal helpers ───────────────────────────────────────────

    async def _refresh_entity_names(self) -> None:
        """Refresh the cached entity name list from the ontology."""
        entities = await self._ontology.list_entities()
        self._entity_names = tuple(e.name for e in entities)

    def _detect_entities(self, content: str) -> tuple[str, ...]:
        """Detect entity name references in content text.

        Uses case-insensitive word-boundary matching.

        Args:
            content: Text to search for entity references.

        Returns:
            Tuple of matched entity names (deduplicated, ordered).
        """
        found: list[str] = []
        for name in self._entity_names:
            pattern = rf"\b{re.escape(name)}\b"
            if re.search(pattern, content, re.IGNORECASE):
                found.append(name)
        return tuple(found)

    async def _warn_on_drift(
        self,
        agent_id: str,
        content: str,
        entities: tuple[str, ...],
    ) -> None:
        """Log warning if content diverges from canonical definitions.

        Checks keyword overlap between content and each referenced
        entity's definition.  Logs a warning if overlap is low.

        Args:
            agent_id: Agent storing the memory.
            content: Memory content text.
            entities: Entity names found in content.
        """
        content_words = set(content.lower().split())
        for name in entities:
            try:
                entity = await self._ontology.get(name)
            except MemoryError, RecursionError:
                raise
            except Exception:
                logger.warning(
                    ONTOLOGY_MEMORY_DRIFT_WARNED,
                    agent_id=agent_id,
                    entity_name=name,
                    reason="entity_lookup_failed",
                )
                continue
            if not entity.definition:
                continue
            defn_words = set(entity.definition.lower().split())
            if not defn_words:
                continue
            overlap = len(content_words & defn_words) / len(defn_words)
            if overlap < 0.3:  # noqa: PLR2004
                logger.warning(
                    ONTOLOGY_MEMORY_DRIFT_WARNED,
                    agent_id=agent_id,
                    entity_name=name,
                    overlap_score=round(overlap, 3),
                )
