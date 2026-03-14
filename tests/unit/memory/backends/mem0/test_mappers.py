"""Tests for Mem0 mapping functions."""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

from synthorg.core.enums import MemoryCategory
from synthorg.memory.backends.mem0.mappers import (
    _PREFIX,
    PUBLISHER_KEY,
    _coerce_confidence,
    _normalize_tags,
    apply_post_filters,
    build_mem0_metadata,
    extract_category,
    extract_publisher,
    mem0_result_to_entry,
    normalize_relevance_score,
    parse_mem0_datetime,
    parse_mem0_metadata,
    query_to_mem0_getall_args,
    query_to_mem0_search_args,
    validate_add_result,
)
from synthorg.memory.errors import MemoryRetrievalError, MemoryStoreError
from synthorg.memory.models import (
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
    expires_at: datetime | None = None,
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
        expires_at=expires_at,
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

    def test_malformed_datetime_returns_none(self) -> None:
        assert parse_mem0_datetime("not-a-date") is None

    def test_partial_datetime_returns_none(self) -> None:
        assert parse_mem0_datetime("2026-13-45T99:99:99") is None


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

    def test_string_score_coerced(self) -> None:
        assert normalize_relevance_score("0.82") == 0.82

    def test_non_numeric_string_returns_none(self) -> None:
        assert normalize_relevance_score("not-a-number") is None

    def test_non_numeric_type_returns_none(self) -> None:
        assert normalize_relevance_score(object()) is None

    def test_nan_returns_none(self) -> None:
        assert normalize_relevance_score(float("nan")) is None

    def test_nan_string_returns_none(self) -> None:
        assert normalize_relevance_score("nan") is None

    def test_inf_returns_none(self) -> None:
        assert normalize_relevance_score(float("inf")) is None

    def test_neg_inf_returns_none(self) -> None:
        assert normalize_relevance_score(float("-inf")) is None


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

    def test_non_dict_truthy_metadata(self) -> None:
        """Non-dict truthy metadata (e.g. string) uses defaults."""
        category, metadata, expires_at = parse_mem0_metadata("not-a-dict")  # type: ignore[arg-type]
        assert category == MemoryCategory.WORKING
        assert metadata.confidence == 1.0
        assert expires_at is None

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

    def test_invalid_category_defaults_to_working(self) -> None:
        raw = {f"{_PREFIX}category": "nonexistent_category"}
        category, _metadata, _expires = parse_mem0_metadata(raw)
        assert category == MemoryCategory.WORKING

    def test_empty_tags_filtered(self) -> None:
        raw = {f"{_PREFIX}tags": ["valid", "", "  ", "also-valid"]}
        _category, metadata, _expires = parse_mem0_metadata(raw)
        assert metadata.tags == ("valid", "also-valid")

    def test_blank_source_returns_none(self) -> None:
        raw = {f"{_PREFIX}source": "   "}
        _category, metadata, _expires = parse_mem0_metadata(raw)
        assert metadata.source is None

    def test_non_string_source_coerced(self) -> None:
        raw = {f"{_PREFIX}source": 42}
        _category, metadata, _expires = parse_mem0_metadata(raw)
        assert metadata.source == "42"


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

    def test_missing_created_at_falls_back_to_updated_at(self) -> None:
        """When created_at is missing, falls back to updated_at."""
        raw = {
            "id": "no-created",
            "memory": "content",
            "updated_at": "2026-03-10T08:00:00+00:00",
            "metadata": {},
        }
        entry = mem0_result_to_entry(raw, "test-agent-001")
        assert entry.created_at == datetime(2026, 3, 10, 8, 0, 0, tzinfo=UTC)

    def test_missing_created_at_falls_back_to_expires_at(self) -> None:
        """When created_at and updated_at are missing, falls back to expires_at."""
        raw = {
            "id": "no-created",
            "memory": "content",
            "metadata": {"_synthorg_expires_at": "2026-04-01T00:00:00+00:00"},
        }
        entry = mem0_result_to_entry(raw, "test-agent-001")
        assert entry.created_at == datetime(2026, 4, 1, 0, 0, 0, tzinfo=UTC)

    def test_no_metadata(self) -> None:
        raw = {
            "id": "no-meta",
            "memory": "bare content",
            "created_at": "2026-03-12T10:00:00+00:00",
        }
        entry = mem0_result_to_entry(raw, "test-agent-001")

        assert entry.category == MemoryCategory.WORKING
        assert entry.metadata.confidence == 1.0

    def test_missing_id_raises(self) -> None:
        raw = {"memory": "no id here", "metadata": {}}
        with pytest.raises(MemoryRetrievalError, match="missing or blank 'id'"):
            mem0_result_to_entry(raw, "test-agent-001")

    def test_empty_content_raises(self) -> None:
        raw = {"id": "empty-content", "memory": "", "metadata": {}}
        with pytest.raises(MemoryRetrievalError, match="empty content"):
            mem0_result_to_entry(raw, "test-agent-001")

    def test_whitespace_only_content_raises(self) -> None:
        raw = {"id": "ws-only", "memory": "   ", "metadata": {}}
        with pytest.raises(MemoryRetrievalError, match="empty content"):
            mem0_result_to_entry(raw, "test-agent-001")

    def test_data_key_fallback(self) -> None:
        raw = {
            "id": "data-key",
            "data": "content via data key",
            "created_at": "2026-03-12T10:00:00+00:00",
            "metadata": {},
        }
        entry = mem0_result_to_entry(raw, "test-agent-001")
        assert entry.content == "content via data key"


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

    def test_expired_entries_excluded(self) -> None:
        """Entries with expires_at in the past are filtered out."""
        now = datetime.now(UTC)
        past = now - timedelta(days=7)
        entries = (
            _make_entry(
                memory_id="m1",
                created_at=past,
                expires_at=now - timedelta(hours=1),
            ),
            _make_entry(
                memory_id="m2",
                created_at=past,
                expires_at=now + timedelta(hours=1),
            ),
            _make_entry(memory_id="m3"),  # no expires_at
        )
        query = MemoryQuery()
        result = apply_post_filters(entries, query)
        assert len(result) == 2
        assert {e.id for e in result} == {"m2", "m3"}

    def test_exactly_expired_entry_excluded(self) -> None:
        """Entry with expires_at == now is excluded (<=)."""
        fixed_now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
        past = fixed_now - timedelta(days=7)
        entries = (_make_entry(memory_id="m1", created_at=past, expires_at=fixed_now),)
        query = MemoryQuery()
        with patch(
            "synthorg.memory.backends.mem0.mappers.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)  # noqa: DTZ001, PLW0108
            result = apply_post_filters(entries, query)
        assert len(result) == 0

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


@pytest.mark.unit
class TestValidateAddResult:
    def test_valid_result(self) -> None:
        result = {"results": [{"id": "mem-001", "event": "ADD"}]}
        memory_id = validate_add_result(result, context="test")
        assert memory_id == "mem-001"

    @pytest.mark.parametrize(
        ("result", "expected_match"),
        [
            ({"results": []}, "no results"),
            ({"data": "something"}, "no results"),
            ({"results": "not-a-list"}, "no results"),
            ({"results": [{"memory": "no id"}]}, "missing or blank 'id'"),
            ({"results": [{"id": None, "event": "ADD"}]}, "missing or blank 'id'"),
            ({"results": [{"id": "", "event": "ADD"}]}, "missing or blank 'id'"),
            ({"results": [{"id": "   ", "event": "ADD"}]}, "missing or blank 'id'"),
            ("not-a-dict", "unexpected type"),
            ({"results": ["not-a-dict"]}, "not a dict"),
        ],
    )
    def test_malformed_result_raises(
        self,
        result: Any,
        expected_match: str,
    ) -> None:
        with pytest.raises(MemoryStoreError, match=expected_match):
            validate_add_result(result, context="test")

    def test_numeric_id_coerced_to_string(self) -> None:
        result = {"results": [{"id": 42, "event": "ADD"}]}
        memory_id = validate_add_result(result, context="test")
        assert memory_id == "42"


@pytest.mark.unit
class TestExtractCategory:
    def test_valid_category(self) -> None:
        raw = {"metadata": {f"{_PREFIX}category": "episodic"}}
        assert extract_category(raw) == MemoryCategory.EPISODIC

    def test_missing_metadata(self) -> None:
        raw = {"id": "m1", "memory": "content"}
        assert extract_category(raw) == MemoryCategory.WORKING

    def test_empty_metadata(self) -> None:
        raw: dict[str, Any] = {"metadata": {}}
        assert extract_category(raw) == MemoryCategory.WORKING

    def test_none_metadata(self) -> None:
        raw: dict[str, Any] = {"metadata": None}
        assert extract_category(raw) == MemoryCategory.WORKING

    def test_list_metadata_defaults(self) -> None:
        raw: dict[str, Any] = {"metadata": ["not", "a", "dict"]}
        assert extract_category(raw) == MemoryCategory.WORKING

    def test_string_metadata_defaults(self) -> None:
        raw: dict[str, Any] = {"metadata": "oops"}
        assert extract_category(raw) == MemoryCategory.WORKING

    def test_invalid_category_defaults(self) -> None:
        raw = {"metadata": {f"{_PREFIX}category": "nonexistent"}}
        assert extract_category(raw) == MemoryCategory.WORKING

    def test_missing_category_key(self) -> None:
        raw = {"metadata": {f"{_PREFIX}confidence": 0.9}}
        assert extract_category(raw) == MemoryCategory.WORKING


@pytest.mark.unit
class TestExtractPublisher:
    def test_valid_publisher(self) -> None:
        from synthorg.memory.backends.mem0.mappers import PUBLISHER_KEY

        raw = {"metadata": {PUBLISHER_KEY: "test-agent-001"}}
        assert extract_publisher(raw) == "test-agent-001"

    def test_missing_metadata(self) -> None:
        raw = {"id": "m1"}
        assert extract_publisher(raw) is None

    def test_empty_metadata(self) -> None:
        raw: dict[str, Any] = {"metadata": {}}
        assert extract_publisher(raw) is None

    def test_none_metadata(self) -> None:
        raw = {"metadata": None}
        assert extract_publisher(raw) is None

    def test_no_publisher_key(self) -> None:
        raw = {"metadata": {"_synthorg_category": "semantic"}}
        assert extract_publisher(raw) is None

    def test_list_metadata_returns_none(self) -> None:
        raw: dict[str, Any] = {"metadata": ["not", "a", "dict"]}
        assert extract_publisher(raw) is None

    def test_string_metadata_returns_none(self) -> None:
        raw: dict[str, Any] = {"metadata": "oops"}
        assert extract_publisher(raw) is None

    def test_numeric_publisher_coerced_to_string(self) -> None:
        raw: dict[str, Any] = {"metadata": {PUBLISHER_KEY: 42}}
        assert extract_publisher(raw) == "42"

    def test_blank_publisher_returns_none(self) -> None:
        raw: dict[str, Any] = {"metadata": {PUBLISHER_KEY: "   "}}
        assert extract_publisher(raw) is None


@pytest.mark.unit
class TestCoerceConfidence:
    def test_default_when_absent(self) -> None:
        """Missing key returns 1.0."""
        assert _coerce_confidence({}) == 1.0

    def test_numeric_value(self) -> None:
        assert _coerce_confidence({f"{_PREFIX}confidence": 0.7}) == 0.7

    def test_non_numeric_returns_half(self) -> None:
        """Non-numeric confidence defaults to 0.5."""
        assert _coerce_confidence({f"{_PREFIX}confidence": "not-a-number"}) == 0.5

    def test_above_one_clamped(self) -> None:
        assert _coerce_confidence({f"{_PREFIX}confidence": 1.5}) == 1.0

    def test_below_zero_clamped(self) -> None:
        assert _coerce_confidence({f"{_PREFIX}confidence": -0.5}) == 0.0

    def test_object_type_returns_half(self) -> None:
        """Object type that can't convert to float defaults to 0.5."""
        assert _coerce_confidence({f"{_PREFIX}confidence": object()}) == 0.5

    def test_nan_returns_half(self) -> None:
        """NaN confidence defaults to 0.5."""
        assert _coerce_confidence({f"{_PREFIX}confidence": float("nan")}) == 0.5

    def test_inf_returns_half(self) -> None:
        """Inf confidence defaults to 0.5."""
        assert _coerce_confidence({f"{_PREFIX}confidence": float("inf")}) == 0.5


@pytest.mark.unit
class TestNormalizeTags:
    def test_single_string_wrapped_in_list(self) -> None:
        """A single string tag is wrapped into a tuple."""
        result = _normalize_tags({f"{_PREFIX}tags": "solo-tag"})
        assert result == ("solo-tag",)

    def test_list_of_strings(self) -> None:
        result = _normalize_tags({f"{_PREFIX}tags": ["a", "b", "c"]})
        assert result == ("a", "b", "c")

    def test_empty_strings_filtered(self) -> None:
        result = _normalize_tags({f"{_PREFIX}tags": ["valid", "", "  "]})
        assert result == ("valid",)

    def test_dict_type_ignored(self) -> None:
        """dict type for tags is unexpected and returns empty tuple."""
        result = _normalize_tags({f"{_PREFIX}tags": {"key": "value"}})
        assert result == ()

    def test_int_type_ignored(self) -> None:
        """int type for tags is unexpected and returns empty tuple."""
        result = _normalize_tags({f"{_PREFIX}tags": 42})
        assert result == ()

    def test_missing_key_returns_empty(self) -> None:
        result = _normalize_tags({})
        assert result == ()
