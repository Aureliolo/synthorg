"""Unit tests for :mod:`synthorg.project_brain.query` helpers."""

from datetime import UTC, datetime

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.models import MemoryEntry, MemoryMetadata
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
)
from synthorg.project_brain.query import (
    _parse_history_line,
    build_filter_spec,
    entry_to_search_hit,
    entry_to_summary,
)

pytestmark = pytest.mark.unit

_PROJECT = NotBlankStr("proj-1")


def _ts() -> datetime:
    return datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)


def _entry() -> BrainEntry:
    return BrainEntry(
        project_id=_PROJECT,
        revision=3,
        entry_kind=BrainEntryKind.DECISION,
        title="Adopt append-only storage",
        rationale="History matters.",
        status=BrainEntryStatus.ACCEPTED,
        author=NotBlankStr("agent_alice"),
        recorded_at=_ts(),
        tags=(NotBlankStr("storage"),),
        payload=DecisionPayload(decision_outcome="append-only"),
    )


def test_entry_to_summary_projects_key_fields() -> None:
    summary = entry_to_summary(_entry())
    assert summary.entry_kind is BrainEntryKind.DECISION
    assert summary.revision == 3
    assert summary.status is BrainEntryStatus.ACCEPTED
    assert summary.title == "Adopt append-only storage"
    assert summary.tags == (NotBlankStr("storage"),)


def _memory_entry(tags: tuple[str, ...]) -> MemoryEntry:
    return MemoryEntry(
        id=NotBlankStr("m1"),
        agent_id=NotBlankStr("_system:brain"),
        category=MemoryCategory.PROJECT_BRAIN,
        content=NotBlankStr("[decision/accepted] Adopt append-only storage"),
        metadata=MemoryMetadata(tags=tuple(NotBlankStr(t) for t in tags)),
        created_at=_ts(),
        relevance_score=0.87,
    )


def test_entry_to_search_hit_reconstructs_from_tags() -> None:
    entry = _memory_entry(
        (
            f"{BRAIN_PROJECT_TAG_PREFIX}{_PROJECT}",
            f"{BRAIN_ENTRY_TAG_PREFIX}e-42",
            f"{BRAIN_KIND_TAG_PREFIX}decision",
        )
    )
    hit = entry_to_search_hit(entry)
    assert hit is not None
    assert hit.project_id == _PROJECT
    assert hit.entry_id == NotBlankStr("e-42")
    assert hit.entry_kind is BrainEntryKind.DECISION
    assert hit.relevance_score == pytest.approx(0.87)


def test_entry_to_search_hit_none_without_required_tags() -> None:
    assert (
        entry_to_search_hit(_memory_entry((f"{BRAIN_PROJECT_TAG_PREFIX}{_PROJECT}",)))
        is None
    )


def test_entry_to_search_hit_none_with_unknown_kind() -> None:
    """A ``brain_kind:`` tag that is not a member yields no hit."""
    entry = _memory_entry(
        (
            f"{BRAIN_PROJECT_TAG_PREFIX}{_PROJECT}",
            f"{BRAIN_ENTRY_TAG_PREFIX}e-42",
            f"{BRAIN_KIND_TAG_PREFIX}not-a-real-kind",
        )
    )
    assert entry_to_search_hit(entry) is None


def test_build_filter_spec_passes_dimensions() -> None:
    spec = build_filter_spec(
        project_id=_PROJECT,
        entry_kind=BrainEntryKind.BLOCKER,
        status=BrainEntryStatus.BLOCKED,
        tag=NotBlankStr("infra"),
    )
    assert spec.project_id == _PROJECT
    assert spec.entry_kind is BrainEntryKind.BLOCKER
    assert spec.status is BrainEntryStatus.BLOCKED
    assert spec.tag == NotBlankStr("infra")


def test_parse_history_line_extracts_revision_from_subject() -> None:
    line = "abc123\t2026-05-30T12:00:00+00:00\tbrain(decision): e-42 r7"
    version = _parse_history_line(line)
    assert version is not None
    assert version.commit_hash == NotBlankStr("abc123")
    assert version.revision == 7
    assert version.committed_at.tzinfo is not None


@pytest.mark.parametrize(
    "line",
    [
        pytest.param("abc\t2026-05-30T12:00:00\tbrain: e r1", id="naive-timestamp"),
        pytest.param(
            "abc\t2026-05-30T12:00:00+00:00\tno revision here", id="missing-revision"
        ),
        pytest.param("abc\tonly-two-fields", id="wrong-field-count"),
        pytest.param("abc\tnot-a-date\tbrain: e r1", id="invalid-timestamp"),
    ],
)
def test_parse_history_line_rejects_malformed(line: str) -> None:
    assert _parse_history_line(line) is None
