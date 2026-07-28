"""Vendor-preset resolution for generic-HTTP connections.

Mirrors ``test_deploy_target`` / ``test_registry_target``: the preset is
operator-set metadata, so resolution must be honest about an absent or
unrecognised value rather than guessing a vendor.
"""

import pytest
from structlog.testing import capture_logs

from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.http_vendor import (
    HTTP_VENDOR_PRESETS,
    METADATA_KEY_VENDOR,
    HttpVendor,
    resolve_vendor,
)
from synthorg.integrations.connections.models import AuthMethod, ConnectionType
from synthorg.integrations.errors import (
    InvalidConnectionAuthError,
    InvalidConnectionEndpointError,
)
from synthorg.observability.events.integrations import (
    HTTP_VENDOR_METADATA_UNRECOGNISED,
)
from tests._shared.connection_catalog import make_in_memory_catalog

pytestmark = pytest.mark.unit


class TestResolveVendor:
    def test_declared_vendor_resolves_to_its_preset(self) -> None:
        preset = resolve_vendor({METADATA_KEY_VENDOR: HttpVendor.BRAVE.value})

        assert preset is not None
        assert preset.id == HttpVendor.BRAVE.value
        assert preset.auth_header == "X-Subscription-Token"

    def test_absent_metadata_resolves_to_none(self) -> None:
        assert resolve_vendor({}) is None

    def test_custom_resolves_to_none(self) -> None:
        # A custom endpoint is a deliberate answer, not a missing one: the
        # operator's own base URL and generic auth apply.
        assert resolve_vendor({METADATA_KEY_VENDOR: HttpVendor.CUSTOM.value}) is None

    def test_unrecognised_vendor_warns_and_resolves_to_none(self) -> None:
        with capture_logs() as logs:
            assert resolve_vendor({METADATA_KEY_VENDOR: "Brave "}) is None

        assert [
            entry
            for entry in logs
            if entry.get("event") == HTTP_VENDOR_METADATA_UNRECOGNISED
        ]


class TestPresets:
    def test_every_preset_is_https(self) -> None:
        assert all(
            preset.base_url.startswith("https://")
            for preset in HTTP_VENDOR_PRESETS.values()
        )

    def test_custom_has_no_preset(self) -> None:
        assert HttpVendor.CUSTOM not in HTTP_VENDOR_PRESETS

    def test_auth_headers_render_the_template(self) -> None:
        tavily = HTTP_VENDOR_PRESETS[HttpVendor.TAVILY]

        assert tavily.auth_headers("k") == {"Authorization": "Bearer k"}

    def test_auth_headers_default_to_the_bare_key(self) -> None:
        brave = HTTP_VENDOR_PRESETS[HttpVendor.BRAVE]

        assert brave.auth_headers("k") == {"X-Subscription-Token": "k"}


class TestCreateWithAVendor:
    """The create path resolves the endpoint the operator was never asked for."""

    async def test_preset_supplies_the_base_url(self) -> None:
        catalog = make_in_memory_catalog()

        conn = await catalog.create(
            name="brave-search",
            connection_type=ConnectionType.GENERIC_HTTP,
            auth_method=AuthMethod.API_KEY.value,
            credentials={"token": "secret"},
            metadata={METADATA_KEY_VENDOR: HttpVendor.BRAVE.value},
        )

        assert conn.base_url == HTTP_VENDOR_PRESETS[HttpVendor.BRAVE].base_url

    async def test_explicit_base_url_wins_over_the_preset(self) -> None:
        catalog = make_in_memory_catalog()

        conn = await catalog.create(
            name="brave-proxy",
            connection_type=ConnectionType.GENERIC_HTTP,
            auth_method=AuthMethod.API_KEY.value,
            credentials={"token": "secret"},
            base_url="https://proxy.example.test/brave",
            metadata={METADATA_KEY_VENDOR: HttpVendor.BRAVE.value},
        )

        assert conn.base_url == "https://proxy.example.test/brave"

    async def test_top_level_base_url_satisfies_validation(self) -> None:
        # The REST create schema carries base_url beside credentials rather
        # than inside them, which is the shape the dashboard submits.
        catalog = make_in_memory_catalog()

        conn = await catalog.create(
            name="custom-api",
            connection_type=ConnectionType.GENERIC_HTTP,
            auth_method=AuthMethod.API_KEY.value,
            credentials={"token": "secret"},
            base_url="https://api.example.test",
            metadata={METADATA_KEY_VENDOR: HttpVendor.CUSTOM.value},
        )

        assert conn.base_url == "https://api.example.test"

    async def test_custom_without_a_base_url_is_refused(self) -> None:
        catalog = make_in_memory_catalog()

        with pytest.raises(InvalidConnectionAuthError, match="base_url"):
            await catalog.create(
                name="custom-api",
                connection_type=ConnectionType.GENERIC_HTTP,
                auth_method=AuthMethod.API_KEY.value,
                credentials={"token": "secret"},
                metadata={METADATA_KEY_VENDOR: HttpVendor.CUSTOM.value},
            )

    async def test_credential_less_connection_is_refused(self) -> None:
        # The preset now supplies base_url, so without this guard the one
        # field this type enforced has become optional and a connection with
        # no way to authenticate is created with no friction at all.
        catalog = make_in_memory_catalog()

        with pytest.raises(InvalidConnectionAuthError, match="credential material"):
            await catalog.create(
                name="brave-search",
                connection_type=ConnectionType.GENERIC_HTTP,
                auth_method=AuthMethod.API_KEY.value,
                credentials={},
                metadata={METADATA_KEY_VENDOR: HttpVendor.BRAVE.value},
            )


class TestUpdateWithAVendor:
    """A PATCH must not clear an endpoint the form was never shown."""

    async def _brave(self, name: str = "brave-search") -> tuple[ConnectionCatalog, str]:
        """Create a preset-bound connection.

        Returns:
            The catalog and the connection name.
        """
        catalog = make_in_memory_catalog()
        await catalog.create(
            name=name,
            connection_type=ConnectionType.GENERIC_HTTP,
            auth_method=AuthMethod.API_KEY.value,
            credentials={"token": "secret"},
            metadata={METADATA_KEY_VENDOR: HttpVendor.BRAVE.value},
        )
        return catalog, name

    async def test_an_unrelated_patch_keeps_the_preset_endpoint(self) -> None:
        # The base-url field is hidden for a preset vendor, so the form submits
        # an explicit null on every save. Taken literally that clears the
        # endpoint of a working connection, and the field stays hidden so
        # there is no way to put it back.
        catalog, name = await self._brave()

        updated = await catalog.update(name, base_url=None, sensitive=True)

        assert updated.base_url == HTTP_VENDOR_PRESETS[HttpVendor.BRAVE].base_url

    async def test_switching_vendor_repoints_the_endpoint(self) -> None:
        catalog, name = await self._brave()

        updated = await catalog.update(
            name,
            base_url=None,
            metadata={METADATA_KEY_VENDOR: HttpVendor.TAVILY.value},
        )

        assert updated.base_url == HTTP_VENDOR_PRESETS[HttpVendor.TAVILY].base_url

    async def test_an_explicit_base_url_still_wins(self) -> None:
        catalog, name = await self._brave()

        updated = await catalog.update(name, base_url="https://proxy.example.test")

        assert updated.base_url == "https://proxy.example.test"

    async def test_a_custom_vendor_keeps_the_operators_url(self) -> None:
        # Nothing can re-derive a custom endpoint, so the stored one stands
        # rather than being cleared to null.
        catalog = make_in_memory_catalog()
        await catalog.create(
            name="custom-api",
            connection_type=ConnectionType.GENERIC_HTTP,
            auth_method=AuthMethod.API_KEY.value,
            credentials={"token": "secret"},
            base_url="https://api.example.test",
            metadata={METADATA_KEY_VENDOR: HttpVendor.CUSTOM.value},
        )

        updated = await catalog.update("custom-api", base_url=None, sensitive=True)

        assert updated.base_url == "https://api.example.test"

    async def test_switching_to_custom_without_a_url_is_refused(self) -> None:
        # Falling back to the stored endpoint here would persist a
        # connection labelled 'custom' and still pointed at Brave: the
        # metadata change lands, the unchanged base_url is dropped as a
        # no-op, and no later read can tell the two apart.
        catalog, name = await self._brave()

        with pytest.raises(InvalidConnectionEndpointError, match="requires a base_url"):
            await catalog.update(
                name,
                base_url=None,
                metadata={METADATA_KEY_VENDOR: HttpVendor.CUSTOM.value},
            )

        unchanged = await catalog.get_or_raise(name)
        assert unchanged.base_url == HTTP_VENDOR_PRESETS[HttpVendor.BRAVE].base_url
        assert unchanged.metadata[METADATA_KEY_VENDOR] == HttpVendor.BRAVE.value

    async def test_switching_to_custom_with_a_url_is_accepted(self) -> None:
        catalog, name = await self._brave()

        updated = await catalog.update(
            name,
            base_url="https://self-hosted.example.test",
            metadata={METADATA_KEY_VENDOR: HttpVendor.CUSTOM.value},
        )

        assert updated.base_url == "https://self-hosted.example.test"

    async def test_switching_vendor_repoints_even_when_base_url_is_omitted(
        self,
    ) -> None:
        # A client that omits the key rather than nulling it must not leave
        # the previous vendor's endpoint in place either: the endpoint is
        # derived from the vendor, so a vendor change always re-derives it.
        catalog, name = await self._brave()

        updated = await catalog.update(
            name,
            metadata={METADATA_KEY_VENDOR: HttpVendor.EXA.value},
        )

        assert updated.base_url == HTTP_VENDOR_PRESETS[HttpVendor.EXA].base_url
