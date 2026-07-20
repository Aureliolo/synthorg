"""Tests for the SQL-backed memory backend.

Runs against a real SQLite database through the real repository, so the
hybrid retrieval path (embed, dense recall, lexical recall, RRF fusion)
is exercised end to end rather than mocked.
"""

import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite  # lint-allow: persistence-boundary -- opens the connection the repository under test is constructed with  # noqa: E501
import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.backends.sqlvector import SqlVectorBackend
from synthorg.memory.backends.sqlvector.adapter import _ORTHOGONAL_SIMILARITY
from synthorg.memory.errors import MemoryConnectionError
from synthorg.memory.models import (
    MemoryEntry,
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
    MemoryUpdateRequest,
)
from synthorg.persistence.sqlite.memory_vector_repo import SQLiteMemoryVectorRepository
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
_AGENT = NotBlankStr("agent-1")
_CAP = 100

_SCHEMA = """
CREATE TABLE memory_entries (
    memory_id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    namespace TEXT NOT NULL DEFAULT 'default',
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    tags TEXT NOT NULL DEFAULT '[]',
    token_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    expires_at TEXT
);
CREATE TABLE memory_entry_terms (
    memory_id TEXT NOT NULL REFERENCES memory_entries (memory_id) ON DELETE CASCADE,
    term TEXT NOT NULL,
    term_frequency INTEGER NOT NULL,
    PRIMARY KEY (memory_id, term)
);
"""


class WordEmbedder:
    """Deterministic bag-of-words embedder.

    A real embedding model would make these tests slow, non-deterministic
    and dependent on a network call. This maps each known word to one
    dimension, which gives genuine vector similarity behaviour (documents
    sharing words are near each other) with none of that cost.
    """

    _VOCABULARY = (
        "rollback",
        "deployment",
        "kubernetes",
        "scaling",
        "database",
        "migration",
        "incident",
        "postmortem",
    )

    @property
    def dimensions(self) -> int:
        """Width of every vector produced."""
        return len(self._VOCABULARY)

    async def embed_many(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """Embed each text as a normalised word-presence vector."""
        return tuple(self._embed_one(text) for text in texts)

    def _embed_one(self, text: str) -> tuple[float, ...]:
        lowered = text.lower()
        raw = [1.0 if word in lowered else 0.0 for word in self._VOCABULARY]
        magnitude = sum(value * value for value in raw) ** 0.5
        if magnitude == 0.0:
            return tuple(raw)
        return tuple(value / magnitude for value in raw)


@contextlib.asynccontextmanager
async def _no_op_write_context() -> AsyncIterator[None]:
    """Write serialisation is the persistence backend's job."""
    yield


@pytest.fixture
async def backend(tmp_path: Path) -> AsyncIterator[SqlVectorBackend]:
    """A connected backend over a real database."""
    async with aiosqlite.connect(str(tmp_path / "memory.db")) as db:
        db.row_factory = aiosqlite.Row
        await db.executescript(_SCHEMA)
        await db.commit()
        repository = SQLiteMemoryVectorRepository(
            db, write_context=_no_op_write_context
        )
        instance = SqlVectorBackend(
            repository,
            embedder=WordEmbedder(),
            max_memories_per_agent=_CAP,
            clock=FakeClock(start=_NOW),
        )
        await instance.connect()
        yield instance


def _request(content: str, **kwargs: object) -> MemoryStoreRequest:
    """Build a store request."""
    return MemoryStoreRequest(
        category=kwargs.pop("category", MemoryCategory.SEMANTIC),  # type: ignore[arg-type]
        content=NotBlankStr(content),
        **kwargs,  # type: ignore[arg-type]
    )


class TestLifecycle:
    """Connection gating."""

    async def test_connect_reports_dense_support(
        self, backend: SqlVectorBackend
    ) -> None:
        assert backend.is_connected is True
        assert backend.supports_dense_search is True

    async def test_operations_before_connect_are_rejected(self, tmp_path: Path) -> None:
        async with aiosqlite.connect(str(tmp_path / "x.db")) as db:
            db.row_factory = aiosqlite.Row
            await db.executescript(_SCHEMA)
            repository = SQLiteMemoryVectorRepository(
                db, write_context=_no_op_write_context
            )
            instance = SqlVectorBackend(repository, max_memories_per_agent=_CAP)

            with pytest.raises(MemoryConnectionError):
                await instance.count(_AGENT)

    async def test_health_check_false_when_disconnected(
        self, backend: SqlVectorBackend
    ) -> None:
        await backend.disconnect()

        assert await backend.health_check() is False

    async def test_health_check_true_when_connected(
        self, backend: SqlVectorBackend
    ) -> None:
        assert await backend.health_check() is True


class TestHybridRetrieval:
    """The property the previous substring store could not provide."""

    async def test_semantic_match_without_shared_words(
        self, backend: SqlVectorBackend
    ) -> None:
        # "kubernetes scaling" shares no term with the query, so a
        # substring or purely lexical store returns nothing. The dense
        # arm is what makes this recallable at all.
        await backend.store(_AGENT, _request("kubernetes scaling guidance"))

        hits = await backend.retrieve(
            _AGENT, MemoryQuery(text=NotBlankStr("scaling kubernetes"), limit=5)
        )

        assert [h.content for h in hits] == ["kubernetes scaling guidance"]

    async def test_lexical_match_for_out_of_vocabulary_term(
        self, backend: SqlVectorBackend
    ) -> None:
        # The embedder knows nothing about "sharding", so the dense arm
        # cannot help. BM25 is the orthogonal signal that rescues it,
        # which is the whole reason for fusing two arms.
        await backend.store(_AGENT, _request("sharding runbook"))

        hits = await backend.retrieve(
            _AGENT, MemoryQuery(text=NotBlankStr("sharding"), limit=5)
        )

        assert [h.content for h in hits] == ["sharding runbook"]

    async def test_more_relevant_memory_ranks_first(
        self, backend: SqlVectorBackend
    ) -> None:
        await backend.store(_AGENT, _request("rollback deployment procedure"))
        await backend.store(_AGENT, _request("postmortem incident notes"))

        hits = await backend.retrieve(
            _AGENT, MemoryQuery(text=NotBlankStr("rollback deployment"), limit=5)
        )

        assert hits[0].content == "rollback deployment procedure"

    async def test_irrelevant_query_recalls_nothing(
        self, backend: SqlVectorBackend
    ) -> None:
        await backend.store(_AGENT, _request("rollback deployment procedure"))

        hits = await backend.retrieve(
            _AGENT, MemoryQuery(text=NotBlankStr("zzzz"), limit=5)
        )

        assert hits == ()

    async def test_fused_score_is_within_bounds(
        self, backend: SqlVectorBackend
    ) -> None:
        await backend.store(_AGENT, _request("rollback deployment"))

        hits = await backend.retrieve(
            _AGENT, MemoryQuery(text=NotBlankStr("rollback"), limit=5)
        )

        assert hits[0].relevance_score is not None
        assert 0.0 <= hits[0].relevance_score <= 1.0

    async def test_metadata_only_query_skips_ranking(
        self, backend: SqlVectorBackend
    ) -> None:
        await backend.store(
            _AGENT, _request("alpha", category=MemoryCategory.PROCEDURAL)
        )
        await backend.store(_AGENT, _request("beta"))

        hits = await backend.retrieve(
            _AGENT,
            MemoryQuery(categories=frozenset({MemoryCategory.PROCEDURAL}), limit=5),
        )

        assert [h.content for h in hits] == ["alpha"]

    async def test_retrieval_is_agent_scoped(self, backend: SqlVectorBackend) -> None:
        await backend.store(NotBlankStr("agent-2"), _request("rollback deployment"))

        hits = await backend.retrieve(
            _AGENT, MemoryQuery(text=NotBlankStr("rollback"), limit=5)
        )

        assert hits == ()


class TestLexicalOnlyMode:
    """Recall must still work when no embedder is wired."""

    async def test_lexical_recall_without_embedder(self, tmp_path: Path) -> None:
        async with aiosqlite.connect(str(tmp_path / "lex.db")) as db:
            db.row_factory = aiosqlite.Row
            await db.executescript(_SCHEMA)
            await db.commit()
            repository = SQLiteMemoryVectorRepository(
                db, write_context=_no_op_write_context
            )
            instance = SqlVectorBackend(
                repository, max_memories_per_agent=_CAP, clock=FakeClock(start=_NOW)
            )
            await instance.connect()
            await instance.store(_AGENT, _request("rollback procedure"))

            hits = await instance.retrieve(
                _AGENT, MemoryQuery(text=NotBlankStr("rollback"), limit=5)
            )

            assert instance.supports_dense_search is False
            assert [h.content for h in hits] == ["rollback procedure"]


class TestCrud:
    """Store, read, update, delete and the per-agent cap."""

    async def test_store_returns_id_and_round_trips(
        self, backend: SqlVectorBackend
    ) -> None:
        memory_id = await backend.store(
            _AGENT,
            _request(
                "rollback procedure",
                metadata=MemoryMetadata(tags=(NotBlankStr("runbook"),)),
            ),
        )

        fetched = await backend.get(_AGENT, memory_id)

        assert fetched is not None
        assert fetched.content == "rollback procedure"
        assert fetched.metadata.tags == ("runbook",)
        assert fetched.created_at == _NOW

    async def test_update_content_changes_recall(
        self, backend: SqlVectorBackend
    ) -> None:
        memory_id = await backend.store(_AGENT, _request("rollback procedure"))

        await backend.update(
            _AGENT,
            memory_id,
            MemoryUpdateRequest(content=NotBlankStr("kubernetes scaling")),
        )
        stale = await backend.retrieve(
            _AGENT, MemoryQuery(text=NotBlankStr("rollback"), limit=5)
        )
        fresh = await backend.retrieve(
            _AGENT, MemoryQuery(text=NotBlankStr("kubernetes"), limit=5)
        )

        assert stale == ()
        assert [h.content for h in fresh] == ["kubernetes scaling"]

    async def test_update_missing_returns_none(self, backend: SqlVectorBackend) -> None:
        result = await backend.update(
            _AGENT,
            NotBlankStr("missing"),
            MemoryUpdateRequest(content=NotBlankStr("x")),
        )

        assert result is None

    async def test_delete_removes_memory(self, backend: SqlVectorBackend) -> None:
        memory_id = await backend.store(_AGENT, _request("rollback"))

        assert await backend.delete(_AGENT, memory_id) is True
        assert await backend.get(_AGENT, memory_id) is None

    async def test_count_is_agent_scoped(self, backend: SqlVectorBackend) -> None:
        await backend.store(_AGENT, _request("alpha"))
        await backend.store(NotBlankStr("agent-2"), _request("beta"))

        assert await backend.count(_AGENT) == 1

    async def test_cap_evicts_oldest(self, tmp_path: Path) -> None:
        clock = FakeClock(start=_NOW)

        async with aiosqlite.connect(str(tmp_path / "cap.db")) as db:
            db.row_factory = aiosqlite.Row
            await db.executescript(_SCHEMA)
            await db.commit()
            repository = SQLiteMemoryVectorRepository(
                db, write_context=_no_op_write_context
            )
            instance = SqlVectorBackend(
                repository,
                embedder=WordEmbedder(),
                max_memories_per_agent=2,
                clock=clock,
            )
            await instance.connect()

            for index in range(3):
                clock.advance(float(index + 1))
                await instance.store(_AGENT, _request(f"memory number {index}"))

            remaining = await instance.retrieve(_AGENT, MemoryQuery(limit=10))

            assert await instance.count(_AGENT) == 2
            assert "memory number 0" not in [h.content for h in remaining]


class TestDropUnrelated:
    """Dense hits at or below orthogonal carry no evidence and are dropped.

    Fusing them back in lets a vector no more related than a random one
    contribute recall, which is the noise the two-stage design exists to
    keep out. The threshold is exclusive, so a hit exactly at orthogonal
    is dropped, not kept.
    """

    @staticmethod
    def _hit(entry_id: str, relevance_score: float | None) -> MemoryEntry:
        return MemoryEntry(
            id=NotBlankStr(entry_id),
            agent_id=_AGENT,
            category=MemoryCategory.SEMANTIC,
            content=NotBlankStr("a hit"),
            metadata=MemoryMetadata(),
            created_at=_NOW,
            relevance_score=relevance_score,
        )

    def test_keeps_only_hits_above_the_orthogonal_floor(self) -> None:
        above = self._hit("above", _ORTHOGONAL_SIMILARITY + 0.01)
        at_floor = self._hit("at", _ORTHOGONAL_SIMILARITY)
        unscored = self._hit("none", None)

        kept = SqlVectorBackend._drop_unrelated((above, at_floor, unscored))

        assert [h.id for h in kept] == ["above"]
