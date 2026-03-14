"""Tests for memory capabilities protocol compliance."""

import pytest

from synthorg.core.enums import MemoryCategory
from synthorg.memory.capabilities import MemoryCapabilities

pytestmark = pytest.mark.timeout(30)


class _FakeMemoryCapabilities:
    """Minimal implementation for protocol compliance testing."""

    @property
    def supported_categories(self) -> frozenset[MemoryCategory]:
        return frozenset(MemoryCategory)

    @property
    def supports_graph(self) -> bool:
        return False

    @property
    def supports_temporal(self) -> bool:
        return False

    @property
    def supports_vector_search(self) -> bool:
        return True

    @property
    def supports_shared_access(self) -> bool:
        return False

    @property
    def max_memories_per_agent(self) -> int | None:
        return 10_000


@pytest.mark.unit
class TestCapabilitiesProtocolCompliance:
    def test_fake_is_memory_capabilities(self) -> None:
        assert isinstance(_FakeMemoryCapabilities(), MemoryCapabilities)

    def test_supported_categories_is_frozenset(self) -> None:
        caps = _FakeMemoryCapabilities()
        assert isinstance(caps.supported_categories, frozenset)

    def test_boolean_properties(self) -> None:
        caps = _FakeMemoryCapabilities()
        assert isinstance(caps.supports_graph, bool)
        assert isinstance(caps.supports_temporal, bool)
        assert isinstance(caps.supports_vector_search, bool)
        assert isinstance(caps.supports_shared_access, bool)

    def test_max_memories_per_agent_type(self) -> None:
        caps = _FakeMemoryCapabilities()
        result = caps.max_memories_per_agent
        assert result is None or isinstance(result, int)


class _FakeUnlimitedCapabilities:
    """Capabilities with unlimited memories."""

    @property
    def supported_categories(self) -> frozenset[MemoryCategory]:
        return frozenset({MemoryCategory.WORKING})

    @property
    def supports_graph(self) -> bool:
        return True

    @property
    def supports_temporal(self) -> bool:
        return True

    @property
    def supports_vector_search(self) -> bool:
        return True

    @property
    def supports_shared_access(self) -> bool:
        return True

    @property
    def max_memories_per_agent(self) -> int | None:
        return None


class _IncompleteCapabilities:
    """Missing required properties — should fail isinstance check."""

    @property
    def supported_categories(self) -> frozenset[MemoryCategory]:
        return frozenset(MemoryCategory)


@pytest.mark.unit
class TestCapabilitiesNonCompliance:
    def test_incomplete_class_fails_isinstance(self) -> None:
        assert not isinstance(_IncompleteCapabilities(), MemoryCapabilities)

    def test_plain_object_fails_isinstance(self) -> None:
        assert not isinstance(object(), MemoryCapabilities)


@pytest.mark.unit
class TestCapabilitiesVariants:
    def test_unlimited_is_memory_capabilities(self) -> None:
        assert isinstance(_FakeUnlimitedCapabilities(), MemoryCapabilities)

    def test_unlimited_max_is_none(self) -> None:
        assert _FakeUnlimitedCapabilities().max_memories_per_agent is None

    def test_partial_categories(self) -> None:
        caps = _FakeUnlimitedCapabilities()
        assert caps.supported_categories == frozenset({MemoryCategory.WORKING})
