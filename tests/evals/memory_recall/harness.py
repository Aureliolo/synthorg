"""Runs the golden recall set through the real retrieval pipeline.

Everything below the injection strategy is real: a real SQLite database,
the real vector repository, the real hybrid dense + BM25 fusion. Only
the embedding model is substituted, because a real one would make the
eval slow, networked and non-deterministic while telling us nothing
extra about ranking behaviour.
"""

import contextlib
import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import aiosqlite  # lint-allow: persistence-boundary -- opens the connection the repository under measurement is constructed with  # noqa: E501

from synthorg.core.types import NotBlankStr
from synthorg.memory.backends.inmemory import InMemoryBackend
from synthorg.memory.backends.sqlvector import SqlVectorBackend
from synthorg.memory.models import MemoryMetadata, MemoryStoreRequest
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.retriever import ContextInjectionStrategy
from synthorg.persistence.sqlite.memory_vector_repo import SQLiteMemoryVectorRepository
from tests._shared import FakeClock, recall_request
from tests.evals.memory_recall.golden_set import CASES, CORPUS, GoldenCase

_NOW: Final[datetime] = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
_CAP: Final[int] = 100
_TOKEN_BUDGET: Final[int] = 2000

_SCHEMA: Final[str] = """
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


# Each dimension is a concept, and several surface words map onto it.
# A word-presence embedder would be lexical matching in vector clothing:
# it could never place "revert the release" near "rollback of a
# deployment", so a dense arm built on one cannot demonstrate anything a
# term index does not already do. Mapping synonyms onto shared
# dimensions is the minimum faithful stand-in for a real model, and it
# is what makes the comparison against keyword matching meaningful.
_CONCEPTS: Final[tuple[tuple[str, ...], ...]] = (
    ("rollback", "revert", "roll back", "undo"),
    ("deployment", "deploy", "release", "ship"),
    ("database", "schema", "sql"),
    ("migration", "migrate", "upgrade"),
    ("kubernetes", "k8s", "cluster", "orchestrator"),
    ("scaling", "scale", "autoscale", "capacity"),
    ("incident", "outage", "failure"),
    ("postmortem", "retrospective", "review"),
    ("connection", "pool", "connections"),
    ("lock", "deadlock", "exclusive"),
    ("marketing", "campaign", "advertising"),
    ("budget", "spend", "forecast"),
    ("quarterly", "quarter", "annual"),
)


class GoldenEmbedder:
    """Deterministic concept embedder over the corpus vocabulary.

    Each dimension is a concept rather than a word, so texts about the
    same thing land near each other even when they share no literal
    term. That is the property dense retrieval is bought for, and the
    only one that distinguishes it from the lexical arm.

    Deterministic and offline on purpose: a real model would make the
    eval slow, networked and variable without changing what it measures.
    """

    @property
    def dimensions(self) -> int:
        """Width of every vector produced."""
        return len(_CONCEPTS)

    async def embed_many(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """Embed each text as a normalised concept-presence vector.

        Returns:
            One unit vector per input text.
        """
        return tuple(self._embed_one(text) for text in texts)

    def _embed_one(self, text: str) -> tuple[float, ...]:
        lowered = text.lower()
        raw = [
            1.0 if any(word in lowered for word in concept) else 0.0
            for concept in _CONCEPTS
        ]
        magnitude = sum(value * value for value in raw) ** 0.5
        if magnitude == 0.0:
            return tuple(raw)
        return tuple(value / magnitude for value in raw)


@contextlib.asynccontextmanager
async def _no_op_write_context() -> AsyncIterator[None]:
    """Write serialisation is the persistence backend's job."""
    yield


@contextlib.asynccontextmanager
async def seeded_naive_backend() -> AsyncIterator[InMemoryBackend]:
    """Yield the discouraged keyword-only backend, seeded identically.

    This is the baseline the issue is measured against: before this
    work the shared backend was an ephemeral in-process store whose
    entire matcher was a term test, with no embeddings and nothing
    surviving a restart.

    Yields:
        The connected naive backend, seeded and ready to query.
    """
    backend = InMemoryBackend(clock=FakeClock(start=_NOW))
    await backend.connect()
    for memory in CORPUS:
        await backend.store(
            NotBlankStr(memory.agent_id),
            MemoryStoreRequest(
                category=memory.category,
                content=NotBlankStr(memory.content),
                metadata=MemoryMetadata(
                    tags=tuple(NotBlankStr(t) for t in memory.tags)
                ),
            ),
        )
    yield backend


@contextlib.asynccontextmanager
async def _durable_backend(
    db_path: Path, *, create_schema: bool
) -> AsyncIterator[SqlVectorBackend]:
    """Yield a durable backend over *db_path*.

    Yields:
        The connected backend, ready to store or query.
    """
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        if create_schema:
            await db.executescript(_SCHEMA)
            await db.commit()
        backend = SqlVectorBackend(
            SQLiteMemoryVectorRepository(db, write_context=_no_op_write_context),
            embedder=GoldenEmbedder(),
            max_memories_per_agent=_CAP,
            clock=FakeClock(start=_NOW),
        )
        await backend.connect()
        yield backend


async def _seed_corpus(backend: SqlVectorBackend) -> None:
    """Store the golden corpus into *backend*."""
    for memory in CORPUS:
        await backend.store(
            NotBlankStr(memory.agent_id),
            MemoryStoreRequest(
                category=memory.category,
                content=NotBlankStr(memory.content),
                metadata=MemoryMetadata(
                    tags=tuple(NotBlankStr(t) for t in memory.tags)
                ),
            ),
        )


@contextlib.asynccontextmanager
async def seeded_backend(db_path: Path) -> AsyncIterator[SqlVectorBackend]:
    """Yield a backend holding the golden corpus.

    Yields:
        The connected backend, seeded and ready to query.
    """
    async with _durable_backend(db_path, create_schema=True) as backend:
        await _seed_corpus(backend)
        yield backend


@contextlib.asynccontextmanager
async def reopened_backend(db_path: Path) -> AsyncIterator[SqlVectorBackend]:
    """Yield a fresh backend over an already-seeded database file.

    The point of durable memory: a second process opening the same
    store recalls what the first wrote. Re-uses the file without
    re-creating the schema or re-seeding, so anything recalled here
    genuinely survived the "restart".

    Yields:
        The connected backend, ready to query.
    """
    async with _durable_backend(db_path, create_schema=False) as backend:
        yield backend


def _recalled_ids(messages: tuple[object, ...]) -> frozenset[str]:
    """Map injected message text back to the corpus ids it came from.

    The formatter emits fenced content rather than identifiers, so the
    corpus is matched by content. Anchoring on the stored text keeps the
    eval measuring what actually reached the prompt.

    Returns:
        Ids of the corpus memories present in the injected messages.
    """
    blob = "\n".join(str(getattr(message, "content", "") or "") for message in messages)
    normalised = re.sub(r"\s+", " ", blob)
    return frozenset(
        memory.memory_id
        for memory in CORPUS
        if re.sub(r"\s+", " ", memory.content) in normalised
    )


async def run_case(
    backend: MemoryBackend,
    config: MemoryRetrievalConfig,
    case: GoldenCase,
) -> frozenset[str]:
    """Run one golden case through the real injection pipeline.

    Returns:
        The corpus ids that reached the agent's context.
    """
    strategy = ContextInjectionStrategy(backend=backend, config=config)
    messages = await strategy.prepare_messages(
        recall_request(
            agent_id=case.agent_id,
            query=case.query,
            token_budget=_TOKEN_BUDGET,
        )
    )
    return _recalled_ids(messages)


async def run_suite(
    backend: MemoryBackend,
    config: MemoryRetrievalConfig,
) -> dict[str, frozenset[str]]:
    """Run every golden case under one configuration.

    Returns:
        Case name to the corpus ids recalled.
    """
    return {case.name: await run_case(backend, config, case) for case in CASES}


def write_report(path: Path, payload: dict[str, object]) -> None:
    """Persist a scorecard so a regression is inspectable after the fact."""
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
