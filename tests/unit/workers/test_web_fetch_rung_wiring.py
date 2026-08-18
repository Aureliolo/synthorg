"""Tests for the boot assembly of the ``web_fetch`` ladder from settings.

The operator owns which rungs exist. This module is where that ownership is
actually exercised, so these pin what each setting produces and, more
importantly, what happens when a rung is asked for and cannot be built: it is
left out and said so, rather than registered and unable to answer.
"""

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from synthorg.integrations.connections.models import Connection
from synthorg.settings.resolver import ConfigResolver
from synthorg.tools.web.fetch_types import FetchBackend
from synthorg.tools.web.providers.http_fetch_provider import HttpWebFetchProvider
from synthorg.tools.web.providers.local_fetch_provider import LocalFetchProvider
from synthorg.workers._web_fetch_rung_wiring import build_web_fetch_rungs_or_none
from tests._shared import FakeClock, mock_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState

pytestmark = pytest.mark.unit

_CHAR_BUDGET = 40_000
_MAX_BYTES = 2_097_152
_TIMEOUT = 30.0


class _StubCatalog:
    """Credential source + connection lookup for the proxy rung."""

    async def get_credentials(self, name: str) -> dict[str, str]:
        del name
        return {"api_key": "k"}

    async def get(self, name: str) -> Connection | None:
        del name
        return None


def _app_state(*, catalog: object = None) -> AppState:
    integrations = SimpleNamespace(connection_catalog=catalog)
    return cast(
        "AppState",
        SimpleNamespace(
            slice=lambda _cls: integrations,
            config=SimpleNamespace(web=None),
            clock=FakeClock(),
        ),
    )


def _resolver(
    *,
    enabled: bool = True,
    proxy_enabled: bool = False,
    render_enabled: bool = False,
    discover: bool = True,
    provider: str = "ollama",
    connection: str = "reader-conn",
) -> ConfigResolver:
    """Build a resolver whose reads are keyed, never positional.

    Keyed so a change in the order the wiring reads its settings cannot
    silently hand a value to the wrong question.
    """
    bools = {
        "web_fetch_enabled": enabled,
        "web_fetch_proxy_enabled": proxy_enabled,
        "web_fetch_render_enabled": render_enabled,
        "web_fetch_docs_index_discovery_enabled": discover,
    }
    strs = {
        "web_fetch_user_agent": "synthorg-test",
        "web_search_provider": provider,
        "web_search_connection": connection,
    }
    ints = {
        "web_fetch_max_characters": _CHAR_BUDGET,
        "web_fetch_max_response_bytes": _MAX_BYTES,
    }
    resolver = mock_of[ConfigResolver]()
    resolver.get_bool.side_effect = lambda _ns, key: bools[key]
    resolver.get_str.side_effect = lambda _ns, key: strs[key]
    resolver.get_int.side_effect = lambda _ns, key: ints[key]
    resolver.get_float.return_value = _TIMEOUT
    return cast("ConfigResolver", resolver)


def _patch(monkeypatch: pytest.MonkeyPatch, resolver: ConfigResolver) -> None:
    monkeypatch.setattr(
        "synthorg.workers._web_fetch_rung_wiring.config_resolver_of",
        lambda _app_state: resolver,
    )


class TestFeatureFlag:
    async def test_disabled_builds_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch(monkeypatch, _resolver(enabled=False))
        assert await build_web_fetch_rungs_or_none(_app_state()) is None

    async def test_a_settings_failure_degrades_to_off(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A misconfigured feature must never crash the agent runtime."""
        resolver = mock_of[ConfigResolver]()
        resolver.get_bool.side_effect = RuntimeError("settings backend down")
        _patch(monkeypatch, cast("ConfigResolver", resolver))
        assert await build_web_fetch_rungs_or_none(_app_state()) is None


class TestLocalRung:
    async def test_the_local_rung_needs_no_credential(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """It is why fetch ships on while search ships off."""
        _patch(monkeypatch, _resolver())
        rungs = await build_web_fetch_rungs_or_none(_app_state())
        assert rungs is not None
        assert isinstance(rungs.providers[FetchBackend.LOCAL], LocalFetchProvider)

    async def test_only_the_local_rung_is_built_by_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch(monkeypatch, _resolver())
        rungs = await build_web_fetch_rungs_or_none(_app_state())
        assert rungs is not None
        assert set(rungs.providers) == {FetchBackend.LOCAL}

    async def test_the_budget_reaches_the_ladder(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch(monkeypatch, _resolver())
        rungs = await build_web_fetch_rungs_or_none(_app_state())
        assert rungs is not None
        assert rungs.char_budget == _CHAR_BUDGET


class TestProxyRung:
    async def test_it_is_built_when_enabled_and_bound(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch(monkeypatch, _resolver(proxy_enabled=True))
        rungs = await build_web_fetch_rungs_or_none(_app_state(catalog=_StubCatalog()))
        assert rungs is not None
        assert isinstance(rungs.providers[FetchBackend.PROXY], HttpWebFetchProvider)

    async def test_a_vendor_with_no_reader_leaves_the_rung_out(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Not every search vendor sells a reader, and pretending otherwise
        would register a rung that fails on first use."""
        _patch(monkeypatch, _resolver(proxy_enabled=True, provider="brave"))
        rungs = await build_web_fetch_rungs_or_none(_app_state(catalog=_StubCatalog()))
        assert rungs is not None
        assert FetchBackend.PROXY not in rungs.providers

    async def test_an_unbound_connection_leaves_the_rung_out(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch(monkeypatch, _resolver(proxy_enabled=True, connection=""))
        rungs = await build_web_fetch_rungs_or_none(_app_state(catalog=_StubCatalog()))
        assert rungs is not None
        assert FetchBackend.PROXY not in rungs.providers

    async def test_an_unset_provider_leaves_the_rung_out(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch(monkeypatch, _resolver(proxy_enabled=True, provider=""))
        rungs = await build_web_fetch_rungs_or_none(_app_state(catalog=_StubCatalog()))
        assert rungs is not None
        assert FetchBackend.PROXY not in rungs.providers

    async def test_no_catalog_leaves_the_rung_out(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The rung brokers a credential, so it cannot exist without the store."""
        _patch(monkeypatch, _resolver(proxy_enabled=True))
        rungs = await build_web_fetch_rungs_or_none(_app_state(catalog=None))
        assert rungs is not None
        assert FetchBackend.PROXY not in rungs.providers

    async def test_the_local_rung_survives_a_failed_proxy(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """One rung failing must not take the tool down with it."""
        _patch(monkeypatch, _resolver(proxy_enabled=True, provider="brave"))
        rungs = await build_web_fetch_rungs_or_none(_app_state(catalog=_StubCatalog()))
        assert rungs is not None
        assert FetchBackend.LOCAL in rungs.providers


class TestRenderRung:
    """Render is requested here and completed in the tool factory.

    The browser it drives is built in the execution cohort, so this module
    records the operator's answer and the factory supplies the browser.
    """

    async def test_the_request_is_carried_through(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch(monkeypatch, _resolver(render_enabled=True))
        rungs = await build_web_fetch_rungs_or_none(_app_state())
        assert rungs is not None
        assert rungs.render_enabled is True

    async def test_it_is_not_a_provider_at_this_layer(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch(monkeypatch, _resolver(render_enabled=True))
        rungs = await build_web_fetch_rungs_or_none(_app_state())
        assert rungs is not None
        assert FetchBackend.RENDER not in rungs.providers


class TestDocsIndexDiscovery:
    @pytest.mark.parametrize("discover", [True, False])
    async def test_the_operator_choice_is_carried_through(
        self,
        monkeypatch: pytest.MonkeyPatch,
        discover: bool,
    ) -> None:
        _patch(monkeypatch, _resolver(discover=discover))
        rungs = await build_web_fetch_rungs_or_none(_app_state())
        assert rungs is not None
        assert rungs.discover_docs_index is discover
