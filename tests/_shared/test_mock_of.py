"""Tests for the ``mock_of[T]`` typed-boundary substitution helper."""

from typing import Any, Protocol, cast
from unittest.mock import AsyncMock, Mock

import pytest

from tests._shared import mock_of

pytestmark = pytest.mark.unit


class _ConcreteService:
    """Sample spec class covering sync and async methods."""

    def method(self, x: int) -> str:
        return f"method({x})"

    async def async_method(self, y: int) -> int:
        return y * 2


class _Renderer(Protocol):
    """Protocol-typed spec to confirm Protocol support."""

    def render(self, x: int) -> str: ...


def test_mock_of_returns_typed_autospec() -> None:
    m = cast(Any, mock_of[_ConcreteService]())

    m.method(1)
    m.method.assert_called_once_with(1)

    with pytest.raises(AttributeError):
        _ = m.nonexistent_method


def test_mock_of_overrides_apply() -> None:
    override = AsyncMock(return_value=99)
    m = mock_of[_ConcreteService](async_method=override)
    assert m.async_method is override


def test_mock_of_unknown_override_key_raises() -> None:
    with pytest.raises(AttributeError, match=r"_ConcreteService.*does_not_exist"):
        mock_of[_ConcreteService](does_not_exist=42)


def test_mock_of_with_protocol() -> None:
    m = cast(Any, mock_of[_Renderer]())
    m.render.return_value = "rendered"
    assert m.render(7) == "rendered"


async def test_mock_of_async_method_is_async() -> None:
    m = cast(Any, mock_of[_ConcreteService]())
    await m.async_method(3)
    m.async_method.assert_awaited_once_with(3)


def test_mock_of_subscript_required() -> None:
    with pytest.raises(TypeError):
        mock_of(_ConcreteService)  # type: ignore[call-arg]


def test_mock_of_supports_isinstance() -> None:
    m = mock_of[_ConcreteService]()
    assert isinstance(m, _ConcreteService)


def test_mock_of_does_not_leak_state_between_calls() -> None:
    a = cast(Mock, mock_of[_ConcreteService]())
    b = cast(Mock, mock_of[_ConcreteService]())

    a.method(1)
    a.method.assert_called_once_with(1)
    b.method.assert_not_called()
