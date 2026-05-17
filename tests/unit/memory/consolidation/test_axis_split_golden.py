"""Byte-identical golden regression guard for the RFC#10 axis split.

Pins the exact ``ConsolidationResult`` and stored-summary content for
Simple / DualMode / LLM on fixed inputs. These assertions are written
against EXPLICIT expected values (derived from the pre-split monolith
behaviour and from refactor-stable components like
``ExtractivePreserver``), not by calling the monolith -- so they keep
guarding behaviour after the monolithic strategy classes are deleted
and replaced by ``Composite(selector, op)``.

The LLM truncation case pins the single point where the selector/op
split could silently regress: entries dropped by the
``max_total_user_content_chars`` cap must NOT be deleted (they remain
in the backend for the next pass).
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.enums import MemoryCategory
from synthorg.memory.consolidation.config import LLMConsolidationConfig
from synthorg.memory.consolidation.density import ContentDensity
from synthorg.memory.consolidation.dual_mode_strategy import (
    DualModeConsolidationStrategy,
)
from synthorg.memory.consolidation.extractive import ExtractivePreserver
from synthorg.memory.consolidation.llm_strategy import LLMConsolidationStrategy
from synthorg.memory.consolidation.models import ArchivalMode
from synthorg.memory.consolidation.simple_strategy import (
    SimpleConsolidationStrategy,
)
from synthorg.memory.models import (
    MemoryEntry,
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
)
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import CompletionResponse, TokenUsage

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_AGENT = "agent-golden"


class _RecordingBackend:
    """Deterministic in-memory backend for golden assertions.

    ``store`` assigns sequential ids (``sum-1``, ``sum-2``, ...) and
    records the request; ``delete`` records the id and reports success;
    ``retrieve`` returns no entries (LLM trajectory context is empty
    and deterministic).
    """

    def __init__(self) -> None:
        self.stored: list[tuple[str, MemoryStoreRequest]] = []
        self.deleted: list[str] = []
        self._n = 0

    async def store(self, agent_id: str, request: MemoryStoreRequest) -> str:
        self._n += 1
        self.stored.append((agent_id, request))
        return f"sum-{self._n}"

    async def delete(self, agent_id: str, entry_id: str) -> bool:
        self.deleted.append(entry_id)
        return True

    async def retrieve(
        self, agent_id: str, query: MemoryQuery
    ) -> tuple[MemoryEntry, ...]:
        return ()


def _entry(
    entry_id: str,
    *,
    content: str,
    relevance: float,
    age_hours: int = 0,
    category: MemoryCategory = MemoryCategory.EPISODIC,
) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        agent_id=_AGENT,
        category=category,
        content=content,
        metadata=MemoryMetadata(),
        created_at=_NOW - timedelta(hours=age_hours),
        relevance_score=relevance,
    )


def _response(content: str) -> CompletionResponse:
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=10, output_tokens=5, cost=0.001),
        model="test-model",
    )


class _FixedProvider:
    """CompletionProvider stub returning a fixed synthesis response."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls = 0

    async def complete(
        self,
        messages: object,
        model: object,
        *,
        config: object = None,
    ) -> CompletionResponse:
        self.calls += 1
        return _response(self._content)


# ── Simple: Composite(HighestRelevanceSelector, ConcatenationOp) ──


async def test_simple_golden() -> None:
    backend = _RecordingBackend()
    # Five EPISODIC entries; best = highest relevance (m4 @ 0.4).
    entries = tuple(
        _entry(f"m{i}", content=f"Content for m{i}", relevance=0.1 * i)
        for i in range(5)
    )
    strategy = SimpleConsolidationStrategy(backend=backend, group_threshold=3)  # type: ignore[arg-type]

    result = await strategy.consolidate(entries, agent_id=_AGENT)

    # m4 kept (max relevance); m0..m3 removed.
    assert result.removed_ids == ("m0", "m1", "m2", "m3")
    assert result.summary_ids == ("sum-1",)
    assert result.mode_assignments == ()
    assert backend.deleted == ["m0", "m1", "m2", "m3"]

    agent_id, req = backend.stored[0]
    assert agent_id == _AGENT
    assert req.category is MemoryCategory.EPISODIC
    assert req.metadata.tags == ("consolidated",)
    # Pinned ConcatenationOp format (Simple._build_summary).
    expected = (
        "Consolidated episodic memories:\n"
        "- Content for m0\n"
        "- Content for m1\n"
        "- Content for m2\n"
        "- Content for m3"
    )
    assert req.content == expected


# ── DualMode extractive route: provider-free, deterministic ──


async def test_dual_mode_extractive_golden() -> None:
    backend = _RecordingBackend()
    extractor = ExtractivePreserver()

    class _AllDenseClassifier:
        """Deterministic stub: every entry is DENSE -> EXTRACTIVE route.

        The real ``DensityClassifier`` has its own tests; this golden
        pins the dual-mode result shape + ``mode_assignments`` + tags
        + extractive content, provider-free.
        """

        def classify_batch(
            self, entries: tuple[MemoryEntry, ...]
        ) -> tuple[tuple[MemoryEntry, ContentDensity], ...]:
            return tuple((e, ContentDensity.DENSE) for e in entries)

    class _UnusedSummarizer:
        async def summarize(self, content: str, *, agent_id: str) -> str:
            msg = "abstractive path must not run for dense content"
            raise AssertionError(msg)

    entries = tuple(
        _entry(f"d{i}", content=f"id=ABC-{i} ref=DEF-{i} key: value", relevance=0.1 * i)
        for i in range(4)
    )
    strategy = DualModeConsolidationStrategy(
        backend=backend,  # type: ignore[arg-type]
        classifier=_AllDenseClassifier(),  # type: ignore[arg-type]
        extractor=extractor,
        summarizer=_UnusedSummarizer(),  # type: ignore[arg-type]
        group_threshold=3,
    )

    result = await strategy.consolidate(entries, agent_id=_AGENT)

    # d3 kept (max relevance); d0..d2 removed.
    assert result.removed_ids == ("d0", "d1", "d2")
    assert result.summary_ids == ("sum-1",)
    assert tuple(a.original_id for a in result.mode_assignments) == (
        "d0",
        "d1",
        "d2",
    )
    assert all(a.mode is ArchivalMode.EXTRACTIVE for a in result.mode_assignments)

    _agent, req = backend.stored[0]
    assert req.metadata.tags == ("consolidated", "mode:extractive")
    # Content == the refactor-stable ExtractivePreserver output joined
    # with the dual-mode separator, over the removed (non-kept) set.
    to_remove = (entries[0], entries[1], entries[2])
    expected = "\n---\n".join(extractor.extract(e.content) for e in to_remove)
    assert req.content == expected


# ── LLM: Composite(HighestRelevanceSelector, LLMSynthesisOp) ──


async def test_llm_synthesis_golden() -> None:
    backend = _RecordingBackend()
    provider = _FixedProvider("SYNTHESIZED")
    config = LLMConsolidationConfig(
        group_threshold=3,
        include_distillation_context=False,
    )
    strategy = LLMConsolidationStrategy(
        backend=backend,  # type: ignore[arg-type]
        provider=provider,  # type: ignore[arg-type]
        model="test-model",
        config=config,
    )
    entries = tuple(
        _entry(f"l{i}", content=f"Content for l{i}", relevance=0.1 * i)
        for i in range(4)
    )

    result = await strategy.consolidate(entries, agent_id=_AGENT)

    # l3 kept; l0..l2 synthesized + removed.
    assert result.removed_ids == ("l0", "l1", "l2")
    assert result.summary_ids == ("sum-1",)
    assert provider.calls == 1
    _agent, req = backend.stored[0]
    assert req.content == "SYNTHESIZED"
    assert req.metadata.tags == ("consolidated", "llm-synthesized")


async def test_llm_truncation_keeps_dropped_entries() -> None:
    """Entries dropped by the prompt cap are NOT deleted.

    The single point where the selector/op split could silently
    regress: ``represented`` (not the selector's full to-remove set)
    drives deletion.
    """
    backend = _RecordingBackend()
    provider = _FixedProvider("SYNTH")
    # Each wrapped entry is well over 200 chars; a 300-char total cap
    # admits only the first removed entry into the prompt.
    config = LLMConsolidationConfig(
        group_threshold=3,
        include_distillation_context=False,
        max_entry_input_chars=1000,
        max_total_user_content_chars=1000,
        fallback_truncate_length=50,
    )
    big = "X" * 900
    entries = tuple(
        _entry(f"t{i}", content=f"{big}-{i}", relevance=0.1 * i) for i in range(4)
    )
    strategy = LLMConsolidationStrategy(
        backend=backend,  # type: ignore[arg-type]
        provider=provider,  # type: ignore[arg-type]
        model="test-model",
        config=config,
    )

    result = await strategy.consolidate(entries, agent_id=_AGENT)

    # t3 is kept (max relevance). Of t0,t1,t2 only the entries that
    # actually fit the 1000-char prompt cap are summarised + deleted;
    # the rest stay in the backend. The invariant: every removed id is
    # one that was represented, and at least one over-cap entry is NOT
    # deleted.
    assert set(result.removed_ids).issubset({"t0", "t1", "t2"})
    assert result.removed_ids == tuple(backend.deleted)
    assert len(result.removed_ids) < 3
    assert "t3" not in result.removed_ids
