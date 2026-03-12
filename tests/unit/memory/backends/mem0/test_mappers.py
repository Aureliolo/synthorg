"""Tests for Mem0 mapping functions."""

from datetime import UTC, datetime, timedelta

import pytest

from ai_company.core.enums import MemoryCategory
from ai_company.memory.backends.mem0.mappers import (
    _PREFIX,
    apply_post_filters,
    build_mem0_metadata,
    mem0_result_to_entry,
    normalize_relevance_score,
    parse_mem0_datetime,
    parse_mem0_metadata,
    query_to_mem0_getall_args,
    query_to_mem0_search_args,
    store_request_to_mem0_args,
)
from ai_company.memory.models import (
    MemoryEntry,
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
)

pytestmark = pytest.mark.timeout(30)


def _make_entry(  # noqa: PLR0913
    *,
    memory_id: str = "mem-1",
    agent_id: str = "test-agent-001",
    category: MemoryCategory = MemoryCategory.EPISODIC,
    content: str = "test content",
    tags: tuple[str, ...] = (),
    relevance_score: float | None = None,
    created_at: datetime | None = None,
) -> MemoryEntry:
    """Helper to build a MemoryEntry for tests."""
    now = created_at or datetime.now(UTC)
    return MemoryEntry(
        id=memory_id,
        agent_id=agent_id,
        category=category,
        content=content,
        metadata=MemoryMetadata(tags=tags),
        created_at=now,
        relevance_score=relevance_score,
    )


@pytest.mark.unit
class TestBuildMem0Metadata:
    def test_basic_request(self) -> None:
        request = MemoryStoreRequest(
            category=MemoryCategory.EPISODIC,
            content="test content",
        )
        meta = build_mem0_metadata(request)

        assert meta[f"{_PREFIX}category"] == "episodic"
        assert meta[f"{_PREFIX}confidence"] == 1.0
        assert f"{_PREFIX}source" not in meta
        assert f"{_PREFIX}tags" not in meta
        assert f"{_PREFIX}expires_at" not in meta

    def test_full_metadata(self) -> None:
        expires = datetime.now(UTC) + timedelta(days=7)
        request = MemoryStoreRequest(
            category=MemoryCategory.SEMANTIC,
            content="important fact",
            metadata=MemoryMetadata(
                source="task-123",
                confidence=0.85,
                tags=("tag-a", "tag-b"),
            ),
            expires_at=expires,
        )
        meta = build_mem0_metadata(request)

        assert meta[f"{_PREFIX}category"] == "semantic"
        assert meta[f"{_PREFIX}confidence"] == 0.85
        assert meta[f"{_PREFIX}source"] == "task-123"
        assert meta[f"{_PREFIX}tags"] == ["tag-a", "tag-b"]
        assert meta[f"{_PREFIX}expires_at"] == expires.isoformat()


@pytest.mark.unit
class TestStoreRequestToMem0Args:
    def test_basic_conversion(self) -> None:
        request = MemoryStoreRequest(
            category=MemoryCategory.WORKING,
            content="remember this",
        )
        args = store_request_to_mem0_args("test-agent-001", request)

        assert args["messages"] == [
            {"role": "user", "content": "remember this"},
        ]
        assert args["user_id"] == "test-agent-001"
        assert args["infer"] is False
        assert f"{_PREFIX}category" in args["metadata"]


@pytest.mark.unit
class TestParseMem0Datetime:
    def test_none_returns_none(self) -> None:
        assert parse_mem0_datetime(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert parse_mem0_datetime("") is None

    def test_aware_iso_string(self) -> None:
        dt = parse_mem0_datetime("2026-03-12T10:30:00+00:00")
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt.year == 2026

    def test_naive_gets_utc(self) -> None:
        dt = parse_mem0_datetime("2026-03-12T10:30:00")
        assert dt is not None
        assert dt.tzinfo == UTC

    def test_non_utc_timezone(self) -> None:
        dt = parse_mem0_datetime("2026-03-12T10:30:00+05:30")
        assert dt is not None
        assert dt.utcoffset() == timedelta(hours=5, minutes=30)


@pytest.mark.unit
class TestNormalizeRelevanceScore:
    def test_none_returns_none(self) -> None:
        assert normalize_relevance_score(None) is None

    def test_in_range(self) -> None:
        assert normalize_relevance_score(0.75) == 0.75

    def test_below_zero_clamped(self) -> None:
        assert normalize_relevance_score(-0.5) == 0.0

    def test_above_one_clamped(self) -> None:
        assert normalize_relevance_score(1.5) == 1.0

    def test_boundaries(self) -> None:
        assert normalize_relevance_score(0.0) == 0.0
        assert normalize_relevance_score(1.0) == 1.0


@pytest.mark.unit
class TestParseMem0Metadata:
    def test_none_metadata(self) -> None:
        category, metadata, expires_at = parse_mem0_metadata(None)
        assert category == MemoryCategory.WORKING
        assert metadata.confidence == 1.0
        assert expires_at is None

    def test_empty_metadata(self) -> None:
        category, metadata, _expires_at = parse_mem0_metadata({})
        assert category == MemoryCategory.WORKING
        assert metadata.confidence == 1.0

    def test_full_metadata(self) -> None:
        raw = {
            f"{_PREFIX}category": "semantic",
            f"{_PREFIX}confidence": 0.9,
            f"{_PREFIX}source": "task-456",
            f"{_PREFIX}tags": ["important", "verified"],
            f"{_PREFIX}expires_at": "2026-12-31T23:59:59+00:00",
        }
        category, metadata, expires_at = parse_mem0_metadata(raw)

        assert category == MemoryCategory.SEMANTIC
        assert metadata.confidence == 0.9
        assert metadata.source == "task-456"
        assert metadata.tags == ("important", "verified")
        assert expires_at is not None
        assert expires_at.year == 2026

    def test_missing_category_defaults_to_working(self) -> None:
        raw = {f"{_PREFIX}confidence": 0.5}
        category, _metadata, _expires = parse_mem0_metadata(raw)
        assert category == MemoryCategory.WORKING

    def test_empty_tags_filtered(self) -> None:
        raw = {f"{_PREFIX}tags": ["valid", "", "  ", "also-valid"]}
        _category, metadata, _expires = parse_mem0_metadata(raw)
        assert metadata.tags == ("valid", "also-valid")


@pytest.mark.unit
class TestMem0ResultToEntry:
    def test_basic_result(self) -> None:
        raw = {
            "id": "abc-123",
            "memory": "test content",
            "created_at": "2026-03-12T10:00:00+00:00",
            "updated_at": None,
            "metadata": {
                f"{_PREFIX}category": "episodic",
                f"{_PREFIX}confidence": 0.8,
            },
        }
        entry = mem0_result_to_entry(raw, "test-agent-001")

        assert entry.id == "abc-123"
        assert entry.agent_id == "test-agent-001"
        assert entry.category == MemoryCategory.EPISODIC
        assert entry.content == "test content"
        assert entry.metadata.confidence == 0.8
        assert entry.relevance_score is None

    def test_with_score(self) -> None:
        raw = {
            "id": "def-456",
            "memory": "scored content",
            "created_at": "2026-03-12T10:00:00+00:00",
            "score": 0.95,
            "metadata": {},
        }
        entry = mem0_result_to_entry(raw, "test-agent-001")

        assert entry.relevance_score == 0.95

    def test_missing_created_at_uses_now(self) -> None:
        raw = {
            "id": "no-time",
            "memory": "timeless",
            "metadata": {},
        }
        before = datetime.now(UTC)
        entry = mem0_result_to_entry(raw, "test-agent-001")
        after = datetime.now(UTC)

        assert before <= entry.created_at <= after

    def test_no_metadata(self) -> None:
        raw = {
            "id": "no-meta",
            "memory": "bare content",
            "created_at": "2026-03-12T10:00:00+00:00",
        }
        entry = mem0_result_to_entry(raw, "test-agent-001")

        assert entry.category == MemoryCategory.WORKING
        assert entry.metadata.confidence == 1.0


@pytest.mark.unit
class TestQueryToMem0SearchArgs:
    def test_basic_search(self) -> None:
        query = MemoryQuery(text="find this", limit=5)
        args = query_to_mem0_search_args("test-agent-001", query)

        assert args["query"] == "find this"
        assert args["user_id"] == "test-agent-001"
        assert args["limit"] == 5

    def test_raises_on_none_text(self) -> None:
        query = MemoryQuery(text=None)
        with pytest.raises(ValueError, match=r"search requires query\.text"):
            query_to_mem0_search_args("test-agent-001", query)


@pytest.mark.unit
class TestQueryToMem0GetallArgs:
    def test_basic_getall(self) -> None:
        query = MemoryQuery(limit=20)
        args = query_to_mem0_getall_args("test-agent-001", query)

        assert args["user_id"] == "test-agent-001"
        assert args["limit"] == 20


@pytest.mark.unit
class TestApplyPostFilters:
    def test_no_filters_passes_all(self) -> None:
        entries = (
            _make_entry(memory_id="m1"),
            _make_entry(memory_id="m2"),
        )
        query = MemoryQuery()
        result = apply_post_filters(entries, query)
        assert len(result) == 2

    def test_category_filter(self) -> None:
        entries = (
            _make_entry(memory_id="m1", category=MemoryCategory.EPISODIC),
            _make_entry(memory_id="m2", category=MemoryCategory.SEMANTIC),
            _make_entry(memory_id="m3", category=MemoryCategory.EPISODIC),
        )
        query = MemoryQuery(
            categories=frozenset({MemoryCategory.EPISODIC}),
        )
        result = apply_post_filters(entries, query)
        assert len(result) == 2
        assert all(e.category == MemoryCategory.EPISODIC for e in result)

    def test_tag_filter(self) -> None:
        entries = (
            _make_entry(memory_id="m1", tags=("important",)),
            _make_entry(memory_id="m2", tags=("trivial",)),
            _make_entry(memory_id="m3", tags=("important", "verified")),
        )
        query = MemoryQuery(tags=("important",))
        result = apply_post_filters(entries, query)
        assert len(result) == 2

    def test_time_range_filter(self) -> None:
        now = datetime.now(UTC)
        old = now - timedelta(hours=48)
        recent = now - timedelta(hours=1)

        entries = (
            _make_entry(memory_id="m1", created_at=old),
            _make_entry(memory_id="m2", created_at=recent),
        )
        query = MemoryQuery(since=now - timedelta(hours=24))
        result = apply_post_filters(entries, query)
        assert len(result) == 1
        assert result[0].id == "m2"

    def test_min_relevance_filter(self) -> None:
        entries = (
            _make_entry(memory_id="m1", relevance_score=0.9),
            _make_entry(memory_id="m2", relevance_score=0.3),
            _make_entry(memory_id="m3", relevance_score=None),
        )
        query = MemoryQuery(min_relevance=0.5)
        result = apply_post_filters(entries, query)
        # m1 passes (0.9 >= 0.5), m2 fails (0.3 < 0.5), m3 passes (None skips check)
        assert len(result) == 2

    def test_until_filter_exclusive(self) -> None:
        now = datetime.now(UTC)
        entries = (
            _make_entry(memory_id="m1", created_at=now - timedelta(hours=2)),
            _make_entry(memory_id="m2", created_at=now),
        )
        query = MemoryQuery(until=now)
        result = apply_post_filters(entries, query)
        assert len(result) == 1
        assert result[0].id == "m1"

    def test_combined_filters(self) -> None:
        now = datetime.now(UTC)
        entries = (
            _make_entry(
                memory_id="m1",
                category=MemoryCategory.EPISODIC,
                tags=("important",),
                created_at=now - timedelta(hours=1),
            ),
            _make_entry(
                memory_id="m2",
                category=MemoryCategory.SEMANTIC,
                tags=("important",),
                created_at=now - timedelta(hours=1),
            ),
        )
        query = MemoryQuery(
            categories=frozenset({MemoryCategory.EPISODIC}),
            tags=("important",),
            since=now - timedelta(hours=2),
        )
        result = apply_post_filters(entries, query)
        assert len(result) == 1
        assert result[0].id == "m1"
