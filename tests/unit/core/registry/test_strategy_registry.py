"""Tests for :class:`synthorg.core.registry.StrategyRegistry`."""

import pytest

from synthorg.core.registry import StrategyFactoryNotFoundError, StrategyRegistry

pytestmark = pytest.mark.unit


class _Greeter:
    def __init__(self, name: str, *, loud: bool = False) -> None:
        self.name = name
        self.loud = loud

    def greet(self) -> str:
        return f"HI {self.name.upper()}!" if self.loud else f"hi {self.name}"


def _build_loud(name: str) -> _Greeter:
    return _Greeter(name, loud=True)


def _build_quiet(name: str) -> _Greeter:
    return _Greeter(name, loud=False)


def test_build_dispatches_to_registered_factory() -> None:
    registry = StrategyRegistry[_Greeter](
        {"loud": _build_loud, "quiet": _build_quiet},
        kind="greeter",
    )

    loud = registry.build("loud", "Daisy")
    quiet = registry.build("quiet", "Daisy")

    assert loud.greet() == "HI DAISY!"
    assert quiet.greet() == "hi Daisy"


def test_get_returns_factory_callable() -> None:
    registry = StrategyRegistry[_Greeter]({"loud": _build_loud}, kind="greeter")

    factory = registry.get("loud")

    assert factory is _build_loud


def test_get_unknown_name_raises_strategy_factory_not_found() -> None:
    registry = StrategyRegistry[_Greeter](
        {"loud": _build_loud, "quiet": _build_quiet},
        kind="greeter",
    )

    with pytest.raises(StrategyFactoryNotFoundError) as excinfo:
        registry.get("shouty")

    assert "greeter" in str(excinfo.value)
    assert "shouty" in str(excinfo.value)
    assert excinfo.value.context == {"kind": "greeter", "name": "shouty"}


def test_build_propagates_factory_exception() -> None:
    def _broken(_name: str) -> _Greeter:
        msg = "factory exploded"
        raise RuntimeError(msg)

    registry = StrategyRegistry[_Greeter]({"broken": _broken}, kind="greeter")

    with pytest.raises(RuntimeError, match="factory exploded"):
        registry.build("broken", "Daisy")


def test_contains_only_matches_registered_string_keys() -> None:
    registry = StrategyRegistry[_Greeter]({"loud": _build_loud}, kind="greeter")

    assert "loud" in registry
    assert "quiet" not in registry
    assert 42 not in registry  # type: ignore[operator]


def test_names_returns_sorted_tuple() -> None:
    registry = StrategyRegistry[_Greeter](
        {"quiet": _build_quiet, "loud": _build_loud},
        kind="greeter",
    )

    assert registry.names() == ("loud", "quiet")


def test_len_reports_factory_count() -> None:
    registry = StrategyRegistry[_Greeter](
        {"loud": _build_loud, "quiet": _build_quiet},
        kind="greeter",
    )

    assert len(registry) == 2


def test_kind_is_exposed() -> None:
    registry = StrategyRegistry[_Greeter]({"loud": _build_loud}, kind="greeter")

    assert registry.kind == "greeter"


def test_empty_factories_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="requires at least one factory"):
        StrategyRegistry[_Greeter]({}, kind="greeter")


def test_registry_storage_is_immutable() -> None:
    factories = {"loud": _build_loud}
    registry = StrategyRegistry[_Greeter](factories, kind="greeter")

    factories["sneaky"] = _build_quiet

    assert "sneaky" not in registry
    assert registry.names() == ("loud",)


def test_build_forwards_keyword_arguments() -> None:
    def _build_named(name: str, *, loud: bool) -> _Greeter:
        return _Greeter(name, loud=loud)

    registry = StrategyRegistry[_Greeter]({"named": _build_named}, kind="greeter")

    instance = registry.build("named", "Daisy", loud=True)

    assert instance.loud is True
    assert instance.name == "Daisy"
