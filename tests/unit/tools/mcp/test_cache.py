"""Tests for MCP result cache."""

import time
from unittest.mock import patch

import pytest

from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.mcp.cache import MCPResultCache, _make_hashable

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


class TestCacheHitMiss:
    """Basic cache hit/miss behavior."""

    def test_miss_returns_none(
        self,
        result_cache: MCPResultCache,
    ) -> None:
        assert result_cache.get("tool", {}) is None

    def test_put_then_get_returns_result(
        self,
        result_cache: MCPResultCache,
    ) -> None:
        result = ToolExecutionResult(content="cached")
        result_cache.put("tool", {"key": "val"}, result)
        cached = result_cache.get("tool", {"key": "val"})
        assert cached is not None
        assert cached.content == "cached"

    def test_different_args_different_entries(
        self,
        result_cache: MCPResultCache,
    ) -> None:
        r1 = ToolExecutionResult(content="r1")
        r2 = ToolExecutionResult(content="r2")
        result_cache.put("tool", {"a": 1}, r1)
        result_cache.put("tool", {"a": 2}, r2)
        assert result_cache.get("tool", {"a": 1}).content == "r1"  # type: ignore[union-attr]
        assert result_cache.get("tool", {"a": 2}).content == "r2"  # type: ignore[union-attr]

    def test_different_tools_different_entries(
        self,
        result_cache: MCPResultCache,
    ) -> None:
        r1 = ToolExecutionResult(content="t1")
        r2 = ToolExecutionResult(content="t2")
        result_cache.put("tool1", {}, r1)
        result_cache.put("tool2", {}, r2)
        assert result_cache.get("tool1", {}).content == "t1"  # type: ignore[union-attr]
        assert result_cache.get("tool2", {}).content == "t2"  # type: ignore[union-attr]


class TestCacheTTL:
    """TTL expiry behavior."""

    def test_expired_entry_returns_none(self) -> None:
        cache = MCPResultCache(max_size=10, ttl_seconds=0.5)
        result = ToolExecutionResult(content="old")
        cache.put("tool", {}, result)

        # Mock time to simulate expiry
        original_time = time.monotonic()
        with patch("synthorg.tools.mcp.cache.time") as mock_time:
            mock_time.monotonic.return_value = original_time + 1.0
            assert cache.get("tool", {}) is None

    def test_fresh_entry_returns_result(self) -> None:
        cache = MCPResultCache(max_size=10, ttl_seconds=60.0)
        result = ToolExecutionResult(content="fresh")
        cache.put("tool", {}, result)
        assert cache.get("tool", {}).content == "fresh"  # type: ignore[union-attr]


class TestCacheLRUEviction:
    """LRU eviction when at capacity."""

    def test_oldest_evicted_at_capacity(self) -> None:
        cache = MCPResultCache(max_size=2, ttl_seconds=60.0)
        r1 = ToolExecutionResult(content="r1")
        r2 = ToolExecutionResult(content="r2")
        r3 = ToolExecutionResult(content="r3")

        cache.put("t1", {}, r1)
        cache.put("t2", {}, r2)
        # This should evict t1 (oldest)
        cache.put("t3", {}, r3)

        assert cache.get("t1", {}) is None
        assert cache.get("t2", {}).content == "r2"  # type: ignore[union-attr]
        assert cache.get("t3", {}).content == "r3"  # type: ignore[union-attr]

    def test_access_refreshes_position(self) -> None:
        cache = MCPResultCache(max_size=2, ttl_seconds=60.0)
        r1 = ToolExecutionResult(content="r1")
        r2 = ToolExecutionResult(content="r2")
        r3 = ToolExecutionResult(content="r3")

        cache.put("t1", {}, r1)
        cache.put("t2", {}, r2)
        # Access t1 to move it to end
        cache.get("t1", {})
        # This should evict t2 (now oldest)
        cache.put("t3", {}, r3)

        assert cache.get("t1", {}).content == "r1"  # type: ignore[union-attr]
        assert cache.get("t2", {}) is None
        assert cache.get("t3", {}).content == "r3"  # type: ignore[union-attr]

    def test_zero_max_size_stores_nothing(self) -> None:
        cache = MCPResultCache(max_size=0, ttl_seconds=60.0)
        result = ToolExecutionResult(content="ignored")
        cache.put("tool", {}, result)
        assert cache.get("tool", {}) is None


class TestCacheInvalidate:
    """Cache invalidation."""

    def test_invalidate_specific_tool(
        self,
        result_cache: MCPResultCache,
    ) -> None:
        r1 = ToolExecutionResult(content="r1")
        r2 = ToolExecutionResult(content="r2")
        result_cache.put("tool1", {"a": 1}, r1)
        result_cache.put("tool1", {"a": 2}, r1)
        result_cache.put("tool2", {}, r2)

        result_cache.invalidate("tool1")

        assert result_cache.get("tool1", {"a": 1}) is None
        assert result_cache.get("tool1", {"a": 2}) is None
        assert result_cache.get("tool2", {}).content == "r2"  # type: ignore[union-attr]

    def test_invalidate_all(
        self,
        result_cache: MCPResultCache,
    ) -> None:
        r1 = ToolExecutionResult(content="r1")
        r2 = ToolExecutionResult(content="r2")
        result_cache.put("tool1", {}, r1)
        result_cache.put("tool2", {}, r2)

        result_cache.invalidate()

        assert result_cache.get("tool1", {}) is None
        assert result_cache.get("tool2", {}) is None


class TestMakeHashable:
    """Recursive hashable conversion."""

    def test_dict_to_frozenset(self) -> None:
        result = _make_hashable({"a": 1, "b": 2})
        assert isinstance(result, frozenset)

    def test_list_to_tuple(self) -> None:
        result = _make_hashable([1, 2, 3])
        assert result == (1, 2, 3)

    def test_nested_dict(self) -> None:
        result = _make_hashable({"outer": {"inner": "val"}})
        assert isinstance(result, frozenset)

    def test_primitive_passthrough(self) -> None:
        assert _make_hashable(42) == 42
        assert _make_hashable("str") == "str"
        assert _make_hashable(None) is None

    def test_mixed_nested(self) -> None:
        result = _make_hashable(
            {"key": [1, {"nested": True}]},
        )
        assert isinstance(result, frozenset)
        # Should be hashable
        hash(result)

    def test_tuple_to_tuple(self) -> None:
        result = _make_hashable((1, 2, 3))
        assert result == (1, 2, 3)

    def test_empty_dict(self) -> None:
        result = _make_hashable({})
        assert result == frozenset()

    def test_empty_list(self) -> None:
        result = _make_hashable([])
        assert result == ()
