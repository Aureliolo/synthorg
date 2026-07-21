"""Tests for CompositeBackendConfig."""

import pytest
from pydantic import ValidationError

from synthorg.memory.backends.composite.config import (
    CompositeBackendConfig,
)


@pytest.mark.unit
class TestCompositeBackendConfig:
    def test_defaults(self) -> None:
        cfg = CompositeBackendConfig()
        assert cfg.routes == {}
        assert cfg.default == "inmemory"

    def test_custom_routes(self) -> None:
        cfg = CompositeBackendConfig(
            routes={"memories": "sqlvector", "scratch": "inmemory"},
            default="inmemory",
        )
        assert cfg.routes["memories"] == "sqlvector"
        assert cfg.routes["scratch"] == "inmemory"

    def test_frozen(self) -> None:
        cfg = CompositeBackendConfig()
        with pytest.raises(ValidationError):
            cfg.default = "other"  # type: ignore[misc]

    def test_empty_default_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least 1 character"):
            CompositeBackendConfig(default="")

    def test_whitespace_default_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            CompositeBackendConfig(default="  ")

    def test_routes_are_read_only(self) -> None:
        cfg = CompositeBackendConfig(routes={"memories": "sqlvector"})
        with pytest.raises(TypeError):
            cfg.routes["memories"] = "inmemory"  # type: ignore[index]

    def test_deep_copy_preserves_routes(self) -> None:
        cfg = CompositeBackendConfig(routes={"memories": "sqlvector"})
        clone = cfg.model_copy(deep=True)
        assert clone.routes == {"memories": "sqlvector"}
        with pytest.raises(TypeError):
            clone.routes["memories"] = "inmemory"  # type: ignore[index]
