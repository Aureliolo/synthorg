"""Unit tests for :class:`synthorg.project_brain.chunker.BrainChunker`."""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.project_brain.chunker import BrainChunker
from synthorg.project_brain.constants import (
    BRAIN_ENTRY_TAG_PREFIX,
    BRAIN_KIND_TAG_PREFIX,
    BRAIN_PROJECT_TAG_PREFIX,
)
from synthorg.project_brain.models import (
    BrainEntry,
    BrainEntryKind,
    BrainEntryStatus,
    DecisionPayload,
    RiskLevel,
    RiskPayload,
)

pytestmark = pytest.mark.unit

_PROJECT = NotBlankStr("proj-1")


def _ts() -> datetime:
    return datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)


def _decision(**overrides: object) -> BrainEntry:
    fields: dict[str, object] = {
        "project_id": _PROJECT,
        "entry_kind": BrainEntryKind.DECISION,
        "title": "Adopt append-only storage",
        "rationale": "We need a full history of why each choice was made.",
        "status": BrainEntryStatus.ACCEPTED,
        "author": NotBlankStr("agent_alice"),
        "recorded_at": _ts(),
        "payload": DecisionPayload(
            decision_outcome="append-only",
            alternatives=(NotBlankStr("in-place update"),),
        ),
    }
    fields.update(overrides)
    return BrainEntry(**fields)  # type: ignore[arg-type]


def test_chunk_is_deterministic() -> None:
    """Same entry + project yields identical chunks across runs."""
    chunker = BrainChunker()
    entry = _decision()
    first = chunker.chunk(project_id=_PROJECT, entry=entry)
    second = chunker.chunk(project_id=_PROJECT, entry=entry)
    assert [c.text for c in first] == [c.text for c in second]
    assert [c.chunk_index for c in first] == list(range(len(first)))


def test_chunk_carries_scoping_tags() -> None:
    """Every chunk carries project / entry / kind tags."""
    chunker = BrainChunker()
    entry = _decision()
    chunks = chunker.chunk(project_id=_PROJECT, entry=entry)
    assert chunks
    for chunk in chunks:
        assert f"{BRAIN_PROJECT_TAG_PREFIX}{_PROJECT}" in chunk.tags
        assert f"{BRAIN_ENTRY_TAG_PREFIX}{entry.entry_id}" in chunk.tags
        assert f"{BRAIN_KIND_TAG_PREFIX}{entry.entry_kind.value}" in chunk.tags


def test_header_segment_identifies_kind_status_and_title() -> None:
    """The first chunk is a self-describing header."""
    chunker = BrainChunker()
    entry = _decision()
    chunks = chunker.chunk(project_id=_PROJECT, entry=entry)
    assert chunks[0].text.startswith("[decision/accepted] Adopt append-only storage")


def test_payload_fields_are_embedded() -> None:
    """Decision outcome + alternatives reach the chunk text."""
    chunker = BrainChunker()
    joined = "\n".join(
        c.text for c in chunker.chunk(project_id=_PROJECT, entry=_decision())
    )
    assert "append-only" in joined
    assert "in-place update" in joined


def test_oversized_rationale_splits_into_multiple_chunks() -> None:
    """A rationale far past the max token budget yields more than one chunk."""
    chunker = BrainChunker(target_tokens=8, max_tokens=16)
    long_rationale = ". ".join(
        f"Sentence number {i} explains the choice" for i in range(60)
    )
    entry = _decision(rationale=long_rationale)
    chunks = chunker.chunk(project_id=_PROJECT, entry=entry)
    assert len(chunks) > 1


def test_risk_payload_renders_levels() -> None:
    """Risk likelihood/impact/mitigation render into the chunk text."""
    chunker = BrainChunker()
    entry = _decision(
        entry_kind=BrainEntryKind.RISK,
        status=BrainEntryStatus.ACTIVE,
        payload=RiskPayload(
            likelihood=RiskLevel.HIGH,
            impact=RiskLevel.MEDIUM,
            mitigation="Add a circuit breaker",
        ),
    )
    joined = "\n".join(c.text for c in chunker.chunk(project_id=_PROJECT, entry=entry))
    assert "high" in joined
    assert "Add a circuit breaker" in joined


def test_rejects_max_below_target() -> None:
    """The chunker rejects an inverted size budget."""
    with pytest.raises(ValueError, match="max_tokens"):
        BrainChunker(target_tokens=100, max_tokens=10)
