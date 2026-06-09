"""Byte-identical golden guard for the RFC#10 consolidation axis split.

Pins the exact ``ConsolidationResult`` and stored-summary content for
the Simple / DualMode / LLM composites on fixed inputs, against
EXPLICIT expected values (refactor-stable components like
``ExtractivePreserver`` produce them deterministically). They guard the
composites against silent output regressions.

The LLM truncation case pins the single point where the selector/op
split could silently regress: entries dropped by the
``max_total_user_content_chars`` cap must NOT be deleted (they remain
in the backend for the next pass).
"""

from datetime import UTC, datetime, timedelta
from typing import override

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.consolidation.abstractive import AbstractiveSummarizer
from synthorg.memory.consolidation.composite import (
    CompositeConsolidationStrategy,
)
from synthorg.memory.consolidation.config import LLMConsolidationConfig
from synthorg.memory.consolidation.density import ContentDensity, DensityClassifier
from synthorg.memory.consolidation.extractive import ExtractivePreserver
from synthorg.memory.consolidation.llm_op import LLMSynthesisOp
from synthorg.memory.consolidation.models import ArchivalMode
from synthorg.memory.consolidation.ops import (
    ConcatenationOp,
    DensityRoutingOp,
)
from synthorg.memory.consolidation.selectors import HighestRelevanceSelector
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

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def health_check(self) -> bool:
        return True

    @property
    def is_connected(self) -> bool:
        return True

    @property
    def backend_name(self) -> NotBlankStr:
        return NotBlankStr("recording")

    async def store(
        self, agent_id: NotBlankStr, request: MemoryStoreRequest
    ) -> NotBlankStr:
        self._n += 1
        self.stored.append((agent_id, request))
        return NotBlankStr(f"sum-{self._n}")

    async def retrieve(
        self, agent_id: NotBlankStr, query: MemoryQuery
    ) -> tuple[MemoryEntry, ...]:
        return ()

    async def get(
        self, agent_id: NotBlankStr, memory_id: NotBlankStr
    ) -> MemoryEntry | None:
        return None

    async def delete(self, agent_id: NotBlankStr, memory_id: NotBlankStr) -> bool:
        self.deleted.append(memory_id)
        return True

    async def count(
        self, agent_id: NotBlankStr, *, category: MemoryCategory | None = None
    ) -> int:
        return len(self.stored)


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


# ── Simple = Composite(HighestRelevanceSelector, ConcatenationOp) ──


async def test_simple_golden() -> None:
    backend = _RecordingBackend()
    entries = tuple(
        _entry(f"m{i}", content=f"Content for m{i}", relevance=0.1 * i)
        for i in range(5)
    )
    strategy = CompositeConsolidationStrategy(
        selector=HighestRelevanceSelector(group_threshold=3),
        op=ConcatenationOp(backend=backend),
    )

    result = await strategy.consolidate(entries, agent_id=_AGENT)

    assert result.removed_ids == ("m0", "m1", "m2", "m3")
    assert result.summary_ids == ("sum-1",)
    assert result.mode_assignments == ()
    assert backend.deleted == ["m0", "m1", "m2", "m3"]
    _agent, req = backend.stored[0]
    assert req.metadata.tags == ("consolidated",)
    expected = (
        "Consolidated episodic memories:\n"
        "- Content for m0\n"
        "- Content for m1\n"
        "- Content for m2\n"
        "- Content for m3"
    )
    assert req.content == expected


# ── DualMode = Composite(selector, DensityRoutingOp) ──


async def test_dual_mode_golden() -> None:
    backend = _RecordingBackend()
    extractor = ExtractivePreserver()

    class _AllDenseClassifier(DensityClassifier):
        @override
        def classify_batch(
            self, entries: tuple[MemoryEntry, ...]
        ) -> tuple[tuple[MemoryEntry, ContentDensity], ...]:
            return tuple((e, ContentDensity.DENSE) for e in entries)

    class _UnusedSummarizer(AbstractiveSummarizer):
        def __init__(self) -> None:
            """Skip the real provider/model wiring; this double never runs."""

        @override
        async def summarize(
            self, content: str, *, agent_id: NotBlankStr | None = None
        ) -> str:
            msg = "abstractive path must not run for dense content"
            raise AssertionError(msg)

    entries = tuple(
        _entry(f"d{i}", content=f"id=ABC-{i} ref=DEF-{i} key: value", relevance=0.1 * i)
        for i in range(4)
    )
    strategy = CompositeConsolidationStrategy(
        selector=HighestRelevanceSelector(group_threshold=3),
        op=DensityRoutingOp(
            backend=backend,
            classifier=_AllDenseClassifier(),
            extractor=extractor,
            summarizer=_UnusedSummarizer(),
        ),
    )

    result = await strategy.consolidate(entries, agent_id=_AGENT)

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
    to_remove = (entries[0], entries[1], entries[2])
    expected = "\n---\n".join(extractor.extract(e.content) for e in to_remove)
    assert req.content == expected


# ── LLM = Composite(selector, LLMSynthesisOp, parallel=True) ──


async def test_llm_golden() -> None:
    backend = _RecordingBackend()
    provider = _FixedProvider("SYNTHESIZED")
    config = LLMConsolidationConfig(
        group_threshold=3,
        include_distillation_context=False,
    )
    strategy = CompositeConsolidationStrategy(
        selector=HighestRelevanceSelector(group_threshold=3),
        op=LLMSynthesisOp(
            backend=backend,
            provider=provider,
            model="test-model",
            config=config,
        ),
        parallel=True,
    )
    entries = tuple(
        _entry(f"l{i}", content=f"Content for l{i}", relevance=0.1 * i)
        for i in range(4)
    )

    result = await strategy.consolidate(entries, agent_id=_AGENT)

    assert result.removed_ids == ("l0", "l1", "l2")
    assert result.summary_ids == ("sum-1",)
    assert provider.calls == 1
    _agent, req = backend.stored[0]
    assert req.content == "SYNTHESIZED"
    assert req.metadata.tags == ("consolidated", "llm-synthesized")


async def test_llm_truncation_keeps_dropped_entries() -> None:
    """Entries dropped by the prompt cap are NOT deleted.

    The single point where the selector/op split could silently
    regress: ``LLMSynthesisOp`` deletes only the prompt-cap survivors,
    so over-cap entries stay for the next pass.
    """
    backend = _RecordingBackend()
    provider = _FixedProvider("SYNTH")
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
    strategy = CompositeConsolidationStrategy(
        selector=HighestRelevanceSelector(group_threshold=3),
        op=LLMSynthesisOp(
            backend=backend,
            provider=provider,
            model="test-model",
            config=config,
        ),
        parallel=True,
    )

    result = await strategy.consolidate(entries, agent_id=_AGENT)

    assert set(result.removed_ids).issubset({"t0", "t1", "t2"})
    assert result.removed_ids == tuple(backend.deleted)
    assert len(result.removed_ids) < 3
    assert "t3" not in result.removed_ids
