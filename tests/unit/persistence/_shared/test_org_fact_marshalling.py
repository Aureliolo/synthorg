"""Tests for the shared org-fact MVCC marshalling helpers."""

from datetime import UTC, datetime

import pytest

from synthorg.core.enums import AutonomyLevel, OrgFactCategory, SeniorityLevel
from synthorg.core.types import NotBlankStr
from synthorg.memory.org.errors import OrgMemoryQueryError
from synthorg.memory.org.models import OrgFact, OrgFactAuthor
from synthorg.persistence._shared.org_fact_marshalling import (
    row_to_operation_log_entry,
    row_to_snapshot,
    snapshot_row_to_org_fact,
    tags_from_json,
    tags_to_json,
)

_NOW = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)


def _fact() -> OrgFact:
    return OrgFact(
        id=NotBlankStr("fact-1"),
        content=NotBlankStr("The deploy cadence is weekly."),
        category=OrgFactCategory.CONVENTION,
        # Stored sorted, so use sorted order for a clean round-trip identity.
        tags=(NotBlankStr("deploy"), NotBlankStr("ops")),
        author=OrgFactAuthor(
            agent_id=NotBlankStr("agent-1"),
            seniority=SeniorityLevel.SENIOR,
            autonomy_level=AutonomyLevel.SEMI,
            is_human=False,
        ),
        created_at=_NOW,
    )


def _snapshot_row(fact: OrgFact, *, native_ts: bool = False) -> dict[str, object]:
    return {
        "fact_id": fact.id,
        "content": fact.content,
        "category": fact.category.value,
        "tags": tags_to_json(fact.tags),
        "author_agent_id": fact.author.agent_id,
        "author_seniority": (
            fact.author.seniority.value if fact.author.seniority else None
        ),
        "author_is_human": int(fact.author.is_human),
        "author_autonomy_level": (
            fact.author.autonomy_level.value if fact.author.autonomy_level else None
        ),
        "created_at": _NOW if native_ts else _NOW.isoformat(),
        "retracted_at": None,
        "version": 1,
    }


@pytest.mark.unit
class TestTagsRoundTrip:
    """``tags_to_json`` / ``tags_from_json`` round-trip and validate."""

    def test_sorted_json_round_trip(self) -> None:
        tags = (NotBlankStr("z"), NotBlankStr("a"))
        result = tags_from_json(tags_to_json(tags))
        assert result == (NotBlankStr("a"), NotBlankStr("z"))

    def test_native_list_postgres(self) -> None:
        assert tags_from_json(["a", "b"]) == (NotBlankStr("a"), NotBlankStr("b"))

    def test_non_array_raises(self) -> None:
        with pytest.raises(OrgMemoryQueryError):
            tags_from_json('{"not": "array"}')

    def test_blank_tag_raises(self) -> None:
        with pytest.raises(OrgMemoryQueryError):
            tags_from_json('["ok", "  "]')


@pytest.mark.unit
class TestSnapshotRowToOrgFact:
    """``snapshot_row_to_org_fact`` reconstructs from either backend shape."""

    def test_sqlite_round_trip(self) -> None:
        fact = _fact()
        assert snapshot_row_to_org_fact(_snapshot_row(fact)) == fact

    def test_postgres_native_timestamp(self) -> None:
        fact = _fact()
        result = snapshot_row_to_org_fact(_snapshot_row(fact, native_ts=True))
        assert result == fact

    def test_corrupt_category_raises(self) -> None:
        row = _snapshot_row(_fact())
        row["category"] = "not-a-category"
        with pytest.raises(OrgMemoryQueryError):
            snapshot_row_to_org_fact(row)


@pytest.mark.unit
class TestOperationLogMarshalling:
    """``row_to_operation_log_entry`` / ``row_to_snapshot`` reconstruct rows."""

    def test_operation_log_entry(self) -> None:
        row: dict[str, object] = {
            "operation_id": "op-1",
            "fact_id": "fact-1",
            "operation_type": "PUBLISH",
            "content": "body",
            "category": OrgFactCategory.ADR.value,
            "tags": tags_to_json((NotBlankStr("x"),)),
            "author_agent_id": "agent-1",
            "author_seniority": SeniorityLevel.LEAD.value,
            "author_is_human": 0,
            "author_autonomy_level": AutonomyLevel.FULL.value,
            "timestamp": _NOW.isoformat(),
            "version": 3,
        }
        entry = row_to_operation_log_entry(row)
        assert entry.operation_id == "op-1"
        assert entry.operation_type == "PUBLISH"
        assert entry.version == 3
        assert entry.tags == (NotBlankStr("x"),)

    def test_snapshot_publish(self) -> None:
        row: dict[str, object] = {
            "fact_id": "fact-1",
            "operation_type": "PUBLISH",
            "content": "body",
            "category": OrgFactCategory.PROCEDURE.value,
            "tags": tags_to_json((NotBlankStr("x"),)),
            "version": 1,
            "timestamp": _NOW.isoformat(),
            "created_at": _NOW.isoformat(),
        }
        snap = row_to_snapshot(row)
        assert snap.fact_id == "fact-1"
        assert snap.retracted_at is None
        assert snap.created_at == _NOW

    def test_snapshot_retract_sets_retracted_at(self) -> None:
        row: dict[str, object] = {
            "fact_id": "fact-1",
            "operation_type": "RETRACT",
            "content": "body",
            "category": OrgFactCategory.PROCEDURE.value,
            "tags": tags_to_json((NotBlankStr("x"),)),
            "version": 2,
            "timestamp": _NOW.isoformat(),
            "created_at": None,
        }
        snap = row_to_snapshot(row)
        assert snap.retracted_at == _NOW
        # created_at falls back to the operation timestamp when absent.
        assert snap.created_at == _NOW

    def test_operation_log_corrupt_category_raises(self) -> None:
        row: dict[str, object] = {
            "operation_id": "op-1",
            "fact_id": "fact-1",
            "operation_type": "PUBLISH",
            "content": "body",
            "category": "not-a-category",
            "tags": tags_to_json((NotBlankStr("x"),)),
            "author_agent_id": "agent-1",
            "author_seniority": SeniorityLevel.LEAD.value,
            "author_is_human": 0,
            "author_autonomy_level": AutonomyLevel.FULL.value,
            "timestamp": _NOW.isoformat(),
            "version": 3,
        }
        with pytest.raises(OrgMemoryQueryError):
            row_to_operation_log_entry(row)

    def test_snapshot_corrupt_category_raises(self) -> None:
        row: dict[str, object] = {
            "fact_id": "fact-1",
            "operation_type": "PUBLISH",
            "content": "body",
            "category": "not-a-category",
            "tags": tags_to_json((NotBlankStr("x"),)),
            "version": 1,
            "timestamp": _NOW.isoformat(),
            "created_at": _NOW.isoformat(),
        }
        with pytest.raises(OrgMemoryQueryError):
            row_to_snapshot(row)
