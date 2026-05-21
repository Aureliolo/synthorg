"""Dict-backed fake repositories for knowledge unit tests.

Hand-written fakes (not ``MagicMock``) so they satisfy the
``@runtime_checkable`` repository protocols and model the stateful
save/query behaviour the indexer and retriever depend on. Using real
fakes keeps the ``check_mock_spec`` gate happy and exercises ordering /
filtering logic the way the durable backends do.
"""

from synthorg.core.types import NotBlankStr
from synthorg.knowledge.models import ChunkProvenanceRow, KnowledgeSource
from synthorg.persistence.knowledge_protocol import (
    ChunkProvenanceFilter,
    KnowledgeSourceFilter,
)


class FakeKnowledgeSourceRepository:
    """In-memory ``KnowledgeSourceRepository`` for unit tests."""

    def __init__(self) -> None:
        self._rows: dict[str, KnowledgeSource] = {}

    async def save(self, entity: KnowledgeSource) -> None:
        self._rows[entity.source_id] = entity

    async def get(self, entity_id: NotBlankStr) -> KnowledgeSource | None:
        return self._rows.get(entity_id)

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return self._rows.pop(entity_id, None) is not None

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[KnowledgeSource, ...]:
        ordered = sorted(
            self._rows.values(),
            key=lambda s: (s.updated_at, s.source_id),
            reverse=True,
        )
        return tuple(ordered[offset : offset + limit])

    async def query(
        self,
        filter_spec: KnowledgeSourceFilter,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[KnowledgeSource, ...]:
        rows = [s for s in self._rows.values() if self._matches(s, filter_spec)]
        rows.sort(key=lambda s: (s.updated_at, s.source_id), reverse=True)
        return tuple(rows[offset : offset + limit])

    async def count(self, filter_spec: KnowledgeSourceFilter) -> int:
        return sum(1 for s in self._rows.values() if self._matches(s, filter_spec))

    @staticmethod
    def _matches(source: KnowledgeSource, spec: KnowledgeSourceFilter) -> bool:
        pid = spec.project_id
        if pid is not None and spec.include_global:
            if source.project_id not in (pid, None):
                return False
        elif pid is not None:
            if source.project_id != pid:
                return False
        elif spec.include_global and source.project_id is not None:
            return False
        if spec.source_type is not None and source.source_type != spec.source_type:
            return False
        if spec.status is not None and source.status != spec.status:
            return False
        return not (spec.stale_only and source.status.value != "stale")


class FakeChunkProvenanceRepository:
    """In-memory ``ChunkProvenanceRepository`` for unit tests."""

    def __init__(self) -> None:
        self._rows: dict[str, ChunkProvenanceRow] = {}

    async def save(self, entity: ChunkProvenanceRow) -> None:
        self._rows[entity.chunk_id] = entity

    async def get(self, entity_id: NotBlankStr) -> ChunkProvenanceRow | None:
        return self._rows.get(entity_id)

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return self._rows.pop(entity_id, None) is not None

    async def get_many(
        self, chunk_ids: tuple[NotBlankStr, ...]
    ) -> tuple[ChunkProvenanceRow, ...]:
        return tuple(self._rows[cid] for cid in chunk_ids if cid in self._rows)

    async def delete_by_source(self, source_id: NotBlankStr) -> int:
        victims = [cid for cid, r in self._rows.items() if r.source_id == source_id]
        for cid in victims:
            del self._rows[cid]
        return len(victims)

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[ChunkProvenanceRow, ...]:
        ordered = sorted(
            self._rows.values(), key=lambda r: (r.source_id, r.chunk_index)
        )
        return tuple(ordered[offset : offset + limit])

    async def query(
        self,
        filter_spec: ChunkProvenanceFilter,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ChunkProvenanceRow, ...]:
        rows = sorted(
            (r for r in self._rows.values() if r.source_id == filter_spec.source_id),
            key=lambda r: r.chunk_index,
        )
        return tuple(rows[offset : offset + limit])

    async def count(self, filter_spec: ChunkProvenanceFilter) -> int:
        return sum(
            1 for r in self._rows.values() if r.source_id == filter_spec.source_id
        )
