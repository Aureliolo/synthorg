"""Unit tests for DecisionRecord model."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.engine.decisions import DecisionOutcome, DecisionRecord


def _make_record(**overrides: object) -> DecisionRecord:
    """Build a DecisionRecord with sensible defaults."""
    defaults: dict[str, object] = {
        "id": "decision-001",
        "task_id": "task-1",
        "executing_agent_id": "alice",
        "reviewer_agent_id": "bob",
        "decision": DecisionOutcome.APPROVED,
        "recorded_at": datetime(2026, 4, 4, 12, 0, tzinfo=UTC),
        "version": 1,
        "metadata": {},
    }
    defaults.update(overrides)
    return DecisionRecord(**defaults)  # type: ignore[arg-type]


@pytest.mark.unit
class TestDecisionRecordConstruction:
    """Tests for DecisionRecord construction and validation."""

    def test_minimal_construction(self) -> None:
        """All required fields produce a valid record."""
        record = _make_record()
        assert record.id == "decision-001"
        assert record.task_id == "task-1"
        assert record.executing_agent_id == "alice"
        assert record.reviewer_agent_id == "bob"
        assert record.decision is DecisionOutcome.APPROVED
        assert record.version == 1
        assert record.metadata == {}

    def test_defaults(self) -> None:
        """Optional fields have expected defaults."""
        record = _make_record()
        assert record.approval_id is None
        assert record.reason is None
        assert record.criteria_snapshot == ()

    def test_frozen(self) -> None:
        """Attempting to mutate raises ValidationError."""
        record = _make_record()
        with pytest.raises(ValidationError):
            record.decision = DecisionOutcome.REJECTED  # type: ignore[misc]

    def test_metadata_deep_copied_and_frozen(self) -> None:
        """Source mutation does not leak into the record; nested dicts frozen.

        ``record.metadata`` is a ``MappingProxyType`` (read-only view)
        and nested dicts are recursively frozen to ``MappingProxyType``
        so ``record.metadata["nested"]["key"] = ...`` also fails.
        """
        from collections.abc import Mapping
        from types import MappingProxyType

        nested: dict[str, int] = {"a": 1}
        original: dict[str, object] = {"key": "value", "nested": nested}
        record = _make_record(metadata=original)
        # Mutating the source dict must not leak into the record.
        original["key"] = "mutated"
        nested["a"] = 999
        assert record.metadata["key"] == "value"
        # Top-level is a read-only proxy.
        assert isinstance(record.metadata, MappingProxyType)
        # Nested dict is recursively frozen.
        nested_copy = record.metadata["nested"]
        assert isinstance(nested_copy, Mapping)
        assert isinstance(nested_copy, MappingProxyType)
        assert nested_copy["a"] == 1

    def test_metadata_defaults_to_empty_dict(self) -> None:
        """metadata has a default_factory -- omitting it yields {}."""
        record = DecisionRecord(
            id="decision-001",
            task_id="task-1",
            executing_agent_id="alice",
            reviewer_agent_id="bob",
            decision=DecisionOutcome.APPROVED,
            recorded_at=datetime(2026, 4, 4, 12, 0, tzinfo=UTC),
            version=1,
        )
        assert record.metadata == {}

    def test_version_must_be_at_least_one(self) -> None:
        """version < 1 raises ValidationError."""
        with pytest.raises(ValidationError):
            _make_record(version=0)

    def test_empty_id_rejected(self) -> None:
        """Blank id raises ValidationError."""
        with pytest.raises(ValidationError):
            _make_record(id="")

    def test_empty_task_id_rejected(self) -> None:
        """Blank task_id raises ValidationError."""
        with pytest.raises(ValidationError):
            _make_record(task_id="")

    def test_empty_executing_agent_id_rejected(self) -> None:
        """Blank executing_agent_id raises ValidationError."""
        with pytest.raises(ValidationError):
            _make_record(executing_agent_id="")

    def test_empty_reviewer_agent_id_rejected(self) -> None:
        """Blank reviewer_agent_id raises ValidationError."""
        with pytest.raises(ValidationError):
            _make_record(reviewer_agent_id="")

    def test_all_fields_populated(self) -> None:
        """All optional fields can be set."""
        record = _make_record(
            approval_id="approval-42",
            reason="Code meets quality standards",
            criteria_snapshot=("JWT login", "Tests pass"),
            metadata={"context": "sprint-5"},
        )
        assert record.approval_id == "approval-42"
        assert record.reason == "Code meets quality standards"
        assert record.criteria_snapshot == ("JWT login", "Tests pass")
        assert record.metadata == {"context": "sprint-5"}

    def test_decision_rejected(self) -> None:
        """REJECTED decision is valid."""
        record = _make_record(decision=DecisionOutcome.REJECTED)
        assert record.decision is DecisionOutcome.REJECTED

    def test_self_review_rejected(self) -> None:
        """executing_agent_id and reviewer_agent_id must differ.

        No-self-review is a type-level invariant; the model validator
        rejects construction when the two identifiers match.
        """
        with pytest.raises(
            ValidationError,
            match="executing_agent_id and reviewer_agent_id must differ",
        ):
            _make_record(executing_agent_id="alice", reviewer_agent_id="alice")

    def test_self_review_error_contains_offending_id(self) -> None:
        """The validator error names the offending identifier."""
        with pytest.raises(ValidationError) as exc_info:
            _make_record(executing_agent_id="alice", reviewer_agent_id="alice")
        assert "alice" in str(exc_info.value)

    def test_criteria_snapshot_rejects_duplicates(self) -> None:
        """Duplicate acceptance criteria are rejected (set semantics)."""
        with pytest.raises(ValidationError, match="Duplicate entries"):
            _make_record(criteria_snapshot=("Login works", "Login works"))
