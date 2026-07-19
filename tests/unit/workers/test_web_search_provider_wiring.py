"""Tests for the boot web-search-provider wiring from ``tools`` settings."""

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
import structlog

from synthorg.integrations.connections.models import Connection
from synthorg.settings.resolver import ConfigResolver
from synthorg.tools.web.providers.http_search_provider import HttpWebSearchProvider
from synthorg.workers._web_search_provider_wiring import (
    build_web_search_provider_or_none,
)
from tests._shared import FakeClock, mock_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState

pytestmark = pytest.mark.unit


class _StubCatalog:
    """Satisfies the credential-source + connection-lookup seams for wiring tests."""

    async def get_credentials(self, name: str) -> dict[str, str]:
        del name
        return {"api_key": "k"}

    async def get(self, name: str) -> Connection | None:
        del name
        # No connection object -> the provider falls back to the default
        # rate-limit ceiling, which is all these wiring tests need.
        return None


def _app_state(*, catalog: object) -> AppState:
    integrations = SimpleNamespace(connection_catalog=catalog)
    config = SimpleNamespace(web=None)
    return cast(
        "AppState",
        SimpleNamespace(
            slice=lambda _cls: integrations,
            config=config,
            clock=FakeClock(),
        ),
    )


def _resolver(
    *,
    enabled: bool,
    provider: str = "brave",
    connection: str = "search-conn",
    max_results: int = 10,
    timeout: float = 15.0,
) -> ConfigResolver:
    resolver = mock_of[ConfigResolver]()
    resolver.get_bool.return_value = enabled
    values = {"web_search_provider": provider, "web_search_connection": connection}

    def _get_str(_ns: str, key: str) -> str:
        # Keyed (not positional) so a change in read order can't silently
        # target the wrong lookup. Non-async: AsyncMock wraps the return.
        return values[key]

    resolver.get_str.side_effect = _get_str
    resolver.get_int.return_value = max_results
    resolver.get_float.return_value = timeout
    return cast("ConfigResolver", resolver)


def _patch(monkeypatch: pytest.MonkeyPatch, resolver: ConfigResolver) -> None:
    monkeypatch.setattr(
        "synthorg.workers._web_search_provider_wiring.config_resolver_of",
        lambda _app_state: resolver,
    )


async def test_returns_none_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _resolver(enabled=False))
    result = await build_web_search_provider_or_none(_app_state(catalog=_StubCatalog()))
    assert result is None


async def test_returns_none_when_no_connection_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, _resolver(enabled=True))
    result = await build_web_search_provider_or_none(_app_state(catalog=None))
    assert result is None


async def test_returns_none_for_unknown_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, _resolver(enabled=True, provider="does-not-exist"))
    result = await build_web_search_provider_or_none(_app_state(catalog=_StubCatalog()))
    assert result is None


async def test_returns_none_when_no_connection_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, _resolver(enabled=True, connection="   "))
    result = await build_web_search_provider_or_none(_app_state(catalog=_StubCatalog()))
    assert result is None


async def test_builds_provider_on_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, _resolver(enabled=True))
    result = await build_web_search_provider_or_none(_app_state(catalog=_StubCatalog()))
    assert isinstance(result, HttpWebSearchProvider)


async def test_no_connection_bound_logs_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An enabled-but-unbound connection is an operator misconfig -> ERROR."""
    _patch(monkeypatch, _resolver(enabled=True, connection="   "))
    with structlog.testing.capture_logs() as cap:
        result = await build_web_search_provider_or_none(
            _app_state(catalog=_StubCatalog()),
        )
    assert result is None
    errors = [
        e
        for e in cap
        if e.get("service") == "web_search" and e.get("log_level") == "error"
    ]
    assert errors


async def test_returns_none_when_enabled_resolve_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = mock_of[ConfigResolver]()
    resolver.get_bool.side_effect = RuntimeError("settings backend down")
    _patch(monkeypatch, cast("ConfigResolver", resolver))
    result = await build_web_search_provider_or_none(_app_state(catalog=_StubCatalog()))
    assert result is None


async def test_returns_none_when_settings_resolve_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = mock_of[ConfigResolver]()
    resolver.get_bool.return_value = True
    resolver.get_str.side_effect = RuntimeError("settings backend down")
    _patch(monkeypatch, cast("ConfigResolver", resolver))
    result = await build_web_search_provider_or_none(_app_state(catalog=_StubCatalog()))
    assert result is None
