"""Tests for memory domain models."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from synthorg.core.enums import MemoryCategory
from synthorg.memory.models import (
    MemoryEntry,
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
)

pytestmark = pytest.mark.timeout(30)


# ── MemoryMetadata ────────────────────────────────────────────────


@pytest.mark.unit
class TestMemoryMetadata:
    def test_defaults(self) -> None:
        m = MemoryMetadata()
        assert m.source is None
        assert m.confidence == 1.0
        assert m.tags == ()

    def test_custom_values(self) -> None:
        m = MemoryMetadata(
            source="task-123",
            confidence=0.8,
            tags=("important", "reviewed"),
        )
        assert m.source == "task-123"
        assert m.confidence == 0.8
        assert len(m.tags) == 2

    def test_frozen(self) -> None:
        m = MemoryMetadata()
        with pytest.raises(ValidationError):
            m.confidence = 0.5  # type: ignore[misc]

    def test_confidence_lower_bound(self) -> None:
        m = MemoryMetadata(confidence=0.0)
        assert m.confidence == 0.0

    def test_confidence_upper_bound(self) -> None:
        m = MemoryMetadata(confidence=1.0)
        assert m.confidence == 1.0

    def test_confidence_below_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryMetadata(confidence=-0.1)

    def test_confidence_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryMetadata(confidence=1.1)

    def test_confidence_nan_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryMetadata(confidence=float("nan"))

    def test_empty_tag_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least 1 character"):
            MemoryMetadata(tags=("valid", ""))

    def test_whitespace_tag_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            MemoryMetadata(tags=("  ",))

    def test_empty_source_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least 1 character"):
            MemoryMetadata(source="")

    def test_whitespace_source_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            MemoryMetadata(source="   ")

    def test_duplicate_tags_deduplicated(self) -> None:
        m = MemoryMetadata(tags=("important", "reviewed", "important"))
        assert m.tags == ("important", "reviewed")

    def test_duplicate_tags_preserves_order(self) -> None:
        m = MemoryMetadata(tags=("b", "a", "b", "c", "a"))
        assert m.tags == ("b", "a", "c")


# ── MemoryStoreRequest ────────────────────────────────────────────


@pytest.mark.unit
class TestMemoryStoreRequest:
    def test_minimal(self) -> None:
        r = MemoryStoreRequest(
            category=MemoryCategory.EPISODIC,
            content="test memory",
        )
        assert r.category is MemoryCategory.EPISODIC
        assert r.content == "test memory"
        assert r.metadata == MemoryMetadata()
        assert r.expires_at is None

    def test_with_all_fields(self) -> None:
        expires = datetime(2027, 1, 1, tzinfo=UTC)
        r = MemoryStoreRequest(
            category=MemoryCategory.SEMANTIC,
            content="important fact",
            metadata=MemoryMetadata(
                source="conv-456",
                confidence=0.9,
                tags=("fact",),
            ),
            expires_at=expires,
        )
        assert r.expires_at == expires
        assert r.metadata.source == "conv-456"

    def test_frozen(self) -> None:
        r = MemoryStoreRequest(
            category=MemoryCategory.WORKING,
            content="test",
        )
        with pytest.raises(ValidationError):
            r.content = "changed"  # type: ignore[misc]

    def test_empty_content_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least 1 character"):
            MemoryStoreRequest(
                category=MemoryCategory.WORKING,
                content="",
            )

    def test_whitespace_content_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            MemoryStoreRequest(
                category=MemoryCategory.WORKING,
                content="   ",
            )


# ── MemoryEntry ───────────────────────────────────────────────────


@pytest.mark.unit
class TestMemoryEntry:
    def test_all_fields(self) -> None:
        now = datetime.now(tz=UTC)
        e = MemoryEntry(
            id="mem-001",
            agent_id="agent-a",
            category=MemoryCategory.PROCEDURAL,
            content="step-by-step process",
            created_at=now,
        )
        assert e.id == "mem-001"
        assert e.agent_id == "agent-a"
        assert e.category is MemoryCategory.PROCEDURAL
        assert e.relevance_score is None
        assert e.updated_at is None
        assert e.expires_at is None

    def test_with_relevance_score(self) -> None:
        now = datetime.now(tz=UTC)
        e = MemoryEntry(
            id="mem-002",
            agent_id="agent-b",
            category=MemoryCategory.SOCIAL,
            content="team dynamics",
            created_at=now,
            relevance_score=0.95,
        )
        assert e.relevance_score == 0.95

    def test_frozen(self) -> None:
        now = datetime.now(tz=UTC)
        e = MemoryEntry(
            id="mem-001",
            agent_id="agent-a",
            category=MemoryCategory.EPISODIC,
            content="event",
            created_at=now,
        )
        with pytest.raises(ValidationError):
            e.content = "changed"  # type: ignore[misc]

    def test_relevance_score_lower_bound(self) -> None:
        now = datetime.now(tz=UTC)
        e = MemoryEntry(
            id="m",
            agent_id="a",
            category=MemoryCategory.WORKING,
            content="c",
            created_at=now,
            relevance_score=0.0,
        )
        assert e.relevance_score == 0.0

    def test_relevance_score_upper_bound(self) -> None:
        now = datetime.now(tz=UTC)
        e = MemoryEntry(
            id="m",
            agent_id="a",
            category=MemoryCategory.WORKING,
            content="c",
            created_at=now,
            relevance_score=1.0,
        )
        assert e.relevance_score == 1.0

    def test_relevance_score_below_zero_rejected(self) -> None:
        now = datetime.now(tz=UTC)
        with pytest.raises(ValidationError):
            MemoryEntry(
                id="m",
                agent_id="a",
                category=MemoryCategory.WORKING,
                content="c",
                created_at=now,
                relevance_score=-0.1,
            )

    def test_relevance_score_above_one_rejected(self) -> None:
        now = datetime.now(tz=UTC)
        with pytest.raises(ValidationError):
            MemoryEntry(
                id="m",
                agent_id="a",
                category=MemoryCategory.WORKING,
                content="c",
                created_at=now,
                relevance_score=1.1,
            )

    def test_empty_id_rejected(self) -> None:
        now = datetime.now(tz=UTC)
        with pytest.raises(ValidationError, match="at least 1 character"):
            MemoryEntry(
                id="",
                agent_id="a",
                category=MemoryCategory.WORKING,
                content="c",
                created_at=now,
            )

    def test_empty_agent_id_rejected(self) -> None:
        now = datetime.now(tz=UTC)
        with pytest.raises(ValidationError, match="at least 1 character"):
            MemoryEntry(
                id="m",
                agent_id="",
                category=MemoryCategory.WORKING,
                content="c",
                created_at=now,
            )

    def test_relevance_score_nan_rejected(self) -> None:
        now = datetime.now(tz=UTC)
        with pytest.raises(ValidationError):
            MemoryEntry(
                id="m",
                agent_id="a",
                category=MemoryCategory.WORKING,
                content="c",
                created_at=now,
                relevance_score=float("nan"),
            )

    def test_updated_at_before_created_at_rejected(self) -> None:
        now = datetime.now(tz=UTC)
        with pytest.raises(ValidationError, match="updated_at"):
            MemoryEntry(
                id="m",
                agent_id="a",
                category=MemoryCategory.WORKING,
                content="c",
                created_at=now,
                updated_at=now - timedelta(hours=1),
            )

    def test_updated_at_equal_created_at_accepted(self) -> None:
        now = datetime.now(tz=UTC)
        e = MemoryEntry(
            id="m",
            agent_id="a",
            category=MemoryCategory.WORKING,
            content="c",
            created_at=now,
            updated_at=now,
        )
        assert e.updated_at == e.created_at

    def test_updated_at_after_created_at_accepted(self) -> None:
        now = datetime.now(tz=UTC)
        e = MemoryEntry(
            id="m",
            agent_id="a",
            category=MemoryCategory.WORKING,
            content="c",
            created_at=now,
            updated_at=now + timedelta(hours=1),
        )
        assert e.updated_at is not None
        assert e.updated_at > e.created_at

    def test_expires_at_before_created_at_rejected(self) -> None:
        now = datetime.now(tz=UTC)
        with pytest.raises(ValidationError, match="expires_at"):
            MemoryEntry(
                id="m",
                agent_id="a",
                category=MemoryCategory.WORKING,
                content="c",
                created_at=now,
                expires_at=now - timedelta(hours=1),
            )

    def test_expires_at_equal_created_at_accepted(self) -> None:
        now = datetime.now(tz=UTC)
        e = MemoryEntry(
            id="m",
            agent_id="a",
            category=MemoryCategory.WORKING,
            content="c",
            created_at=now,
            expires_at=now,
        )
        assert e.expires_at == e.created_at

    def test_expires_at_after_created_at_accepted(self) -> None:
        now = datetime.now(tz=UTC)
        e = MemoryEntry(
            id="m",
            agent_id="a",
            category=MemoryCategory.WORKING,
            content="c",
            created_at=now,
            expires_at=now + timedelta(days=30),
        )
        assert e.expires_at is not None
        assert e.expires_at > e.created_at

    def test_naive_created_at_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone"):
            MemoryEntry(
                id="m",
                agent_id="a",
                category=MemoryCategory.WORKING,
                content="c",
                created_at=datetime(2025, 1, 1),  # noqa: DTZ001
            )

    def test_naive_expires_at_on_store_request_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone"):
            MemoryStoreRequest(
                category=MemoryCategory.WORKING,
                content="c",
                expires_at=datetime(2025, 1, 1),  # noqa: DTZ001
            )

    def test_json_roundtrip(self) -> None:
        now = datetime.now(tz=UTC)
        e = MemoryEntry(
            id="mem-rt",
            agent_id="agent-rt",
            category=MemoryCategory.SEMANTIC,
            content="roundtrip test",
            metadata=MemoryMetadata(source="test", confidence=0.7, tags=("tag1",)),
            created_at=now,
            relevance_score=0.5,
        )
        json_str = e.model_dump_json()
        restored = MemoryEntry.model_validate_json(json_str)
        assert restored == e


# ── MemoryQuery ───────────────────────────────────────────────────


@pytest.mark.unit
class TestMemoryQuery:
    def test_defaults(self) -> None:
        q = MemoryQuery()
        assert q.text is None
        assert q.categories is None
        assert q.tags == ()
        assert q.min_relevance == 0.0
        assert q.limit == 10
        assert q.since is None
        assert q.until is None

    def test_with_text(self) -> None:
        q = MemoryQuery(text="search term")
        assert q.text == "search term"

    def test_with_categories(self) -> None:
        cats = frozenset({MemoryCategory.EPISODIC, MemoryCategory.SEMANTIC})
        q = MemoryQuery(categories=cats)
        assert q.categories == cats

    def test_frozen(self) -> None:
        q = MemoryQuery()
        with pytest.raises(ValidationError):
            q.limit = 20  # type: ignore[misc]

    def test_limit_lower_bound(self) -> None:
        q = MemoryQuery(limit=1)
        assert q.limit == 1

    def test_limit_upper_bound(self) -> None:
        q = MemoryQuery(limit=1000)
        assert q.limit == 1000

    def test_limit_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryQuery(limit=0)

    def test_limit_above_max_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryQuery(limit=1001)

    def test_since_before_until_accepted(self) -> None:
        now = datetime.now(tz=UTC)
        q = MemoryQuery(
            since=now - timedelta(hours=1),
            until=now,
        )
        assert q.since is not None
        assert q.until is not None

    def test_since_equal_until_rejected(self) -> None:
        now = datetime.now(tz=UTC)
        with pytest.raises(ValidationError, match="since must be before until"):
            MemoryQuery(since=now, until=now)

    def test_since_after_until_rejected(self) -> None:
        now = datetime.now(tz=UTC)
        with pytest.raises(ValidationError, match="since must be before until"):
            MemoryQuery(
                since=now,
                until=now - timedelta(hours=1),
            )

    def test_min_relevance_bounds(self) -> None:
        q_low = MemoryQuery(min_relevance=0.0)
        assert q_low.min_relevance == 0.0
        q_high = MemoryQuery(min_relevance=1.0)
        assert q_high.min_relevance == 1.0

    def test_min_relevance_below_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryQuery(min_relevance=-0.1)

    def test_min_relevance_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryQuery(min_relevance=1.1)

    def test_min_relevance_nan_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryQuery(min_relevance=float("nan"))

    def test_naive_since_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone"):
            MemoryQuery(since=datetime(2025, 1, 1))  # noqa: DTZ001

    def test_duplicate_tags_deduplicated(self) -> None:
        q = MemoryQuery(tags=("important", "reviewed", "important"))
        assert q.tags == ("important", "reviewed")

    def test_duplicate_tags_preserves_order(self) -> None:
        q = MemoryQuery(tags=("b", "a", "b", "c", "a"))
        assert q.tags == ("b", "a", "c")

    def test_json_roundtrip(self) -> None:
        now = datetime.now(tz=UTC)
        q = MemoryQuery(
            text="search",
            categories=frozenset({MemoryCategory.WORKING}),
            tags=("tag1",),
            min_relevance=0.5,
            limit=50,
            since=now - timedelta(days=1),
            until=now,
        )
        json_str = q.model_dump_json()
        restored = MemoryQuery.model_validate_json(json_str)
        assert restored.text == q.text
        assert restored.limit == q.limit
        assert restored.min_relevance == q.min_relevance
        assert restored.categories == q.categories
        assert restored.tags == q.tags
        assert restored.since == q.since
        assert restored.until == q.until
