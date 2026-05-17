"""Tests for dynamic SSRF allowlist integration in ProviderManagementService."""

from unittest.mock import AsyncMock, patch

import pytest

from synthorg.api.dto_providers import CreateFromPresetRequest, UpdateProviderRequest
from synthorg.config.schema import ProviderModelConfig
from synthorg.providers.discovery_policy import ProviderDiscoveryPolicy
from synthorg.providers.management.service import ProviderManagementService
from tests.unit.api.fakes import FakePersistenceBackend

from .conftest import make_create_request

pytestmark = pytest.mark.unit
# ── Helpers ──────────────────────────────────────────────────────


async def _read_persisted_policy(
    service: ProviderManagementService,
) -> ProviderDiscoveryPolicy:
    """Read the current discovery policy from the service."""
    return await service.get_discovery_policy()


# ── Provider create adds to allowlist ────────────────────────────


class TestCreateProviderAllowlist:
    """Creating a provider updates the discovery allowlist."""

    async def test_create_adds_host_port(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Provider with base_url adds host:port to allowlist."""
        await service.create_provider(
            make_create_request(base_url="http://my-server:9090/v1"),
        )
        policy = await _read_persisted_policy(service)
        assert "my-server:9090" in policy.host_port_allowlist

    async def test_create_without_base_url_no_change(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Provider with no base_url does not modify allowlist."""
        policy_before = await _read_persisted_policy(service)
        await service.create_provider(
            make_create_request(base_url=None),
        )
        policy_after = await _read_persisted_policy(service)
        assert policy_before.host_port_allowlist == policy_after.host_port_allowlist

    async def test_create_preset_url_already_seeded(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Creating with a preset URL does not duplicate the entry."""
        await service.create_provider(
            make_create_request(base_url="http://localhost:11434"),
        )
        policy = await _read_persisted_policy(service)
        count = policy.host_port_allowlist.count("localhost:11434")
        assert count == 1


# ── Provider delete removes from allowlist ───────────────────────


class TestDeleteProviderAllowlist:
    """Deleting a provider updates the discovery allowlist."""

    async def test_delete_removes_host_port(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Deleting the only user of a host:port removes it."""
        await service.create_provider(
            make_create_request(base_url="http://unique-host:9090"),
        )
        policy = await _read_persisted_policy(service)
        assert "unique-host:9090" in policy.host_port_allowlist

        await service.delete_provider("test-provider")
        policy = await _read_persisted_policy(service)
        assert "unique-host:9090" not in policy.host_port_allowlist

    async def test_delete_preserves_shared_host_port(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Deleting one provider keeps host:port if another uses it."""
        await service.create_provider(
            make_create_request(
                name="provider-a",
                base_url="http://shared-host:9090",
                models=(ProviderModelConfig(id="model-a"),),
            ),
        )
        await service.create_provider(
            make_create_request(
                name="provider-b",
                base_url="http://shared-host:9090",
                models=(ProviderModelConfig(id="model-b"),),
            ),
        )

        await service.delete_provider("provider-a")
        policy = await _read_persisted_policy(service)
        assert "shared-host:9090" in policy.host_port_allowlist


# ── Provider update modifies allowlist ───────────────────────────


class TestUpdateProviderAllowlist:
    """Updating a provider's base_url updates the allowlist."""

    async def test_update_base_url_adds_new_removes_old(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Changing base_url removes old host:port, adds new one."""
        await service.create_provider(
            make_create_request(base_url="http://old-host:9090"),
        )
        await service.update_provider(
            "test-provider",
            UpdateProviderRequest(base_url="http://new-host:8080"),
        )
        policy = await _read_persisted_policy(service)
        assert "new-host:8080" in policy.host_port_allowlist
        assert "old-host:9090" not in policy.host_port_allowlist

    async def test_update_non_url_field_no_change(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Updating non-base_url fields does not touch allowlist."""
        await service.create_provider(
            make_create_request(base_url="http://my-host:9090"),
        )
        policy_before = await _read_persisted_policy(service)

        await service.update_provider(
            "test-provider",
            UpdateProviderRequest(
                models=(ProviderModelConfig(id="new-model"),),
            ),
        )
        policy_after = await _read_persisted_policy(service)
        assert policy_before.host_port_allowlist == policy_after.host_port_allowlist


# ── Discovery trust uses allowlist ───────────────────────────────


class TestDiscoveryTrustViaAllowlist:
    """Trust resolution now uses the allowlist, not _resolve_discovery_trust."""

    async def test_allowlisted_url_is_trusted(
        self,
        service: ProviderManagementService,
    ) -> None:
        """URL whose host:port is in the allowlist gets trust_url=True."""
        await service.create_provider(
            make_create_request(base_url="http://localhost:11434"),
        )
        with patch(
            "synthorg.providers.management.service.discover_models",
            new_callable=AsyncMock,
            return_value=(),
        ) as mock_discover:
            await service.discover_models_for_provider("test-provider")

        mock_discover.assert_awaited_once()
        assert mock_discover.call_args.kwargs["trust_url"] is True

    async def test_non_allowlisted_url_not_trusted(
        self,
        service: ProviderManagementService,
    ) -> None:
        """URL whose host:port is NOT in allowlist gets trust_url=False."""
        await service.create_provider(
            make_create_request(base_url="http://evil.example.com:9999"),
        )
        # Manually remove the entry that was auto-added by create
        await service.remove_custom_allowlist_entry("evil.example.com:9999")

        with patch(
            "synthorg.providers.management.service.discover_models",
            new_callable=AsyncMock,
            return_value=(),
        ) as mock_discover:
            await service.discover_models_for_provider("test-provider")

        mock_discover.assert_awaited_once()
        assert mock_discover.call_args.kwargs["trust_url"] is False


# ── Custom allowlist entry API ───────────────────────────────────


class TestCustomAllowlistEntries:
    """Public API for adding/removing custom allowlist entries."""

    async def test_add_custom_entry(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Adding a custom entry persists and reflects in policy."""
        policy = await service.add_custom_allowlist_entry("custom-server:7070")
        assert "custom-server:7070" in policy.host_port_allowlist
        # Verify persistence
        reloaded = await _read_persisted_policy(service)
        assert "custom-server:7070" in reloaded.host_port_allowlist

    async def test_remove_custom_entry(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Removing a custom entry persists."""
        await service.add_custom_allowlist_entry("temp-server:6060")
        policy = await service.remove_custom_allowlist_entry("temp-server:6060")
        assert "temp-server:6060" not in policy.host_port_allowlist

    async def test_add_duplicate_is_idempotent(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Adding the same entry twice does not duplicate."""
        await service.add_custom_allowlist_entry("my-host:8080")
        policy = await service.add_custom_allowlist_entry("my-host:8080")
        assert policy.host_port_allowlist.count("my-host:8080") == 1

    async def test_remove_nonexistent_is_noop(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Removing a nonexistent entry does not raise."""
        policy = await service.remove_custom_allowlist_entry("no-such:1234")
        assert "no-such:1234" not in policy.host_port_allowlist

    async def test_get_discovery_policy_returns_current(
        self,
        service: ProviderManagementService,
    ) -> None:
        """get_discovery_policy returns the current allowlist state."""
        await service.add_custom_allowlist_entry("read-test:5050")
        policy = await service.get_discovery_policy()
        assert "read-test:5050" in policy.host_port_allowlist


# ── Seed includes preset entries ─────────────────────────────────


class TestAllowlistSeeding:
    """The initial allowlist is seeded from preset candidate URLs."""

    async def test_seed_includes_ollama_preset(
        self,
        service: ProviderManagementService,
    ) -> None:
        """First load seeds allowlist with preset candidate URLs."""
        policy = await _read_persisted_policy(service)
        assert "localhost:11434" in policy.host_port_allowlist
        assert "host.docker.internal:11434" in policy.host_port_allowlist
        assert "172.17.0.1:11434" in policy.host_port_allowlist

    async def test_seed_includes_lm_studio_preset(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Seeded allowlist includes LM Studio preset candidate URLs."""
        policy = await _read_persisted_policy(service)
        assert "localhost:1234" in policy.host_port_allowlist

    async def test_seed_includes_vllm_preset(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Seeded allowlist includes vLLM preset candidate URLs."""
        policy = await _read_persisted_policy(service)
        assert "localhost:8000" in policy.host_port_allowlist


# ── create_from_preset trust ─────────────────────────────────────


class TestCreateFromPresetAllowlistTrust:
    """Preset creation uses the allowlist for trust decisions."""

    async def test_preset_default_url_is_trusted(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Preset default URL is in the seeded allowlist, so trust_url=True."""
        with (
            patch(
                "synthorg.providers.management.service.models_from_litellm",
                return_value=(),
            ),
            patch(
                "synthorg.providers.management.service.discover_models",
                new_callable=AsyncMock,
                return_value=(),
            ) as mock_discover,
        ):
            request = CreateFromPresetRequest(
                preset_name="ollama",
                name="test-preset-provider",
            )
            await service.create_from_preset(request)

        mock_discover.assert_awaited_once()
        assert mock_discover.call_args.kwargs["trust_url"] is True

    async def test_preset_custom_url_not_in_allowlist(
        self,
        service: ProviderManagementService,
    ) -> None:
        """User-supplied base_url not in allowlist gets trust_url=False."""
        with (
            patch(
                "synthorg.providers.management.service.models_from_litellm",
                return_value=(),
            ),
            patch(
                "synthorg.providers.management.service.discover_models",
                new_callable=AsyncMock,
                return_value=(),
            ) as mock_discover,
        ):
            request = CreateFromPresetRequest(
                preset_name="ollama",
                name="test-preset-provider",
                base_url="http://custom-host:11434",
            )
            await service.create_from_preset(request)

        mock_discover.assert_awaited_once()
        assert mock_discover.call_args.kwargs["trust_url"] is False


# ── Edge cases ───────────────────────────────────────────────


class TestAllowlistEdgeCases:
    """Edge cases and error recovery."""

    async def test_corrupted_policy_reseeds(
        self,
        service: ProviderManagementService,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        """Corrupted persisted policy triggers re-seed."""
        from datetime import UTC, datetime

        # Write corrupted JSON directly to the raw repository,
        # bypassing SettingsService validation.
        repo = fake_persistence.settings
        repo._store[("providers", "discovery_allowlist")] = (
            "not-valid-json",
            datetime.now(tz=UTC).isoformat(),
        )
        policy = await service.get_discovery_policy()
        # Re-seeded: should contain preset entries
        assert "localhost:11434" in policy.host_port_allowlist

    async def test_invalid_schema_reseeds(
        self,
        service: ProviderManagementService,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        """Valid JSON with invalid schema triggers re-seed."""
        from datetime import UTC, datetime

        repo = fake_persistence.settings
        repo._store[("providers", "discovery_allowlist")] = (
            '{"block_private_ips": "not-a-bool"}',
            datetime.now(tz=UTC).isoformat(),
        )
        policy = await service.get_discovery_policy()
        assert "localhost:11434" in policy.host_port_allowlist

    async def test_update_from_none_to_url(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Updating provider from no base_url to a URL adds to allowlist."""
        await service.create_provider(
            make_create_request(base_url=None),
        )
        await service.update_provider(
            "test-provider",
            UpdateProviderRequest(base_url="http://new-host:7070"),
        )
        policy = await _read_persisted_policy(service)
        assert "new-host:7070" in policy.host_port_allowlist

    async def test_delete_provider_without_base_url(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Deleting a provider with no base_url does not modify allowlist."""
        policy_before = await _read_persisted_policy(service)
        await service.create_provider(
            make_create_request(base_url=None),
        )
        await service.delete_provider("test-provider")
        policy_after = await _read_persisted_policy(service)
        assert policy_before.host_port_allowlist == policy_after.host_port_allowlist

    async def test_add_custom_entry_normalizes_case(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Custom entry is normalized to lowercase."""
        policy = await service.add_custom_allowlist_entry("MY-HOST:8080")
        assert "my-host:8080" in policy.host_port_allowlist
        assert "MY-HOST:8080" not in policy.host_port_allowlist
