"""Tests for engine package __all__ re-exports."""

import pytest

import synthorg.engine as engine_mod


@pytest.mark.unit
class TestEngineAllExports:
    """Engine __init__.__all__ re-exports."""

    def test_all_names_importable(self) -> None:
        for name in engine_mod.__all__:
            assert hasattr(engine_mod, name), f"{name!r} in __all__ but not importable"
