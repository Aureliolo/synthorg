"""Unit tests for :mod:`synthorg.project_brain.serializer`.

The serializer is the canonical on-disk encoder for brain entries. Its two
load-bearing guarantees are determinism (same input, same bytes) and round-trip
(``deserialize_entry(serialize_entry(entry)) == entry``). The error paths raise
:class:`BrainEntryValidationError` rather than letting internal exceptions leak.
"""

from datetime import UTC, datetime

import pytest

from synthorg.project_brain.errors import BrainEntryValidationError
from synthorg.project_brain.models import (
    BrainEntry,
    BrainEntryKind,
    BrainEntryStatus,
    Citation,
    CitationKind,
    DecisionPayload,
)
from synthorg.project_brain.serializer import deserialize_entry, serialize_entry

pytestmark = pytest.mark.unit


def _ts() -> datetime:
    return datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)


def _entry() -> BrainEntry:
    return BrainEntry(
        entry_id="entry-1",
        project_id="proj-1",
        revision=2,
        entry_kind=BrainEntryKind.DECISION,
        title="Use append-only storage",
        rationale="Full why/when history is required.",
        status=BrainEntryStatus.ACCEPTED,
        author="agent_alice",
        recorded_at=_ts(),
        related_task_ids=("task-1", "task-2"),
        tags=("storage", "schema"),
        confidence=0.8,
        citations=(Citation(source_ref="task-1", source_kind=CitationKind.TASK),),
        payload=DecisionPayload(
            decision_outcome="append-only",
            alternatives=("mutable current row",),
        ),
    )


class TestRoundTrip:
    def test_round_trip_preserves_entry(self) -> None:
        entry = _entry()
        restored = deserialize_entry(serialize_entry(entry))
        assert restored == entry

    def test_round_trip_preserves_payload_type(self) -> None:
        restored = deserialize_entry(serialize_entry(_entry()))
        assert isinstance(restored.payload, DecisionPayload)


class TestDeterminism:
    def test_repeated_serialize_yields_identical_bytes(self) -> None:
        entry = _entry()
        assert serialize_entry(entry) == serialize_entry(entry)

    def test_trailing_newline(self) -> None:
        assert serialize_entry(_entry()).endswith(b"\n")

    def test_keys_sorted(self) -> None:
        text = serialize_entry(_entry()).decode("utf-8")
        author_idx = text.index('"author"')
        title_idx = text.index('"title"')
        assert author_idx < title_idx


class TestErrorPaths:
    def test_invalid_utf8_raises(self) -> None:
        with pytest.raises(BrainEntryValidationError):
            deserialize_entry(b"\xff\xfe\x00")

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(BrainEntryValidationError):
            deserialize_entry(b"not json at all")

    def test_schema_mismatch_raises(self) -> None:
        with pytest.raises(BrainEntryValidationError):
            deserialize_entry(b'{"entry_id": "x"}')
