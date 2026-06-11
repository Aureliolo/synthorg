"""Tests for :class:`synthorg.persistence.registry.PersistenceBackendRegistry`."""

from unittest.mock import MagicMock

import pytest

from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.persistence.config import PersistenceConfig, SQLiteConfig
from synthorg.persistence.factory import default_registry
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.registry import PersistenceBackendRegistry

pytestmark = pytest.mark.unit


def _stub_backend() -> PersistenceBackend:
    return MagicMock(spec_set=[])


def test_default_registry_includes_sqlite_and_postgres() -> None:
    registry = default_registry()

    assert "sqlite" in registry
    assert "postgres" in registry
    assert registry.names() == ("postgres", "sqlite")
    assert len(registry) == 2


def test_build_dispatches_on_backend_field() -> None:
    sentinel = _stub_backend()
    factory_called_with: dict[str, PersistenceConfig] = {}

    def _build(config: PersistenceConfig) -> PersistenceBackend:
        factory_called_with["config"] = config
        return sentinel

    registry = PersistenceBackendRegistry({"sqlite": _build})
    config = PersistenceConfig(sqlite=SQLiteConfig(path=":memory:"))

    result = registry.build(config)

    assert result is sentinel
    assert factory_called_with["config"] is config


def test_build_unknown_backend_raises_strategy_factory_not_found() -> None:
    registry = PersistenceBackendRegistry({"sqlite": lambda _c: _stub_backend()})
    config = PersistenceConfig(sqlite=SQLiteConfig(path=":memory:"))
    bad_config = config.model_copy(update={"backend": "cassandra"})

    with pytest.raises(StrategyFactoryNotFoundError) as excinfo:
        registry.build(bad_config)

    assert "cassandra" in str(excinfo.value)
    assert "persistence_backend" in str(excinfo.value)


def test_empty_factories_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="at least one factory"):
        PersistenceBackendRegistry({})


def test_contains_only_matches_string_keys() -> None:
    registry = PersistenceBackendRegistry({"sqlite": lambda _c: _stub_backend()})

    assert "sqlite" in registry
    assert "postgres" not in registry
    assert 42 not in registry


def test_factory_exception_propagates() -> None:
    def _broken(_config: PersistenceConfig) -> PersistenceBackend:
        msg = "boom"
        raise RuntimeError(msg)

    registry = PersistenceBackendRegistry({"sqlite": _broken})
    config = PersistenceConfig(sqlite=SQLiteConfig(path=":memory:"))

    with pytest.raises(RuntimeError, match="boom"):
        registry.build(config)
