"""Unit tests for the shared ``resolve_currency`` helper."""

from unittest.mock import AsyncMock

import pytest

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.budget.currency_resolver import resolve_currency
from synthorg.settings.resolver import ConfigResolver
from tests._shared import mock_of

pytestmark = pytest.mark.unit


async def test_none_resolver_falls_back_to_default() -> None:
    assert await resolve_currency(None) == DEFAULT_CURRENCY


async def test_returns_resolved_currency() -> None:
    resolver = mock_of[ConfigResolver](get_str=AsyncMock(return_value="EUR"))
    assert await resolve_currency(resolver) == "EUR"


async def test_non_critical_error_falls_back_to_default() -> None:
    resolver = mock_of[ConfigResolver](
        get_str=AsyncMock(side_effect=ValueError("settings outage"))
    )
    assert await resolve_currency(resolver) == DEFAULT_CURRENCY


async def test_critical_error_reraises() -> None:
    resolver = mock_of[ConfigResolver](get_str=AsyncMock(side_effect=MemoryError()))
    with pytest.raises(MemoryError):
        await resolve_currency(resolver)
