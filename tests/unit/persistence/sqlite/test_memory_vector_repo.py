"""Tests for the SQLite agent-memory vector repository.

Runs against a real on-disk SQLite database with the declared schema
applied, so the SQL, the sqlite-vec extension and the BM25 scoring are
all exercised for real rather than mocked.
"""

import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest
from structlog.testing import capture_logs

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.vector_spec import MemoryVectorSearchSpec
from synthorg.observability.events.memory import MEMORY_DENSE_INDEX_WIDTH_CHANGED
from synthorg.persistence.sqlite.memory_vector_repo import SQLiteMemoryVectorRepository

pytestmark = pytest.mark.unit

_DIMS = 4
_NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)

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


@contextlib.asynccontextmanager
async def _no_op_write_context() -> AsyncIterator[None]:
    """Write serialisation is the backend's job; tests own the connection."""
    yield


def _entry(  # noqa: PLR0913 -- a fixture builder; each field is one test axis
    memory_id: str,
    content: str,
    *,
    agent_id: str = "agent-1",
    category: MemoryCategory = MemoryCategory.SEMANTIC,
    namespace: str = "default",
    tags: tuple[str, ...] = (),
    created_at: datetime = _NOW,
    expires_at: datetime | None = None,
) -> MemoryEntry:
    """Build a memory entry for the tests."""
    return MemoryEntry(
        id=NotBlankStr(memory_id),
        agent_id=NotBlankStr(agent_id),
        namespace=NotBlankStr(namespace),
        category=category,
        content=NotBlankStr(content),
        metadata=MemoryMetadata(tags=tuple(NotBlankStr(t) for t in tags)),
        created_at=created_at,
        expires_at=expires_at,
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Path to a throwaway database file."""
    return tmp_path / "memory.db"


@pytest.fixture
async def repo(db_path: Path) -> AsyncIterator[SQLiteMemoryVectorRepository]:
    """A ready repository over a real database with the declared schema."""
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        await db.executescript(_SCHEMA)
        await db.commit()
        repository = SQLiteMemoryVectorRepository(
            db,
            write_context=_no_op_write_context,
        )
        await repository.ensure_ready(_DIMS)
        yield repository


class TestDenseSearch:
    """Semantic recall via sqlite-vec."""

    async def test_extension_loads(self, repo: SQLiteMemoryVectorRepository) -> None:
        assert repo.supports_dense_search is True

    async def test_nearest_vector_ranks_first(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        await repo.upsert(_entry("m1", "alpha"), embedding=(1.0, 0.0, 0.0, 0.0))
        await repo.upsert(_entry("m2", "beta"), embedding=(0.0, 1.0, 0.0, 0.0))

        hits = await repo.search_dense(
            MemoryVectorSearchSpec(
                agent_id=NotBlankStr("agent-1"),
                embedding=(0.9, 0.1, 0.0, 0.0),
                limit=2,
            )
        )

        assert [h.id for h in hits] == ["m1", "m2"]

    async def test_relevance_score_within_bounds(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        await repo.upsert(_entry("m1", "alpha"), embedding=(1.0, 0.0, 0.0, 0.0))

        hits = await repo.search_dense(
            MemoryVectorSearchSpec(
                agent_id=NotBlankStr("agent-1"),
                embedding=(1.0, 0.0, 0.0, 0.0),
                limit=1,
            )
        )

        assert hits[0].relevance_score is not None
        assert 0.0 <= hits[0].relevance_score <= 1.0

    async def test_without_embedding_returns_empty(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        await repo.upsert(_entry("m1", "alpha"), embedding=(1.0, 0.0, 0.0, 0.0))

        hits = await repo.search_dense(
            MemoryVectorSearchSpec(agent_id=NotBlankStr("agent-1"))
        )

        assert hits == ()

    async def test_dense_respects_agent_scope(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        await repo.upsert(
            _entry("m1", "alpha", agent_id="agent-2"),
            embedding=(1.0, 0.0, 0.0, 0.0),
        )

        hits = await repo.search_dense(
            MemoryVectorSearchSpec(
                agent_id=NotBlankStr("agent-1"),
                embedding=(1.0, 0.0, 0.0, 0.0),
                limit=5,
            )
        )

        assert hits == ()

    async def test_other_agents_cannot_crowd_out_the_caller(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        """Ownership must bound the KNN, not filter it afterwards.

        Filtering after the k nearest are chosen spends every slot on
        whichever agent owns the closest vectors, so a busy neighbour
        silently reduces everyone else's dense recall to nothing.
        """
        for i in range(50):
            await repo.upsert(
                _entry(f"other-{i}", "noise", agent_id="agent-2"),
                embedding=(1.0, 0.001 * i, 0.0, 0.0),
            )
        await repo.upsert(
            _entry("mine", "the one that matters"),
            embedding=(0.9, 0.4, 0.0, 0.0),
        )

        hits = await repo.search_dense(
            MemoryVectorSearchSpec(
                agent_id=NotBlankStr("agent-1"),
                embedding=(1.0, 0.0, 0.0, 0.0),
                limit=10,
            )
        )

        assert [h.id for h in hits] == ["mine"]

    async def test_deleted_vector_is_not_inherited_by_a_later_entry(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        """A new memory must never answer for a deleted one's vector.

        SQLite reuses rowids, so correlating the dense index by rowid let
        a fresh entry inherit the embedding of whichever entry last held
        that rowid and rank as an exact match for content it never had.
        """
        await repo.upsert(_entry("gone", "secret"), embedding=(1.0, 0.0, 0.0, 0.0))
        await repo.delete(NotBlankStr("agent-1"), NotBlankStr("gone"))

        await repo.upsert(_entry("fresh", "totally unrelated"), embedding=None)

        hits = await repo.search_dense(
            MemoryVectorSearchSpec(
                agent_id=NotBlankStr("agent-1"),
                embedding=(1.0, 0.0, 0.0, 0.0),
                limit=5,
            )
        )

        assert hits == ()

    async def test_rewriting_content_without_an_embedding_drops_the_vector(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        """A stale vector would match content the entry no longer holds."""
        await repo.upsert(_entry("m1", "rollback"), embedding=(1.0, 0.0, 0.0, 0.0))
        await repo.upsert(_entry("m1", "something else entirely"), embedding=None)

        hits = await repo.search_dense(
            MemoryVectorSearchSpec(
                agent_id=NotBlankStr("agent-1"),
                embedding=(1.0, 0.0, 0.0, 0.0),
                limit=5,
            )
        )

        assert hits == ()

    async def test_equidistant_hits_order_deterministically(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        for name in ("m3", "m1", "m2"):
            await repo.upsert(_entry(name, "same"), embedding=(1.0, 0.0, 0.0, 0.0))

        hits = await repo.search_dense(
            MemoryVectorSearchSpec(
                agent_id=NotBlankStr("agent-1"),
                embedding=(1.0, 0.0, 0.0, 0.0),
                limit=5,
            )
        )

        assert [h.id for h in hits] == ["m1", "m2", "m3"]


class TestLexicalSearch:
    """Keyword recall via the inverted index and BM25."""

    async def test_matching_term_is_found(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        await repo.upsert(_entry("m1", "rollback the deployment"), embedding=None)
        await repo.upsert(_entry("m2", "unrelated content here"), embedding=None)

        hits = await repo.search_lexical(
            MemoryVectorSearchSpec(
                agent_id=NotBlankStr("agent-1"),
                text=NotBlankStr("rollback"),
                limit=5,
            )
        )

        assert [h.id for h in hits] == ["m1"]

    async def test_more_query_terms_ranks_higher(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        await repo.upsert(_entry("m1", "rollback deployment procedure"), embedding=None)
        await repo.upsert(_entry("m2", "rollback only"), embedding=None)

        hits = await repo.search_lexical(
            MemoryVectorSearchSpec(
                agent_id=NotBlankStr("agent-1"),
                text=NotBlankStr("rollback deployment"),
                limit=5,
            )
        )

        assert hits[0].id == "m1"

    async def test_no_match_returns_empty(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        await repo.upsert(_entry("m1", "rollback"), embedding=None)

        hits = await repo.search_lexical(
            MemoryVectorSearchSpec(
                agent_id=NotBlankStr("agent-1"),
                text=NotBlankStr("kubernetes"),
                limit=5,
            )
        )

        assert hits == ()

    async def test_stop_word_only_query_returns_empty(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        await repo.upsert(_entry("m1", "rollback"), embedding=None)

        hits = await repo.search_lexical(
            MemoryVectorSearchSpec(
                agent_id=NotBlankStr("agent-1"),
                text=NotBlankStr("the and of"),
                limit=5,
            )
        )

        assert hits == ()

    async def test_reindex_on_update_drops_old_terms(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        await repo.upsert(_entry("m1", "rollback procedure"), embedding=None)
        await repo.upsert(_entry("m1", "kubernetes scaling"), embedding=None)

        stale = await repo.search_lexical(
            MemoryVectorSearchSpec(
                agent_id=NotBlankStr("agent-1"),
                text=NotBlankStr("rollback"),
                limit=5,
            )
        )
        fresh = await repo.search_lexical(
            MemoryVectorSearchSpec(
                agent_id=NotBlankStr("agent-1"),
                text=NotBlankStr("kubernetes"),
                limit=5,
            )
        )

        assert stale == ()
        assert [h.id for h in fresh] == ["m1"]


class TestFiltering:
    """Filters must apply identically across every read path."""

    async def test_category_filter(self, repo: SQLiteMemoryVectorRepository) -> None:
        await repo.upsert(
            _entry("m1", "alpha", category=MemoryCategory.SEMANTIC), embedding=None
        )
        await repo.upsert(
            _entry("m2", "alpha", category=MemoryCategory.PROCEDURAL), embedding=None
        )

        hits = await repo.list_filtered(
            MemoryVectorSearchSpec(
                agent_id=NotBlankStr("agent-1"),
                categories=frozenset({MemoryCategory.PROCEDURAL}),
                limit=5,
            )
        )

        assert [h.id for h in hits] == ["m2"]

    async def test_tags_use_and_semantics(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        await repo.upsert(_entry("m1", "alpha", tags=("x", "y")), embedding=None)
        await repo.upsert(_entry("m2", "alpha", tags=("x",)), embedding=None)

        hits = await repo.list_filtered(
            MemoryVectorSearchSpec(
                agent_id=NotBlankStr("agent-1"),
                tags=(NotBlankStr("x"), NotBlankStr("y")),
                limit=5,
            )
        )

        assert [h.id for h in hits] == ["m1"]

    async def test_namespace_filter(self, repo: SQLiteMemoryVectorRepository) -> None:
        await repo.upsert(_entry("m1", "alpha", namespace="scratch"), embedding=None)
        await repo.upsert(_entry("m2", "alpha", namespace="default"), embedding=None)

        hits = await repo.list_filtered(
            MemoryVectorSearchSpec(
                agent_id=NotBlankStr("agent-1"),
                namespaces=frozenset({NotBlankStr("scratch")}),
                limit=5,
            )
        )

        assert [h.id for h in hits] == ["m1"]

    async def test_expired_entries_excluded_when_now_given(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        await repo.upsert(
            _entry("m1", "alpha", expires_at=_NOW + timedelta(hours=1)),
            embedding=None,
        )

        hits = await repo.list_filtered(
            MemoryVectorSearchSpec(
                agent_id=NotBlankStr("agent-1"),
                now=_NOW + timedelta(hours=2),
                limit=5,
            )
        )

        assert hits == ()

    async def test_lexical_applies_the_same_filters(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        await repo.upsert(
            _entry("m1", "rollback", category=MemoryCategory.PROCEDURAL),
            embedding=None,
        )

        hits = await repo.search_lexical(
            MemoryVectorSearchSpec(
                agent_id=NotBlankStr("agent-1"),
                text=NotBlankStr("rollback"),
                categories=frozenset({MemoryCategory.SEMANTIC}),
                limit=5,
            )
        )

        assert hits == ()


class TestCrud:
    """Round-tripping, scoping and lifecycle."""

    async def test_round_trip_preserves_fields(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        await repo.upsert(
            _entry("m1", "alpha", tags=("x",), category=MemoryCategory.EPISODIC),
            embedding=None,
        )

        fetched = await repo.get(NotBlankStr("agent-1"), NotBlankStr("m1"))

        assert fetched is not None
        assert fetched.content == "alpha"
        assert fetched.category is MemoryCategory.EPISODIC
        assert fetched.metadata.tags == ("x",)
        assert fetched.created_at == _NOW

    async def test_get_is_agent_scoped(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        await repo.upsert(_entry("m1", "alpha"), embedding=None)

        assert await repo.get(NotBlankStr("agent-2"), NotBlankStr("m1")) is None

    async def test_delete_removes_entry(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        await repo.upsert(_entry("m1", "alpha"), embedding=(1.0, 0.0, 0.0, 0.0))

        assert await repo.delete(NotBlankStr("agent-1"), NotBlankStr("m1")) is True
        assert await repo.get(NotBlankStr("agent-1"), NotBlankStr("m1")) is None

    async def test_delete_is_agent_scoped(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        await repo.upsert(_entry("m1", "alpha"), embedding=None)

        assert await repo.delete(NotBlankStr("agent-2"), NotBlankStr("m1")) is False
        assert await repo.get(NotBlankStr("agent-1"), NotBlankStr("m1")) is not None

    async def test_delete_missing_returns_false(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        assert await repo.delete(NotBlankStr("agent-1"), NotBlankStr("nope")) is False

    async def test_count_by_agent_and_category(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        await repo.upsert(
            _entry("m1", "alpha", category=MemoryCategory.SEMANTIC), embedding=None
        )
        await repo.upsert(
            _entry("m2", "beta", category=MemoryCategory.PROCEDURAL), embedding=None
        )
        await repo.upsert(
            _entry("m3", "gamma", agent_id="agent-2"),
            embedding=None,
        )

        assert await repo.count(NotBlankStr("agent-1")) == 2
        assert (
            await repo.count(NotBlankStr("agent-1"), category=MemoryCategory.PROCEDURAL)
            == 1
        )

    async def test_purge_expired_deletes_only_expired(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        await repo.upsert(
            _entry("stale", "alpha", expires_at=_NOW + timedelta(hours=1)),
            embedding=None,
        )
        await repo.upsert(_entry("live", "beta"), embedding=None)

        purged = await repo.purge_expired(_NOW + timedelta(hours=2))

        assert purged == 1
        assert await repo.get(NotBlankStr("agent-1"), NotBlankStr("live")) is not None

    async def test_purge_expired_chunks_beyond_bind_param_limit(
        self,
        repo: SQLiteMemoryVectorRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A sweep wider than the bind-param ceiling deletes across chunks.

        With the ceiling forced low, more expired ids than one statement
        can bind still purge cleanly, proving the delete is split rather
        than issued as one over-long ``IN (?, ...)``.
        """
        monkeypatch.setattr(
            "synthorg.persistence.sqlite.memory_vector_repo._SQLITE_MAX_BIND_PARAMS",
            2,
        )
        expired_count = 5
        for i in range(expired_count):
            await repo.upsert(
                _entry(f"stale-{i}", "alpha", expires_at=_NOW + timedelta(hours=1)),
                embedding=None,
            )
        await repo.upsert(_entry("live", "beta"), embedding=None)

        purged = await repo.purge_expired(_NOW + timedelta(hours=2))

        assert purged == expired_count
        assert await repo.count(NotBlankStr("agent-1")) == 1
        assert await repo.get(NotBlankStr("agent-1"), NotBlankStr("live")) is not None

    async def test_oldest_ids_returns_oldest_first(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        await repo.upsert(
            _entry("old", "alpha", created_at=_NOW - timedelta(days=2)), embedding=None
        )
        await repo.upsert(_entry("new", "beta", created_at=_NOW), embedding=None)

        assert await repo.oldest_ids(NotBlankStr("agent-1"), excess=1) == ("old",)

    async def test_oldest_ids_non_positive_excess_is_empty(
        self, repo: SQLiteMemoryVectorRepository
    ) -> None:
        assert await repo.oldest_ids(NotBlankStr("agent-1"), excess=0) == ()


class TestDurability:
    """The property the previous in-process store could never satisfy."""

    async def test_memory_survives_reconnect(self, db_path: Path) -> None:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            await db.executescript(_SCHEMA)
            await db.commit()
            first = SQLiteMemoryVectorRepository(db, write_context=_no_op_write_context)
            await first.ensure_ready(_DIMS)
            await first.upsert(
                _entry("m1", "rollback procedure"), embedding=(1.0, 0.0, 0.0, 0.0)
            )

        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            second = SQLiteMemoryVectorRepository(
                db, write_context=_no_op_write_context
            )
            await second.ensure_ready(_DIMS)

            survived = await second.get(NotBlankStr("agent-1"), NotBlankStr("m1"))
            recalled = await second.search_dense(
                MemoryVectorSearchSpec(
                    agent_id=NotBlankStr("agent-1"),
                    embedding=(1.0, 0.0, 0.0, 0.0),
                    limit=1,
                )
            )

        assert survived is not None
        assert [h.id for h in recalled] == ["m1"]

    async def test_width_change_is_reported_not_silently_swallowed(
        self, db_path: Path
    ) -> None:
        """A model swap orphans stored vectors; the operator must be told.

        Recall goes silently empty otherwise, which reads as a memory
        bug rather than as the consequence of the swap.
        """
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            await db.executescript(_SCHEMA)
            await db.commit()
            first = SQLiteMemoryVectorRepository(db, write_context=_no_op_write_context)
            await first.ensure_ready(_DIMS)
            await first.upsert(
                _entry("m1", "rollback procedure"), embedding=(1.0, 0.0, 0.0, 0.0)
            )

        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            second = SQLiteMemoryVectorRepository(
                db, write_context=_no_op_write_context
            )
            with capture_logs() as logs:
                await second.ensure_ready(_DIMS + 1)

        reported = [
            entry
            for entry in logs
            if entry["event"] == MEMORY_DENSE_INDEX_WIDTH_CHANGED
        ]
        assert len(reported) == 1
        assert reported[0]["log_level"] == "error"
        assert reported[0]["orphaned_vectors"] == 1
        assert reported[0]["previous_index"] == f"memory_entries_vec_{_DIMS}"


class TestQueryErrorPaths:
    """A failed statement surfaces as a typed ``QueryError``, never a raw
    sqlite error crossing the persistence boundary."""

    async def test_read_raises_query_error(self, db_path: Path) -> None:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            await db.executescript(_SCHEMA)
            await db.commit()
            repo = SQLiteMemoryVectorRepository(db, write_context=_no_op_write_context)
            await repo.ensure_ready(_DIMS)
            # Remove the table out from under the repository to force the
            # next statement to fail.
            await db.execute("DROP TABLE memory_entries")
            await db.commit()

            with pytest.raises(QueryError):
                await repo.get(NotBlankStr("agent-1"), NotBlankStr("m1"))

    async def test_write_raises_query_error(self, db_path: Path) -> None:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            await db.executescript(_SCHEMA)
            await db.commit()
            repo = SQLiteMemoryVectorRepository(db, write_context=_no_op_write_context)
            await repo.ensure_ready(_DIMS)
            await db.execute("DROP TABLE memory_entries")
            await db.commit()

            with pytest.raises(QueryError):
                await repo.upsert(_entry("m1", "alpha"), embedding=(1.0, 0.0, 0.0, 0.0))
