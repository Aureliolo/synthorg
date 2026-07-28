"""Tests for the multi-provider ``TunnelManager`` facade."""

from typing import override
from unittest.mock import AsyncMock

import pytest

from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.errors import ConnectionNotFoundError, TunnelError
from synthorg.integrations.tunnel.devtunnels_adapter import DevTunnelsAdapter
from synthorg.integrations.tunnel.manager import (
    TunnelManager,
    credential_connection_name,
)
from synthorg.integrations.tunnel.ngrok_adapter import NgrokAdapter
from synthorg.integrations.tunnel.protocol import (
    DeviceLoginPrompt,
    TunnelAdapter,
    TunnelCredentialKind,
)
from tests._shared import mock_of

pytestmark = pytest.mark.unit


class FakeAdapter:
    """Full ``TunnelAdapter`` implementation (typeguard checks the protocol)."""

    def __init__(
        self,
        provider_id: str,
        *,
        credential_kind: TunnelCredentialKind = TunnelCredentialKind.NONE,
        available: bool = True,
    ) -> None:
        self._provider_id = provider_id
        self._credential_kind = credential_kind
        self._available = available
        self.url: str | None = None
        self.start_calls = 0
        self.stop_calls = 0

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def display_name(self) -> str:
        return self._provider_id.title()

    @property
    def credential_kind(self) -> TunnelCredentialKind:
        return self._credential_kind

    async def availability(self) -> tuple[bool, str | None]:
        return self._available, None if self._available else "missing binary"

    async def credential_configured(self) -> bool:
        return self._credential_kind is TunnelCredentialKind.NONE

    async def start(self) -> str:
        self.start_calls += 1
        self.url = f"https://{self._provider_id}.example.test"
        return self.url

    async def stop(self) -> None:
        self.stop_calls += 1
        self.url = None

    async def get_url(self) -> str | None:
        return self.url


def _manager(
    *adapters: TunnelAdapter,
    selected: str | None = None,
    catalog: ConnectionCatalog | None = None,
) -> TunnelManager:
    manager = TunnelManager(
        adapters=adapters or (FakeAdapter("cloudflare"),),
        default_provider_id="cloudflare",
    )

    async def _selection() -> str | None:
        return selected

    manager.bind_runtime(
        selection_source=_selection,
        catalog_source=lambda: catalog,
    )
    return manager


class TestSelection:
    async def test_defaults_to_cloudflare_without_sources(self) -> None:
        manager = TunnelManager(adapters=(FakeAdapter("cloudflare"),))
        snapshot = await manager.snapshot()
        assert snapshot.selected_provider == "cloudflare"

    async def test_setting_selects_provider(self) -> None:
        ngrok = FakeAdapter("ngrok", credential_kind=TunnelCredentialKind.TOKEN)
        manager = _manager(FakeAdapter("cloudflare"), ngrok, selected="ngrok")
        snapshot = await manager.snapshot()
        assert snapshot.selected_provider == "ngrok"

    async def test_unknown_selection_falls_back_to_default(self) -> None:
        manager = _manager(FakeAdapter("cloudflare"), selected="nonsense")
        snapshot = await manager.snapshot()
        assert snapshot.selected_provider == "cloudflare"


class TestLifecycle:
    async def test_start_uses_selected_provider(self) -> None:
        cloudflare = FakeAdapter("cloudflare")
        devtunnels = FakeAdapter("devtunnels")
        manager = _manager(cloudflare, devtunnels, selected="devtunnels")
        url = await manager.start()
        assert url == "https://devtunnels.example.test"
        assert devtunnels.start_calls == 1
        assert cloudflare.start_calls == 0
        assert await manager.get_url() == url

    async def test_switching_provider_stops_previous_tunnel(self) -> None:
        cloudflare = FakeAdapter("cloudflare")
        devtunnels = FakeAdapter("devtunnels")
        manager = TunnelManager(adapters=(cloudflare, devtunnels))
        selection = {"value": "cloudflare"}

        async def _selection() -> str | None:
            return selection["value"]

        manager.bind_runtime(selection_source=_selection, catalog_source=lambda: None)
        await manager.start()
        selection["value"] = "devtunnels"
        url = await manager.start()
        assert cloudflare.stop_calls == 1
        assert url == "https://devtunnels.example.test"
        snapshot = await manager.snapshot()
        assert snapshot.active_provider == "devtunnels"

    async def test_stop_is_noop_when_never_started(self) -> None:
        cloudflare = FakeAdapter("cloudflare")
        manager = _manager(cloudflare)
        await manager.stop()
        assert cloudflare.stop_calls == 0
        assert await manager.get_url() is None


class TestSnapshot:
    async def test_degrades_provider_on_probe_failure(self) -> None:
        class ExplodingAdapter(FakeAdapter):
            @override
            async def availability(self) -> tuple[bool, str | None]:
                msg = "probe blew up"
                raise RuntimeError(msg)

        manager = _manager(FakeAdapter("cloudflare"), ExplodingAdapter("ngrok"))
        snapshot = await manager.snapshot()
        by_id = {p.provider_id: p for p in snapshot.providers}
        assert by_id["cloudflare"].available is True
        assert by_id["ngrok"].available is False
        assert by_id["ngrok"].detail == "Availability probe failed."
        # The credential probe runs independently, so an availability
        # failure does not clobber its answer.
        assert by_id["ngrok"].credential_configured is True

    async def test_degrades_credential_on_probe_failure(self) -> None:
        class ExplodingCredentialAdapter(FakeAdapter):
            @override
            async def credential_configured(self) -> bool:
                msg = "credential probe blew up"
                raise RuntimeError(msg)

        manager = _manager(
            FakeAdapter("cloudflare"), ExplodingCredentialAdapter("ngrok")
        )
        snapshot = await manager.snapshot()
        by_id = {p.provider_id: p for p in snapshot.providers}
        assert by_id["ngrok"].available is True
        assert by_id["ngrok"].credential_configured is False


class TestCredentials:
    async def test_store_token_mints_catalog_connection(self) -> None:
        catalog = mock_of[ConnectionCatalog](
            delete=AsyncMock(side_effect=ConnectionNotFoundError("absent")),
            create=AsyncMock(return_value=None),
        )
        ngrok = FakeAdapter("ngrok", credential_kind=TunnelCredentialKind.TOKEN)
        manager = _manager(FakeAdapter("cloudflare"), ngrok, catalog=catalog)

        await manager.store_token("ngrok", "tok-123")

        catalog.create.assert_awaited_once()
        kwargs = catalog.create.await_args.kwargs
        assert kwargs["name"] == credential_connection_name("ngrok")
        assert kwargs["credentials"] == {"auth_token": "tok-123"}
        assert kwargs["health_check_enabled"] is False

    async def test_store_token_rejects_non_token_provider(self) -> None:
        manager = _manager(FakeAdapter("cloudflare"))
        with pytest.raises(TunnelError, match="does not take an auth token"):
            await manager.store_token("cloudflare", "tok")

    async def test_store_token_rejects_unknown_provider(self) -> None:
        manager = _manager(FakeAdapter("cloudflare"))
        with pytest.raises(TunnelError, match="Unknown tunnel provider"):
            await manager.store_token("nonsense", "tok")

    async def test_store_token_requires_catalog(self) -> None:
        ngrok = FakeAdapter("ngrok", credential_kind=TunnelCredentialKind.TOKEN)
        manager = _manager(FakeAdapter("cloudflare"), ngrok, catalog=None)
        with pytest.raises(TunnelError, match="persistence"):
            await manager.store_token("ngrok", "tok")

    async def test_clear_token_is_idempotent(self) -> None:
        catalog = mock_of[ConnectionCatalog](
            delete=AsyncMock(side_effect=ConnectionNotFoundError("absent")),
        )
        ngrok = FakeAdapter("ngrok", credential_kind=TunnelCredentialKind.TOKEN)
        manager = _manager(FakeAdapter("cloudflare"), ngrok, catalog=catalog)
        await manager.clear_token("ngrok")
        catalog.delete.assert_awaited_once_with(credential_connection_name("ngrok"))

    async def test_ngrok_adapter_reads_catalog_token_through_manager(self) -> None:
        catalog = mock_of[ConnectionCatalog](
            get_credentials_or_none=AsyncMock(return_value={"auth_token": "cat-token"}),
        )
        ngrok = NgrokAdapter(auth_token_env="SYNTHORG_TEST_UNSET_TOKEN", port=3001)
        manager = TunnelManager(adapters=(FakeAdapter("cloudflare"), ngrok))

        async def _selection() -> str | None:
            return None

        manager.bind_runtime(
            selection_source=_selection, catalog_source=lambda: catalog
        )
        assert await ngrok.credential_configured() is True
        catalog.get_credentials_or_none.assert_awaited_with(
            credential_connection_name("ngrok")
        )


def _devtunnels_adapter(prompt: DeviceLoginPrompt) -> DevTunnelsAdapter:
    """A device-login adapter whose ``begin_login`` yields *prompt*.

    ``begin_device_login`` dispatches on ``isinstance(adapter,
    DevTunnelsAdapter)`` because ``begin_login`` is not on the adapter
    protocol, and ``create_autospec`` sets ``__class__`` so the spec
    satisfies that check without constructing the real adapter.
    """
    adapter: DevTunnelsAdapter = mock_of[DevTunnelsAdapter](
        provider_id="devtunnels",
        credential_kind=TunnelCredentialKind.DEVICE_LOGIN,
        begin_login=AsyncMock(return_value=prompt),
        availability=AsyncMock(return_value=(True, None)),
        credential_configured=AsyncMock(return_value=False),
    )
    return adapter


class TestDeviceLoginConnectionSeed:
    """A device-login provider's catalog row is minted by the login, not a read."""

    async def test_status_read_never_writes(self) -> None:
        catalog = mock_of[ConnectionCatalog](
            get=AsyncMock(return_value=None),
            create=AsyncMock(return_value=None),
        )
        devtunnels = FakeAdapter(
            "devtunnels", credential_kind=TunnelCredentialKind.DEVICE_LOGIN
        )
        manager = _manager(FakeAdapter("cloudflare"), devtunnels, catalog=catalog)

        await manager.snapshot()
        await manager.provider_status("devtunnels")

        # The dashboard polls both on a timer: a connection minted here would
        # reappear moments after the operator deleted it.
        catalog.create.assert_not_called()

    async def test_device_login_seeds_missing_connection(self) -> None:
        catalog = mock_of[ConnectionCatalog](
            get=AsyncMock(return_value=None),
            create=AsyncMock(return_value=None),
        )
        prompt = DeviceLoginPrompt(
            verification_uri="https://example.test/login",
            user_code="ABCD-1234",
        )
        devtunnels = _devtunnels_adapter(prompt)
        manager = _manager(FakeAdapter("cloudflare"), devtunnels, catalog=catalog)

        assert await manager.begin_device_login("devtunnels") == prompt

        catalog.create.assert_awaited_once()
        kwargs = catalog.create.await_args.kwargs
        assert kwargs["name"] == credential_connection_name("devtunnels")
        assert kwargs["credentials"] == {}
        assert kwargs["health_check_enabled"] is False

    async def test_seed_is_idempotent_when_already_present(self) -> None:
        # get returns a truthy row: the manager only checks it is not None
        # before deciding the connection already exists.
        catalog = mock_of[ConnectionCatalog](
            get=AsyncMock(return_value=object()),
            create=AsyncMock(return_value=None),
        )
        prompt = DeviceLoginPrompt(
            verification_uri="https://example.test/login",
            user_code="ABCD-1234",
        )
        devtunnels = _devtunnels_adapter(prompt)
        manager = _manager(FakeAdapter("cloudflare"), devtunnels, catalog=catalog)

        await manager.begin_device_login("devtunnels")

        catalog.create.assert_not_called()

    async def test_seed_skipped_without_catalog(self) -> None:
        prompt = DeviceLoginPrompt(
            verification_uri="https://example.test/login",
            user_code="ABCD-1234",
        )
        devtunnels = _devtunnels_adapter(prompt)
        manager = _manager(FakeAdapter("cloudflare"), devtunnels, catalog=None)
        # No catalog wired -> no crash, the row appears once persistence is up.
        assert await manager.begin_device_login("devtunnels") == prompt


class TestDeviceLogin:
    async def test_rejects_provider_without_device_login(self) -> None:
        manager = _manager(FakeAdapter("cloudflare"))
        with pytest.raises(TunnelError, match="no device login"):
            await manager.begin_device_login("cloudflare")
