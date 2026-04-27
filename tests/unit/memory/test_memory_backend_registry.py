"""Tests for :class:`synthorg.memory.registry.MemoryBackendRegistry`."""

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.memory.config import CompanyMemoryConfig
from synthorg.memory.factory import default_registry
from synthorg.memory.registry import MemoryBackendRegistry

if TYPE_CHECKING:
    from synthorg.memory.backends.mem0.config import Mem0EmbedderConfig
    from synthorg.memory.protocol import MemoryBackend

pytestmark = pytest.mark.unit


def _stub_backend() -> MemoryBackend:
    return MagicMock(spec_set=[])  # type: ignore[return-value]


def test_default_registry_includes_all_three_backends() -> None:
    registry = default_registry()

    assert "mem0" in registry
    assert "inmemory" in registry
    assert "composite" in registry
    assert registry.names() == ("composite", "inmemory", "mem0")
    assert len(registry) == 3


def test_build_dispatches_to_factory_with_config_and_embedder() -> None:
    received: dict[str, object] = {}
    sentinel = _stub_backend()

    def _build(
        config: CompanyMemoryConfig,
        *,
        embedder: Mem0EmbedderConfig | None,
    ) -> MemoryBackend:
        received["config"] = config
        received["embedder"] = embedder
        return sentinel

    registry = MemoryBackendRegistry({"inmemory": _build})
    config = CompanyMemoryConfig(backend="inmemory")

    result = registry.build("inmemory", config, embedder=None)

    assert result is sentinel
    assert received["config"] is config
    assert received["embedder"] is None


def test_build_unknown_backend_raises_strategy_factory_not_found() -> None:
    registry = MemoryBackendRegistry(
        {"inmemory": lambda _c, *, embedder=None: _stub_backend()},
    )
    config = CompanyMemoryConfig(backend="inmemory")

    with pytest.raises(StrategyFactoryNotFoundError) as excinfo:
        registry.build("nonexistent", config, embedder=None)

    assert "nonexistent" in str(excinfo.value)
    assert "memory_backend" in str(excinfo.value)


def test_empty_factories_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="at least one factory"):
        MemoryBackendRegistry({})


def test_contains_only_matches_string_keys() -> None:
    registry = MemoryBackendRegistry(
        {"inmemory": lambda _c, *, embedder=None: _stub_backend()},
    )

    assert "inmemory" in registry
    assert "mem0" not in registry
    assert 42 not in registry  # type: ignore[operator]
