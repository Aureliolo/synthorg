"""Unit tests for :mod:`synthorg.project_brain.mutation`."""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.project_brain.errors import BrainEntryValidationError
from synthorg.project_brain.models import (
    BrainEntry,
    BrainEntryKind,
    BrainEntryStatus,
    DecisionPayload,
    OpenQuestionPayload,
)
from synthorg.project_brain.mutation import apply_overrides, build_entry

pytestmark = pytest.mark.unit

_PROJECT = NotBlankStr("proj-1")


def _now() -> datetime:
    return datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)


def _later() -> datetime:
    return datetime(2026, 5, 30, 13, 0, 0, tzinfo=UTC)


def test_build_entry_derives_kind_from_payload() -> None:
    entry = build_entry(
        now=_now(),
        project_id=_PROJECT,
        title=NotBlankStr("Q"),
        rationale=NotBlankStr("why"),
        status=BrainEntryStatus.OPEN,
        author=NotBlankStr("alice"),
        payload=OpenQuestionPayload(),
        related_task_ids=(),
        related_entry_ids=(),
        supersedes_entry_id=None,
        tags=(),
        confidence=None,
        citations=(),
    )
    assert entry.entry_kind is BrainEntryKind.OPEN_QUESTION
    assert entry.recorded_at == _now()


def test_build_entry_rejects_illegal_status_for_kind() -> None:
    with pytest.raises(BrainEntryValidationError):
        build_entry(
            now=_now(),
            project_id=_PROJECT,
            title=NotBlankStr("Decision"),
            rationale=NotBlankStr("why"),
            status=BrainEntryStatus.MITIGATED,  # risk-only status
            author=NotBlankStr("alice"),
            payload=DecisionPayload(decision_outcome="x"),
            related_task_ids=(),
            related_entry_ids=(),
            supersedes_entry_id=None,
            tags=(),
            confidence=None,
            citations=(),
        )


def _current() -> BrainEntry:
    return BrainEntry(
        project_id=_PROJECT,
        revision=4,
        entry_kind=BrainEntryKind.DECISION,
        title="Adopt X",
        rationale="why",
        status=BrainEntryStatus.ACCEPTED,
        author=NotBlankStr("alice"),
        recorded_at=_now(),
        confidence=0.9,
        payload=DecisionPayload(decision_outcome="X"),
    )


def test_apply_overrides_restamps_and_resets_revision() -> None:
    revised = apply_overrides(
        _current(),
        now=_later(),
        author=NotBlankStr("bob"),
        status=BrainEntryStatus.SUPERSEDED,
    )
    assert revised.status is BrainEntryStatus.SUPERSEDED
    assert revised.author == NotBlankStr("bob")
    assert revised.recorded_at == _later()
    assert revised.revision == 1  # placeholder; repo assigns the real value


def test_apply_overrides_inherits_unset_fields() -> None:
    revised = apply_overrides(_current(), now=_later(), author=NotBlankStr("bob"))
    assert revised.title == "Adopt X"
    assert revised.confidence == pytest.approx(0.9)
    assert revised.entry_kind is BrainEntryKind.DECISION


def test_apply_overrides_updates_confidence() -> None:
    """An explicit confidence replaces the inherited value on revision."""
    revised = apply_overrides(
        _current(),  # current confidence is 0.9
        now=_later(),
        author=NotBlankStr("bob"),
        confidence=0.5,
    )
    assert revised.confidence == pytest.approx(0.5)


def test_apply_overrides_rejects_illegal_status_transition() -> None:
    with pytest.raises(BrainEntryValidationError):
        apply_overrides(
            _current(),
            now=_later(),
            author=NotBlankStr("bob"),
            status=BrainEntryStatus.CLEARED,  # blocker-only status
        )


def test_apply_overrides_clears_tuple_field_with_empty_tuple() -> None:
    """An explicit empty tuple clears a tuple field; ``None`` keeps it."""
    current = _current().model_copy(
        update={"tags": (NotBlankStr("infra"), NotBlankStr("urgent"))}
    )
    cleared = apply_overrides(current, now=_later(), author=NotBlankStr("bob"), tags=())
    assert cleared.tags == ()
    kept = apply_overrides(current, now=_later(), author=NotBlankStr("bob"))
    assert kept.tags == (NotBlankStr("infra"), NotBlankStr("urgent"))
