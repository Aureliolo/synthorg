"""Tests for :class:`synthorg.memory.registry.MemoryBackendRegistry`."""

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.memory.config import CompanyMemoryConfig
from synthorg.memory.factory import MemoryBackendDeps, default_registry
from synthorg.memory.registry import MemoryBackendRegistry

if TYPE_CHECKING:
    from synthorg.memory.protocol import MemoryBackend

pytestmark = pytest.mark.unit


def _stub_backend() -> MemoryBackend:
    return MagicMock(spec_set=[])


def test_default_registry_includes_all_three_backends() -> None:
    registry = default_registry()

    assert "sqlvector" in registry
    assert "inmemory" in registry
    assert "composite" in registry
    assert registry.names() == ("composite", "inmemory", "sqlvector")
    assert len(registry) == 3


def test_build_dispatches_to_factory_with_config_and_deps() -> None:
    received: dict[str, object] = {}
    sentinel = _stub_backend()
    deps = MemoryBackendDeps()

    def _build(
        config: CompanyMemoryConfig,
        *,
        deps: MemoryBackendDeps,
    ) -> MemoryBackend:
        received["config"] = config
        received["deps"] = deps
        return sentinel

    registry = MemoryBackendRegistry({"inmemory": _build})
    config = CompanyMemoryConfig(backend="inmemory")

    result = registry.build("inmemory", config, deps=deps)

    assert result is sentinel
    assert received["config"] is config
    assert received["deps"] is deps


def test_build_unknown_backend_raises_strategy_factory_not_found() -> None:
    registry = MemoryBackendRegistry(
        {"inmemory": lambda config, *, deps: _stub_backend()},
    )
    config = CompanyMemoryConfig(backend="inmemory")

    with pytest.raises(StrategyFactoryNotFoundError) as excinfo:
        registry.build("nonexistent", config, deps=MemoryBackendDeps())

    assert "nonexistent" in str(excinfo.value)
    assert "memory_backend" in str(excinfo.value)


def test_empty_factories_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="at least one factory"):
        MemoryBackendRegistry({})


def test_contains_only_matches_string_keys() -> None:
    registry = MemoryBackendRegistry(
        {"inmemory": lambda config, *, deps: _stub_backend()},
    )

    assert "inmemory" in registry
    assert "sqlvector" not in registry
    assert 42 not in registry
