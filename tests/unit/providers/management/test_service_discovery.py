"""Tests for provider discovery and trust resolution."""

from unittest.mock import AsyncMock, patch

import pytest

from synthorg.api.dto_providers import CreateFromPresetRequest, UpdateProviderRequest
from synthorg.config.schema import ProviderModelConfig
from synthorg.providers.enums import AuthType
from synthorg.providers.errors import ProviderNotFoundError
from synthorg.providers.management.service import ProviderManagementService
from synthorg.providers.presets import LocalPreset
from synthorg.providers.url_utils import redact_url

from .conftest import make_create_request

pytestmark = pytest.mark.unit
# Mirrors the registered ``api.server_port`` default.
_BACKEND_PORT = 3001


class TestDiscoverModelsForProvider:
    """Tests for discover_models_for_provider."""

    async def test_discover_models_updates_provider(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Discovery with results updates the provider config."""
        await service.create_provider(
            make_create_request(
                base_url="http://localhost:11434",
            ),
        )
        discovered = (
            ProviderModelConfig(id="test-model-a"),
            ProviderModelConfig(id="test-model-b"),
        )
        with patch(
            "synthorg.providers.management.service.discover_models",
            new_callable=AsyncMock,
            return_value=discovered,
        ):
            result = await service.discover_models_for_provider(
                "test-provider",
            )

        assert result == discovered
        # Verify the provider was updated with discovered models.
        updated = await service.get_provider("test-provider")
        assert updated.models == discovered

    async def test_discover_models_no_base_url_returns_empty(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Provider with no base_url returns empty tuple without discovery."""
        await service.create_provider(
            make_create_request(base_url=None),
        )
        result = await service.discover_models_for_provider(
            "test-provider",
        )
        assert result == ()

    async def test_discover_models_provider_not_found_raises(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Non-existent provider name raises ProviderNotFoundError."""
        with pytest.raises(ProviderNotFoundError, match="not found"):
            await service.discover_models_for_provider("nonexistent")

    async def test_discover_models_empty_result_no_update(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Empty discovery result does not call update_provider."""
        await service.create_provider(
            make_create_request(
                base_url="http://localhost:11434",
            ),
        )
        with patch(
            "synthorg.providers.management.service.discover_models",
            new_callable=AsyncMock,
            return_value=(),
        ) as mock_discover:
            result = await service.discover_models_for_provider(
                "test-provider",
            )

        assert result == ()
        mock_discover.assert_awaited_once()
        # Original models should remain unchanged.
        original = await service.get_provider("test-provider")
        assert original.models == (
            ProviderModelConfig(
                id="test-model-001",
                alias="medium",
            ),
        )


class TestCreateFromPresetAutoDiscovery:
    """Tests for auto-discovery in create_from_preset."""

    async def test_create_from_preset_auto_discovers_models(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Preset with auth_type=none, empty models, and base_url triggers discovery."""
        discovered = (
            ProviderModelConfig(id="test-model-x"),
            ProviderModelConfig(id="test-model-y"),
        )
        with (
            patch(
                "synthorg.providers.management.service.models_from_litellm",
                return_value=(),
            ),
            patch(
                "synthorg.providers.management.service.discover_models",
                new_callable=AsyncMock,
                return_value=discovered,
            ) as mock_discover,
        ):
            request = CreateFromPresetRequest(
                preset_name="ollama",
                name="my-ollama",
            )
            result = await service.create_from_preset(request)

        mock_discover.assert_awaited_once()
        call_kwargs = mock_discover.call_args
        assert call_kwargs.kwargs["trust_url"] is True
        assert result.models == discovered

    async def test_create_from_preset_user_base_url_not_trusted(
        self,
        service: ProviderManagementService,
    ) -> None:
        """User-supplied base_url not in seeded allowlist is NOT trusted."""
        discovered = (ProviderModelConfig(id="test-model-z"),)
        with (
            patch(
                "synthorg.providers.management.service.models_from_litellm",
                return_value=(),
            ),
            patch(
                "synthorg.providers.management.service.discover_models",
                new_callable=AsyncMock,
                return_value=discovered,
            ) as mock_discover,
        ):
            request = CreateFromPresetRequest(
                preset_name="ollama",
                name="my-ollama",
                base_url="http://custom-host:11434",
            )
            result = await service.create_from_preset(request)

        mock_discover.assert_awaited_once()
        call_kwargs = mock_discover.call_args
        assert call_kwargs.kwargs["trust_url"] is False
        assert result.models == discovered


class TestDiscoverModelsForProviderTrust:
    """Parametrized tests for trust logic in discover_models_for_provider.

    Trust is now determined by the dynamic SSRF allowlist rather than
    preset-hint matching.  Creating a provider auto-adds its host:port
    to the allowlist, so all installed providers' URLs are trusted.
    Preset candidate URLs are seeded at startup.
    """

    @pytest.mark.parametrize(
        ("base_url", "preset_hint", "expected_trust"),
        [
            pytest.param(
                "http://localhost:11434",
                "ollama",
                True,
                id="preset-url-in-seeded-allowlist",
            ),
            pytest.param(
                "http://localhost:11434",
                "fake",
                True,
                id="preset-hint-irrelevant-url-in-allowlist",
            ),
            pytest.param(
                "http://localhost:9999",
                None,
                True,
                id="auto-added-by-create",
            ),
            pytest.param(
                "http://localhost:11434",
                None,
                True,
                id="seeded-preset-url",
            ),
            pytest.param(
                "http://host.docker.internal:11434",
                None,
                True,
                id="docker-internal-in-seeded-allowlist",
            ),
            pytest.param(
                "http://evil.example.com:11434",
                None,
                True,
                id="auto-added-on-create",
            ),
            pytest.param(
                "http://evil.example.com:11434",
                "ollama",
                True,
                id="auto-added-preset-hint-irrelevant",
            ),
        ],
    )
    async def test_trust_resolution(
        self,
        service: ProviderManagementService,
        base_url: str,
        preset_hint: str | None,
        expected_trust: bool,
    ) -> None:
        """All installed provider URLs are trusted via the allowlist."""
        await service.create_provider(
            make_create_request(base_url=base_url),
        )
        kwargs: dict[str, str] = {}
        if preset_hint is not None:
            kwargs["preset_hint"] = preset_hint

        with patch(
            "synthorg.providers.management.service.discover_models",
            new_callable=AsyncMock,
            return_value=(),
        ) as mock_discover:
            await service.discover_models_for_provider(
                "test-provider",
                **kwargs,
            )

        mock_discover.assert_awaited_once()
        call_kwargs = mock_discover.call_args
        assert call_kwargs.kwargs["trust_url"] is expected_trust


class TestApplyDiscoveredModelsTOCTOU:
    """Tests for TOCTOU abort paths in _apply_discovered_models."""

    async def test_discover_aborts_if_provider_deleted(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Discovery aborts if provider is deleted between read and apply."""
        await service.create_provider(
            make_create_request(
                base_url="http://localhost:11434",
            ),
        )

        async def discover_then_delete(
            *_args: object,
            **_kwargs: object,
        ) -> tuple[ProviderModelConfig, ...]:
            await service.delete_provider("test-provider")
            return (ProviderModelConfig(id="test-discovered"),)

        with patch(
            "synthorg.providers.management.service.discover_models",
            side_effect=discover_then_delete,
        ):
            result = await service.discover_models_for_provider(
                "test-provider",
            )

        # _apply_discovered_models returns False -> method returns ()
        assert result == ()

    async def test_discover_aborts_if_base_url_changed(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Discovery aborts if base_url changes between read and apply."""
        await service.create_provider(
            make_create_request(
                base_url="http://localhost:11434",
            ),
        )

        async def discover_then_change_url(
            *_args: object,
            **_kwargs: object,
        ) -> tuple[ProviderModelConfig, ...]:
            await service.update_provider(
                "test-provider",
                UpdateProviderRequest(
                    base_url="http://evil.example.com:9999",
                ),
            )
            return (ProviderModelConfig(id="test-discovered"),)

        with patch(
            "synthorg.providers.management.service.discover_models",
            side_effect=discover_then_change_url,
        ):
            result = await service.discover_models_for_provider(
                "test-provider",
            )

        # _apply_discovered_models returns False -> method returns ()
        assert result == ()
        # Provider should retain its changed URL, not the discovered models
        provider = await service.get_provider("test-provider")
        assert provider.base_url == "http://evil.example.com:9999"
        assert provider.models == (
            ProviderModelConfig(id="test-model-001", alias="medium"),
        )


class TestSelfConnectionGuard:
    """Tests for the self-connection guard in trust resolution.

    The guard rejects URLs that point at the backend itself, even
    when the URL matches a preset's candidate_urls.  The port is
    derived from ``_BACKEND_PORT`` (the ``ServerConfig`` default)
    so tests track port changes automatically.
    """

    @pytest.mark.parametrize(
        ("base_url", "expected_trust"),
        [
            pytest.param(
                f"http://localhost:{_BACKEND_PORT}/v1",
                False,
                id="localhost-backend-port-rejected",
            ),
            pytest.param(
                f"http://127.0.0.1:{_BACKEND_PORT}/v1",
                False,
                id="loopback-backend-port-rejected",
            ),
            pytest.param(
                f"http://host.docker.internal:{_BACKEND_PORT}/v1",
                False,
                id="docker-internal-backend-port-rejected",
            ),
            pytest.param(
                f"http://172.17.0.1:{_BACKEND_PORT}/v1",
                False,
                id="docker-bridge-backend-port-rejected",
            ),
            pytest.param(
                f"http://example-provider.example.com:{_BACKEND_PORT}/v1",
                True,
                id="remote-host-same-port-allowed",
            ),
        ],
    )
    async def test_self_connection_detection(
        self,
        service: ProviderManagementService,
        base_url: str,
        *,
        expected_trust: bool,
    ) -> None:
        """Self-connection URLs are blocked before discovery; others proceed."""
        fake_preset = LocalPreset(
            name="test-local",
            display_name="Test Local",
            description="Fake preset for self-connection guard tests",
            driver="litellm",
            litellm_provider="test-local",
            auth_type=AuthType.NONE,
            candidate_urls=(base_url,),
        )
        await service.create_provider(
            make_create_request(base_url=base_url),
        )

        with (
            patch(
                "synthorg.providers.management.service.get_preset",
                return_value=fake_preset,
            ),
            patch(
                "synthorg.providers.management.service.discover_models",
                new_callable=AsyncMock,
                return_value=(),
            ) as mock_discover,
        ):
            result = await service.discover_models_for_provider(
                "test-provider",
                preset_hint="test-local",
            )

        if expected_trust:
            mock_discover.assert_awaited_once()
        else:
            mock_discover.assert_not_awaited()
            assert result == ()

    async def test_self_connection_via_default_base_url(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Self-connection guard blocks discovery for default_base_url too."""
        self_url = f"http://localhost:{_BACKEND_PORT}/v1"
        fake_preset = LocalPreset(
            name="test-local",
            display_name="Test Local",
            description="Fake preset with self-URL as default_base_url",
            driver="litellm",
            litellm_provider="test-local",
            auth_type=AuthType.NONE,
            default_base_url=self_url,
        )
        await service.create_provider(
            make_create_request(base_url=self_url),
        )

        with (
            patch(
                "synthorg.providers.management.service.get_preset",
                return_value=fake_preset,
            ),
            patch(
                "synthorg.providers.management.service.discover_models",
                new_callable=AsyncMock,
                return_value=(),
            ) as mock_discover,
        ):
            result = await service.discover_models_for_provider(
                "test-provider",
                preset_hint="test-local",
            )

        mock_discover.assert_not_awaited()
        assert result == ()

    async def test_self_connection_logs_warning(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Self-connection guard emits a warning log on rejection."""
        self_url = f"http://localhost:{_BACKEND_PORT}/v1"
        fake_preset = LocalPreset(
            name="test-local",
            display_name="Test Local",
            description="Fake preset for log assertion",
            driver="litellm",
            litellm_provider="test-local",
            auth_type=AuthType.NONE,
            candidate_urls=(self_url,),
        )
        await service.create_provider(
            make_create_request(base_url=self_url),
        )

        with (
            patch(
                "synthorg.providers.management.service.get_preset",
                return_value=fake_preset,
            ),
            patch(
                "synthorg.providers.management.service.discover_models",
                new_callable=AsyncMock,
                return_value=(),
            ),
            patch(
                "synthorg.providers.management.service.logger",
            ) as mock_logger,
        ):
            await service.discover_models_for_provider(
                "test-provider",
                preset_hint="test-local",
            )

        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        from synthorg.observability.events.provider import (
            PROVIDER_DISCOVERY_SELF_CONNECTION_BLOCKED,
        )

        assert call_args.args[0] == PROVIDER_DISCOVERY_SELF_CONNECTION_BLOCKED
        assert call_args.kwargs["url"] == redact_url(self_url)
        assert call_args.kwargs["backend_port"] == _BACKEND_PORT
