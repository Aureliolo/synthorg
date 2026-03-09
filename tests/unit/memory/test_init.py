"""Tests for memory package re-exports."""

import pytest

import ai_company.memory as memory_module

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestMemoryExports:
    def test_all_exports_importable(self) -> None:
        for name in memory_module.__all__:
            assert hasattr(memory_module, name), f"{name} in __all__ but not importable"

    def test_all_has_expected_count(self) -> None:
        assert len(memory_module.__all__) == 18
