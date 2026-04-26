"""Unit tests for SettingsService."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict

from synthorg.persistence.settings_protocol import SettingsRepository
from synthorg.settings.encryption import SettingsEncryptor
from synthorg.settings.enums import (
    SettingNamespace,
    SettingSource,
    SettingType,
)
from synthorg.settings.errors import (
    SettingNotFoundError,
    SettingsEncryptionError,
    SettingValidationError,
)
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import SettingsRegistry
from synthorg.settings.service import SettingsService

# ── Fixtures ──────────────────────────────────────────────────────


class _BudgetConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    total_monthly: float = 100.0


class _FakeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    budget: _BudgetConfig = _BudgetConfig()


_UNSET = object()


def _make_definition(  # noqa: PLR0913
    *,
    namespace: SettingNamespace = SettingNamespace.BUDGET,
    key: str = "total_monthly",
    setting_type: SettingType = SettingType.FLOAT,
    default: str | None | object = _UNSET,
    yaml_path: str | None = "budget.total_monthly",
    sensitive: bool = False,
    restart_required: bool = False,
    enum_values: tuple[str, ...] = (),
    min_value: float | None = None,
    max_value: float | None = None,
    validator_pattern: str | None = None,
) -> SettingDefinition:
    # Only use the "100.0" default when type is FLOAT and no explicit
    # default was provided -- avoids model_validator rejecting mismatched
    # defaults (e.g. "100.0" for an ENUM type).
    resolved_default: str | None
    if default is _UNSET:
        resolved_default = "100.0" if setting_type == SettingType.FLOAT else None
    else:
        resolved_default = default  # type: ignore[assignment]
    return SettingDefinition(
        namespace=namespace,
        key=key,
        type=setting_type,
        default=resolved_default,
        description="test",
        group="test",
        yaml_path=yaml_path,
        sensitive=sensitive,
        restart_required=restart_required,
        enum_values=enum_values,
        min_value=min_value,
        max_value=max_value,
        validator_pattern=validator_pattern,
    )


@pytest.fixture
def registry() -> SettingsRegistry:
    r = SettingsRegistry()
    r.register(_make_definition())
    return r


@pytest.fixture
def mock_repo() -> AsyncMock:
    repo = AsyncMock(spec=SettingsRepository)
    repo.get = AsyncMock(return_value=None)
    repo.set = AsyncMock()
    repo.delete = AsyncMock(return_value=True)
    repo.get_namespace = AsyncMock(return_value=())
    repo.get_all = AsyncMock(return_value=())
    repo.delete_namespace = AsyncMock(return_value=0)
    repo.delete_namespace_returning_keys = AsyncMock(return_value=())
    return repo


@pytest.fixture
def config() -> _FakeConfig:
    return _FakeConfig()


@pytest.fixture
def service(
    mock_repo: AsyncMock, registry: SettingsRegistry, config: _FakeConfig
) -> SettingsService:
    return SettingsService(
        repository=mock_repo,
        registry=registry,
        config=config,
    )


# ── Resolution Order Tests ───────────────────────────────────────


@pytest.mark.unit
class TestResolutionOrder:
    """Tests for the DB > env > YAML > default resolution chain."""

    async def test_resolves_from_db(
        self, service: SettingsService, mock_repo: AsyncMock
    ) -> None:
        mock_repo.get.return_value = ("200.0", "2026-03-16T10:00:00Z")
        result = await service.get("budget", "total_monthly")
        assert result.value == "200.0"
        assert result.source == SettingSource.DATABASE
        assert result.updated_at == "2026-03-16T10:00:00Z"

    async def test_resolves_from_env(
        self,
        service: SettingsService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SYNTHORG_BUDGET_TOTAL_MONTHLY", "500.0")
        result = await service.get("budget", "total_monthly")
        assert result.value == "500.0"
        assert result.source == SettingSource.ENVIRONMENT

    async def test_resolves_from_yaml(self, service: SettingsService) -> None:
        result = await service.get("budget", "total_monthly")
        assert result.value == "100.0"
        assert result.source == SettingSource.YAML

    async def test_resolves_from_default(
        self,
        mock_repo: AsyncMock,
        config: _FakeConfig,
    ) -> None:
        registry = SettingsRegistry()
        registry.register(
            _make_definition(key="custom_key", yaml_path=None, default="42")
        )
        svc = SettingsService(repository=mock_repo, registry=registry, config=config)
        result = await svc.get("budget", "custom_key")
        assert result.value == "42"
        assert result.source == SettingSource.DEFAULT

    async def test_db_overrides_env(
        self,
        service: SettingsService,
        mock_repo: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SYNTHORG_BUDGET_TOTAL_MONTHLY", "500.0")
        mock_repo.get.return_value = ("200.0", "2026-03-16T10:00:00Z")
        result = await service.get("budget", "total_monthly")
        assert result.source == SettingSource.DATABASE

    async def test_env_overrides_yaml(
        self,
        service: SettingsService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SYNTHORG_BUDGET_TOTAL_MONTHLY", "500.0")
        result = await service.get("budget", "total_monthly")
        assert result.source == SettingSource.ENVIRONMENT

    async def test_unknown_setting_raises(self, service: SettingsService) -> None:
        with pytest.raises(SettingNotFoundError, match="Unknown setting"):
            await service.get("budget", "nonexistent")


# ── Cache Tests ──────────────────────────────────────────────────


@pytest.mark.unit
class TestCache:
    """Tests for cache behavior."""

    async def test_cache_hit(
        self, service: SettingsService, mock_repo: AsyncMock
    ) -> None:
        mock_repo.get.return_value = ("200.0", "2026-03-16T10:00:00Z")
        await service.get("budget", "total_monthly")
        await service.get("budget", "total_monthly")
        # Only one DB call -- second was cached
        assert mock_repo.get.call_count == 1

    async def test_cache_invalidated_on_set(
        self, service: SettingsService, mock_repo: AsyncMock
    ) -> None:
        mock_repo.get.return_value = ("200.0", "2026-03-16T10:00:00Z")
        await service.get("budget", "total_monthly")
        await service.set("budget", "total_monthly", "300.0")
        mock_repo.get.return_value = ("300.0", "2026-03-16T11:00:00Z")
        result = await service.get("budget", "total_monthly")
        assert result.value == "300.0"
        assert mock_repo.get.call_count == 2

    async def test_cache_invalidated_on_delete(
        self, service: SettingsService, mock_repo: AsyncMock
    ) -> None:
        mock_repo.get.return_value = ("200.0", "2026-03-16T10:00:00Z")
        await service.get("budget", "total_monthly")
        await service.delete("budget", "total_monthly")
        mock_repo.get.return_value = None
        result = await service.get("budget", "total_monthly")
        # Falls through to YAML after cache miss
        assert result.source == SettingSource.YAML
        assert mock_repo.get.call_count == 2


# ── Validation Tests ─────────────────────────────────────────────


@pytest.mark.unit
class TestValidation:
    """Tests for value validation on set()."""

    @pytest.mark.parametrize(
        ("key", "defn_kwargs", "bad_value", "match"),
        [
            ("total_monthly", {}, "not-a-number", "Expected float"),
            ("total_monthly", {"min_value": 0.0}, "-1.0", "below minimum"),
            ("total_monthly", {"max_value": 1000.0}, "9999.0", "above maximum"),
            (
                "strategy",
                {
                    "setting_type": SettingType.ENUM,
                    "enum_values": ("a", "b"),
                    "yaml_path": None,
                },
                "c",
                "Invalid enum",
            ),
            (
                "enabled",
                {
                    "setting_type": SettingType.BOOLEAN,
                    "yaml_path": None,
                },
                "maybe",
                "Expected boolean",
            ),
        ],
        ids=["non-float", "below-min", "above-max", "bad-enum", "bad-bool"],
    )
    async def test_rejects_invalid_value(  # noqa: PLR0913
        self,
        mock_repo: AsyncMock,
        config: _FakeConfig,
        key: str,
        defn_kwargs: dict[str, Any],
        bad_value: str,
        match: str,
    ) -> None:
        registry = SettingsRegistry()
        registry.register(_make_definition(key=key, **defn_kwargs))
        svc = SettingsService(
            repository=mock_repo,
            registry=registry,
            config=config,
        )
        with pytest.raises(SettingValidationError, match=match):
            await svc.set("budget", key, bad_value)

    async def test_accepts_valid_value(
        self, service: SettingsService, mock_repo: AsyncMock
    ) -> None:
        entry = await service.set("budget", "total_monthly", "200.0")
        assert entry.value == "200.0"
        assert entry.source == SettingSource.DATABASE
        mock_repo.set.assert_called_once()


# ── Sensitive Settings Tests ─────────────────────────────────────


@pytest.mark.unit
class TestSensitiveSettings:
    """Tests for encryption of sensitive settings."""

    async def test_sensitive_encrypted_on_write(
        self, mock_repo: AsyncMock, config: _FakeConfig
    ) -> None:
        enc = SettingsEncryptor(Fernet.generate_key())
        registry = SettingsRegistry()
        registry.register(
            _make_definition(
                key="api_key",
                setting_type=SettingType.STRING,
                sensitive=True,
                yaml_path=None,
            )
        )
        svc = SettingsService(
            repository=mock_repo,
            registry=registry,
            config=config,
            encryptor=enc,
        )
        await svc.set("budget", "api_key", "secret123")
        # The stored value should be encrypted, not plaintext
        call_args = mock_repo.set.call_args
        stored_value = call_args[0][2]
        assert stored_value != "secret123"
        assert enc.decrypt(stored_value) == "secret123"

    async def test_sensitive_decrypted_on_read(
        self, mock_repo: AsyncMock, config: _FakeConfig
    ) -> None:
        enc = SettingsEncryptor(Fernet.generate_key())
        registry = SettingsRegistry()
        registry.register(
            _make_definition(
                key="api_key",
                setting_type=SettingType.STRING,
                sensitive=True,
                yaml_path=None,
            )
        )
        svc = SettingsService(
            repository=mock_repo,
            registry=registry,
            config=config,
            encryptor=enc,
        )
        ciphertext = enc.encrypt("secret123")
        mock_repo.get.return_value = (ciphertext, "2026-03-16T10:00:00Z")
        result = await svc.get("budget", "api_key")
        assert result.value == "secret123"

    async def test_sensitive_masked_in_entry(
        self, mock_repo: AsyncMock, config: _FakeConfig
    ) -> None:
        enc = SettingsEncryptor(Fernet.generate_key())
        registry = SettingsRegistry()
        registry.register(
            _make_definition(
                key="api_key",
                setting_type=SettingType.STRING,
                sensitive=True,
                yaml_path=None,
            )
        )
        svc = SettingsService(
            repository=mock_repo,
            registry=registry,
            config=config,
            encryptor=enc,
        )
        ciphertext = enc.encrypt("secret123")
        mock_repo.get.return_value = (ciphertext, "2026-03-16T10:00:00Z")
        entry = await svc.get_entry("budget", "api_key")
        assert entry.value == "********"

    async def test_sensitive_rejects_without_encryptor(
        self, mock_repo: AsyncMock, config: _FakeConfig
    ) -> None:
        registry = SettingsRegistry()
        registry.register(
            _make_definition(
                key="api_key",
                setting_type=SettingType.STRING,
                sensitive=True,
                yaml_path=None,
            )
        )
        svc = SettingsService(
            repository=mock_repo,
            registry=registry,
            config=config,
            encryptor=None,
        )
        with pytest.raises(SettingsEncryptionError, match="without encryption"):
            await svc.set("budget", "api_key", "secret123")


# ── Notification Tests ───────────────────────────────────────────


@pytest.mark.unit
class TestNotifications:
    """Tests for change notification publishing."""

    async def test_publishes_on_set(
        self, mock_repo: AsyncMock, registry: SettingsRegistry, config: _FakeConfig
    ) -> None:
        bus = MagicMock()
        bus.is_running = True
        bus.publish = AsyncMock()
        svc = SettingsService(
            repository=mock_repo,
            registry=registry,
            config=config,
            message_bus=bus,
        )
        await svc.set("budget", "total_monthly", "200.0")
        bus.publish.assert_called_once()
        msg = bus.publish.call_args[0][0]
        assert msg.channel == "#settings"
        assert "total_monthly" in msg.text

    async def test_publishes_on_delete(
        self, mock_repo: AsyncMock, registry: SettingsRegistry, config: _FakeConfig
    ) -> None:
        bus = MagicMock()
        bus.is_running = True
        bus.publish = AsyncMock()
        svc = SettingsService(
            repository=mock_repo,
            registry=registry,
            config=config,
            message_bus=bus,
        )
        await svc.delete("budget", "total_monthly")
        bus.publish.assert_called_once()

    async def test_no_publish_without_bus(self, service: SettingsService) -> None:
        """Set should succeed even without message bus."""
        entry = await service.set("budget", "total_monthly", "200.0")
        assert entry.value == "200.0"


# ── delete_namespace Tests ───────────────────────────────────────


@pytest.mark.unit
class TestDeleteNamespace:
    """Tests for SettingsService.delete_namespace."""

    async def test_returns_repository_count(
        self, service: SettingsService, mock_repo: AsyncMock
    ) -> None:
        """The deleted-row count is forwarded from the repository."""
        mock_repo.delete_namespace_returning_keys.return_value = (
            "total_monthly",
            "another_a",
            "another_b",
        )
        deleted = await service.delete_namespace("budget")
        assert deleted == 3
        mock_repo.delete_namespace_returning_keys.assert_awaited_once()
        # NotBlankStr coercion: assert via positional arg name
        called_with = mock_repo.delete_namespace_returning_keys.call_args[0][0]
        assert str(called_with) == "budget"

    async def test_invalidates_namespace_cache(
        self, service: SettingsService, mock_repo: AsyncMock
    ) -> None:
        """Every cached entry under the namespace is dropped."""
        mock_repo.get.return_value = ("100.0", "2026-04-25T10:00:00Z")
        await service.get("budget", "total_monthly")
        assert mock_repo.get.call_count == 1

        mock_repo.delete_namespace_returning_keys.return_value = ("total_monthly",)
        await service.delete_namespace("budget")

        # Subsequent get() must re-query the repo (not hit the cache)
        mock_repo.get.return_value = None
        result = await service.get("budget", "total_monthly")
        assert result.source == SettingSource.YAML
        assert mock_repo.get.call_count == 2

    async def test_publishes_only_for_keys_with_overrides_removed(
        self, mock_repo: AsyncMock, registry: SettingsRegistry, config: _FakeConfig
    ) -> None:
        """Publish only fires for keys whose DB override was actually cleared.

        Pins the round-12 fix: keys that have no DB override (defaults
        / env-only) must NOT republish on a namespace delete -- that
        would trigger phantom reload work for every registered key in
        the namespace even when only a single override row was
        cleared.
        """
        registry.register(
            _make_definition(
                key="another_key",
                yaml_path="budget.another_key",
            )
        )
        bus = MagicMock()
        bus.is_running = True
        bus.publish = AsyncMock()
        svc = SettingsService(
            repository=mock_repo,
            registry=registry,
            config=config,
            message_bus=bus,
        )
        # Only one of the two registered keys has a DB override.
        # The second registered key (``another_key``) has no override
        # row; the publish loop must skip it.
        mock_repo.delete_namespace_returning_keys.return_value = ("total_monthly",)

        deleted = await svc.delete_namespace("budget")

        assert deleted == 1
        # Only ``total_monthly`` had a DB override removed; the other
        # registered definition stays silent.
        assert bus.publish.call_count == 1

    async def test_emits_audit_event(
        self, service: SettingsService, mock_repo: AsyncMock
    ) -> None:
        """The namespace delete fires SETTINGS_VALUE_DELETED with count."""
        import structlog

        from synthorg.observability.events.settings import SETTINGS_VALUE_DELETED

        mock_repo.delete_namespace_returning_keys.return_value = (
            "k1",
            "k2",
            "k3",
            "k4",
        )
        with structlog.testing.capture_logs() as logs:
            await service.delete_namespace("budget")

        events = [log for log in logs if log["event"] == SETTINGS_VALUE_DELETED]
        assert len(events) == 1, f"expected one event, got {logs}"
        assert events[0]["namespace"] == "budget"
        assert events[0]["count"] == 4

    async def test_no_op_when_zero_rows_deleted(
        self, mock_repo: AsyncMock, registry: SettingsRegistry, config: _FakeConfig
    ) -> None:
        """Zero-row delete: cache invalidates, no audit, no publish.

        A namespace-clear that touched no rows is a no-op as far as
        downstream subscribers are concerned; emitting
        ``SETTINGS_VALUE_DELETED`` and per-key change notifications
        would trigger phantom reload/restart work.
        """
        import structlog

        from synthorg.observability.events.settings import SETTINGS_VALUE_DELETED

        bus = MagicMock()
        bus.is_running = True
        bus.publish = AsyncMock()
        svc = SettingsService(
            repository=mock_repo,
            registry=registry,
            config=config,
            message_bus=bus,
        )
        mock_repo.delete_namespace_returning_keys.return_value = ()

        with structlog.testing.capture_logs() as logs:
            deleted = await svc.delete_namespace("budget")

        assert deleted == 0
        assert not any(log["event"] == SETTINGS_VALUE_DELETED for log in logs)
        bus.publish.assert_not_called()


# ── Schema Tests ─────────────────────────────────────────────────


@pytest.mark.unit
class TestSchema:
    """Tests for schema introspection."""

    def test_get_schema_all(self, service: SettingsService) -> None:
        schema = service.get_schema()
        assert len(schema) == 1
        assert schema[0].key == "total_monthly"

    def test_get_schema_namespace(self, service: SettingsService) -> None:
        schema = service.get_schema(namespace="budget")
        assert len(schema) == 1

    def test_get_schema_empty_namespace(self, service: SettingsService) -> None:
        schema = service.get_schema(namespace="nonexistent")
        assert schema == ()


# ── Bulk Operations Tests ────────────────────────────────────────


@pytest.mark.unit
class TestBulkOperations:
    """Tests for get_all and get_namespace batch methods."""

    async def test_get_namespace_returns_entries(
        self, service: SettingsService, mock_repo: AsyncMock
    ) -> None:
        mock_repo.get_namespace.return_value = (
            ("total_monthly", "200.0", "2026-03-16T10:00:00Z"),
        )
        entries = await service.get_namespace("budget")
        assert len(entries) == 1
        assert entries[0].definition.key == "total_monthly"
        assert entries[0].value == "200.0"
        assert entries[0].source == SettingSource.DATABASE

    async def test_get_namespace_falls_back_to_default(
        self, service: SettingsService, mock_repo: AsyncMock
    ) -> None:
        mock_repo.get_namespace.return_value = ()
        entries = await service.get_namespace("budget")
        assert len(entries) == 1
        # Falls to YAML since config has budget.total_monthly=100.0
        assert entries[0].source == SettingSource.YAML

    async def test_get_all_returns_entries(
        self, service: SettingsService, mock_repo: AsyncMock
    ) -> None:
        mock_repo.get_all.return_value = (
            ("budget", "total_monthly", "300.0", "2026-03-16T10:00:00Z"),
        )
        entries = await service.get_all()
        assert len(entries) == 1
        assert entries[0].value == "300.0"

    async def test_get_all_uses_batch_method(
        self, service: SettingsService, mock_repo: AsyncMock
    ) -> None:
        """get_all should call repository.get_all, not individual gets."""
        mock_repo.get_all.return_value = ()
        await service.get_all()
        mock_repo.get_all.assert_called_once()
        # Should NOT call individual get()
        mock_repo.get.assert_not_called()


# ── Sensitive Read Without Encryptor ─────────────────────────────


@pytest.mark.unit
class TestSensitiveReadWithoutEncryptor:
    """Test that sensitive DB values are not leaked when encryptor is absent."""

    async def test_sensitive_not_cached(
        self, mock_repo: AsyncMock, config: _FakeConfig
    ) -> None:
        """Sensitive values should not be stored in the cache."""
        enc = SettingsEncryptor(Fernet.generate_key())
        registry = SettingsRegistry()
        registry.register(
            _make_definition(
                key="api_key",
                setting_type=SettingType.STRING,
                sensitive=True,
                yaml_path=None,
            )
        )
        svc = SettingsService(
            repository=mock_repo,
            registry=registry,
            config=config,
            encryptor=enc,
        )
        ciphertext = enc.encrypt("secret123")
        mock_repo.get.return_value = (ciphertext, "2026-03-16T10:00:00Z")
        await svc.get("budget", "api_key")
        # Second call should hit DB again (not cached)
        await svc.get("budget", "api_key")
        assert mock_repo.get.call_count == 2


# ── Notification Exception Handling ──────────────────────────────


@pytest.mark.unit
class TestNotificationExceptionHandling:
    """Test that bus.publish exceptions don't break setting writes."""

    async def test_set_succeeds_when_bus_publish_raises(
        self, mock_repo: AsyncMock, registry: SettingsRegistry, config: _FakeConfig
    ) -> None:
        bus = MagicMock()
        bus.is_running = True
        bus.publish = AsyncMock(side_effect=RuntimeError("bus broken"))
        svc = SettingsService(
            repository=mock_repo,
            registry=registry,
            config=config,
            message_bus=bus,
        )
        # Should NOT raise despite bus failure
        entry = await svc.set("budget", "total_monthly", "200.0")
        assert entry.value == "200.0"

    async def test_skips_publish_when_bus_not_running(
        self, mock_repo: AsyncMock, registry: SettingsRegistry, config: _FakeConfig
    ) -> None:
        bus = MagicMock()
        bus.is_running = False
        bus.publish = AsyncMock()
        svc = SettingsService(
            repository=mock_repo,
            registry=registry,
            config=config,
            message_bus=bus,
        )
        await svc.set("budget", "total_monthly", "200.0")
        bus.publish.assert_not_called()


# ── Additional Validation Tests ──────────────────────────────────


@pytest.mark.unit
class TestAdditionalValidation:
    """Tests for INTEGER, JSON, and validator_pattern paths."""

    async def test_rejects_float_as_integer(
        self, mock_repo: AsyncMock, config: _FakeConfig
    ) -> None:
        registry = SettingsRegistry()
        registry.register(
            _make_definition(
                key="count",
                setting_type=SettingType.INTEGER,
                yaml_path=None,
            )
        )
        svc = SettingsService(repository=mock_repo, registry=registry, config=config)
        with pytest.raises(SettingValidationError, match="Expected integer"):
            await svc.set("budget", "count", "3.5")

    async def test_rejects_invalid_json(
        self, mock_repo: AsyncMock, config: _FakeConfig
    ) -> None:
        registry = SettingsRegistry()
        registry.register(
            _make_definition(
                key="data",
                setting_type=SettingType.JSON,
                yaml_path=None,
            )
        )
        svc = SettingsService(repository=mock_repo, registry=registry, config=config)
        with pytest.raises(SettingValidationError, match="Invalid JSON"):
            await svc.set("budget", "data", "not json")

    async def test_accepts_valid_json(
        self, mock_repo: AsyncMock, config: _FakeConfig
    ) -> None:
        registry = SettingsRegistry()
        registry.register(
            _make_definition(
                key="data",
                setting_type=SettingType.JSON,
                yaml_path=None,
            )
        )
        svc = SettingsService(repository=mock_repo, registry=registry, config=config)
        entry = await svc.set("budget", "data", '{"a": 1}')
        assert entry.value == '{"a": 1}'

    async def test_sensitive_value_masked_in_validation_error(
        self, mock_repo: AsyncMock, config: _FakeConfig
    ) -> None:
        registry = SettingsRegistry()
        registry.register(
            _make_definition(
                key="secret",
                setting_type=SettingType.INTEGER,
                sensitive=True,
                yaml_path=None,
            )
        )
        svc = SettingsService(
            repository=mock_repo,
            registry=registry,
            config=config,
            encryptor=SettingsEncryptor(Fernet.generate_key()),
        )
        with pytest.raises(SettingValidationError) as exc_info:
            await svc.set("budget", "secret", "my-secret-value")
        # The actual secret must NOT appear in the error message
        assert "my-secret-value" not in str(exc_info.value)
        assert "********" in str(exc_info.value)


# ── Ciphertext Leak Guard Tests ─────────────────────────────────


@pytest.mark.unit
class TestCiphertextLeakGuard:
    """Verify sensitive reads raise when encryptor is absent."""

    async def test_get_raises_without_encryptor(
        self, mock_repo: AsyncMock, config: _FakeConfig
    ) -> None:
        registry = SettingsRegistry()
        registry.register(
            _make_definition(
                key="api_key",
                setting_type=SettingType.STRING,
                sensitive=True,
                yaml_path=None,
            )
        )
        svc = SettingsService(
            repository=mock_repo,
            registry=registry,
            config=config,
            encryptor=None,
        )
        mock_repo.get.return_value = ("ciphertext", "2026-01-01T00:00:00Z")
        with pytest.raises(SettingsEncryptionError, match="no encryptor"):
            await svc.get("budget", "api_key")

    async def test_batch_masks_when_encryptor_absent(
        self, mock_repo: AsyncMock, config: _FakeConfig
    ) -> None:
        enc = SettingsEncryptor(Fernet.generate_key())
        registry = SettingsRegistry()
        registry.register(
            _make_definition(
                key="api_key",
                setting_type=SettingType.STRING,
                sensitive=True,
                yaml_path=None,
            )
        )
        ciphertext = enc.encrypt("secret123")
        mock_repo.get_namespace.return_value = (
            ("api_key", ciphertext, "2026-01-01T00:00:00Z"),
        )
        # Service without encryptor -- batch should mask, not leak
        svc = SettingsService(
            repository=mock_repo,
            registry=registry,
            config=config,
            encryptor=None,
        )
        entries = await svc.get_namespace("budget")
        assert len(entries) == 1
        assert entries[0].value == "********"

    async def test_batch_masks_on_decrypt_failure(
        self, mock_repo: AsyncMock, config: _FakeConfig
    ) -> None:
        enc = SettingsEncryptor(Fernet.generate_key())
        registry = SettingsRegistry()
        registry.register(
            _make_definition(
                key="api_key",
                setting_type=SettingType.STRING,
                sensitive=True,
                yaml_path=None,
            )
        )
        # Use a different key's ciphertext -- decrypt will fail
        other_enc = SettingsEncryptor(Fernet.generate_key())
        bad_ciphertext = other_enc.encrypt("secret")
        mock_repo.get_namespace.return_value = (
            ("api_key", bad_ciphertext, "2026-01-01T00:00:00Z"),
        )
        svc = SettingsService(
            repository=mock_repo,
            registry=registry,
            config=config,
            encryptor=enc,
        )
        entries = await svc.get_namespace("budget")
        assert len(entries) == 1
        assert entries[0].value == "********"


# ── Validator Pattern Tests ─────────────────────────────────────


@pytest.mark.unit
class TestValidatorPattern:
    """Tests for validator_pattern regex validation."""

    async def test_valid_pattern_passes(
        self, mock_repo: AsyncMock, config: _FakeConfig
    ) -> None:
        registry = SettingsRegistry()
        registry.register(
            _make_definition(
                key="hostname",
                setting_type=SettingType.STRING,
                default="localhost",
                yaml_path=None,
                validator_pattern=r"^[a-z0-9.-]+$",
            )
        )
        svc = SettingsService(
            repository=mock_repo,
            registry=registry,
            config=config,
        )
        entry = await svc.set("budget", "hostname", "my-host.local")
        assert entry.value == "my-host.local"

    async def test_invalid_pattern_rejects(
        self, mock_repo: AsyncMock, config: _FakeConfig
    ) -> None:
        registry = SettingsRegistry()
        registry.register(
            _make_definition(
                key="hostname",
                setting_type=SettingType.STRING,
                default="localhost",
                yaml_path=None,
                validator_pattern=r"^[a-z0-9.-]+$",
            )
        )
        svc = SettingsService(
            repository=mock_repo,
            registry=registry,
            config=config,
        )
        with pytest.raises(SettingValidationError, match="does not match"):
            await svc.set("budget", "hostname", "INVALID HOST!")


# ── Security Audit Emission Tests (#1599) ───────────────────────


@pytest.mark.unit
class TestSecuritySettingsAuditEmission:
    """``SECURITY_SETTINGS_CHANGED`` must be emitted on `set` / `delete`
    paths only when the namespace is in the audited set.

    structlog routes events through a custom processor pipeline that
    bypasses pytest's stdlib ``caplog`` capture. We hook the service
    module's ``logger.info`` directly to capture event names as the
    underlying `logger.info(EVENT, **kwargs)` calls land.
    """

    async def test_set_emits_security_event_for_audited_namespace(
        self,
        mock_repo: AsyncMock,
        config: _FakeConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from synthorg.settings import service as service_mod

        registry = SettingsRegistry()
        registry.register(
            _make_definition(
                namespace=SettingNamespace.SECURITY,
                key="opt_in",
                setting_type=SettingType.BOOLEAN,
                default="false",
                yaml_path=None,
            )
        )
        svc = SettingsService(
            repository=mock_repo,
            registry=registry,
            config=config,
        )
        captured: list[str] = []
        original_info = service_mod.logger.info

        def _spy(event: str, **kwargs: Any) -> Any:
            captured.append(event)
            return original_info(event, **kwargs)

        monkeypatch.setattr(service_mod.logger, "info", _spy)
        await svc.set("security", "opt_in", "true")
        assert "security.settings.changed" in captured

    async def test_set_does_not_emit_security_event_for_non_audited_namespace(
        self,
        mock_repo: AsyncMock,
        config: _FakeConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from synthorg.settings import service as service_mod

        registry = SettingsRegistry()
        registry.register(
            _make_definition(
                namespace=SettingNamespace.BUDGET,
                key="total_monthly",
                setting_type=SettingType.FLOAT,
                yaml_path=None,
            )
        )
        svc = SettingsService(
            repository=mock_repo,
            registry=registry,
            config=config,
        )
        captured: list[str] = []
        original_info = service_mod.logger.info

        def _spy(event: str, **kwargs: Any) -> Any:
            captured.append(event)
            return original_info(event, **kwargs)

        monkeypatch.setattr(service_mod.logger, "info", _spy)
        await svc.set("budget", "total_monthly", "200.0")
        assert "security.settings.changed" not in captured

    async def test_delete_emits_security_event_for_audited_namespace(
        self,
        mock_repo: AsyncMock,
        config: _FakeConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from synthorg.settings import service as service_mod

        registry = SettingsRegistry()
        registry.register(
            _make_definition(
                namespace=SettingNamespace.SECURITY,
                key="opt_in",
                setting_type=SettingType.BOOLEAN,
                default="false",
                yaml_path=None,
            )
        )
        mock_repo.get.return_value = ("true", "2026-04-25T10:00:00Z")
        svc = SettingsService(
            repository=mock_repo,
            registry=registry,
            config=config,
        )
        captured: list[str] = []
        original_info = service_mod.logger.info

        def _spy(event: str, **kwargs: Any) -> Any:
            captured.append(event)
            return original_info(event, **kwargs)

        monkeypatch.setattr(service_mod.logger, "info", _spy)
        await svc.delete("security", "opt_in")
        assert "security.settings.changed" in captured
