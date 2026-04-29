"""Unit tests for ``SettingsService.set_many``.

Covers validation, encryption of sensitive values, CAS miss behaviour,
and cache invalidation -- the service-layer pipeline sitting on top of
``SettingsRepository.set_many``.
"""

from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict

from synthorg.core.domain_errors import VersionConflictError
from synthorg.persistence.settings_protocol import SettingsRepository
from synthorg.settings.encryption import SettingsEncryptor
from synthorg.settings.enums import SettingNamespace, SettingType
from synthorg.settings.errors import (
    SettingNotFoundError,
    SettingsEncryptionError,
)
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import SettingsRegistry
from synthorg.settings.service import SettingsService


class _FakeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)


def _plain_def() -> SettingDefinition:
    return SettingDefinition(
        namespace=SettingNamespace.COMPANY,
        key="departments",
        type=SettingType.STRING,
        default=None,
        description="test",
        group="test",
        yaml_path=None,
        sensitive=False,
        restart_required=False,
        enum_values=(),
        min_value=None,
        max_value=None,
        validator_pattern=None,
    )


def _sensitive_def() -> SettingDefinition:
    return SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="example_provider_api_key",
        type=SettingType.STRING,
        default=None,
        description="test",
        group="test",
        yaml_path=None,
        sensitive=True,
        restart_required=False,
        enum_values=(),
        min_value=None,
        max_value=None,
        validator_pattern=None,
    )


@pytest.fixture
def mock_repo() -> AsyncMock:
    repo = AsyncMock(spec=SettingsRepository)
    repo.get = AsyncMock(return_value=None)
    repo.set_many = AsyncMock(return_value=True)
    return repo


@pytest.fixture
def encryptor() -> SettingsEncryptor:
    return SettingsEncryptor(Fernet.generate_key())


@pytest.fixture
def registry() -> SettingsRegistry:
    r = SettingsRegistry()
    r.register(_plain_def())
    r.register(_sensitive_def())
    return r


@pytest.fixture
def service(
    mock_repo: AsyncMock,
    registry: SettingsRegistry,
    encryptor: SettingsEncryptor,
) -> SettingsService:
    return SettingsService(
        repository=mock_repo,
        registry=registry,
        config=_FakeConfig(),
        encryptor=encryptor,
    )


@pytest.fixture
def service_no_encryptor(
    mock_repo: AsyncMock,
    registry: SettingsRegistry,
) -> SettingsService:
    return SettingsService(
        repository=mock_repo,
        registry=registry,
        config=_FakeConfig(),
        encryptor=None,
    )


@pytest.mark.unit
class TestSetManyEncryption:
    """Sensitive values are encrypted before being handed to the repo."""

    async def test_sensitive_value_is_encrypted(
        self,
        service: SettingsService,
        mock_repo: AsyncMock,
        encryptor: SettingsEncryptor,
    ) -> None:
        await service.set_many(
            [("providers", "example_provider_api_key", "sk-secret-plaintext")],
            expected_updated_at_map={("providers", "example_provider_api_key"): ""},
        )
        mock_repo.set_many.assert_awaited_once()
        call_args = mock_repo.set_many.await_args
        assert call_args is not None
        prepared = call_args.args[0]
        assert len(prepared) == 1
        stored_value = prepared[0][2]
        # The stored value must be ciphertext, not the plaintext input.
        assert stored_value != "sk-secret-plaintext"
        # And the ciphertext must decrypt back to the original.
        assert encryptor.decrypt(stored_value) == "sk-secret-plaintext"

    async def test_plain_value_is_not_encrypted(
        self,
        service: SettingsService,
        mock_repo: AsyncMock,
    ) -> None:
        await service.set_many(
            [("company", "departments", "[]")],
            expected_updated_at_map={("company", "departments"): ""},
        )
        prepared = mock_repo.set_many.await_args.args[0]
        assert prepared[0][2] == "[]"

    async def test_sensitive_without_encryptor_raises(
        self,
        service_no_encryptor: SettingsService,
        mock_repo: AsyncMock,
    ) -> None:
        with pytest.raises(SettingsEncryptionError, match="without encryption"):
            await service_no_encryptor.set_many(
                [("providers", "example_provider_api_key", "sk-secret")],
                expected_updated_at_map={("providers", "example_provider_api_key"): ""},
            )
        mock_repo.set_many.assert_not_awaited()


@pytest.mark.unit
class TestSetManyValidationAndCAS:
    """Validation, unknown keys, and CAS-miss propagation."""

    async def test_empty_items_raises_value_error(
        self,
        service: SettingsService,
    ) -> None:
        with pytest.raises(ValueError, match="at least one item"):
            await service.set_many([], expected_updated_at_map={})

    async def test_unknown_key_raises_setting_not_found(
        self,
        service: SettingsService,
        mock_repo: AsyncMock,
    ) -> None:
        with pytest.raises(SettingNotFoundError):
            await service.set_many(
                [("company", "not_registered", "value")],
                expected_updated_at_map={("company", "not_registered"): ""},
            )
        mock_repo.set_many.assert_not_awaited()

    async def test_cas_miss_raises_version_conflict(
        self,
        service: SettingsService,
        mock_repo: AsyncMock,
    ) -> None:
        mock_repo.set_many.return_value = False
        with pytest.raises(VersionConflictError, match="Concurrent modification"):
            await service.set_many(
                [("company", "departments", "[]")],
                expected_updated_at_map={("company", "departments"): "stale-ts"},
            )


@pytest.mark.unit
class TestSetManyCacheInvalidation:
    """Cached values are cleared after a successful batch write."""

    async def test_cache_invalidated_per_key(
        self,
        service: SettingsService,
        mock_repo: AsyncMock,
    ) -> None:
        mock_repo.get.return_value = ("[]", "2026-04-11T10:00:00Z")
        # Prime the cache via get()
        await service.get("company", "departments")
        assert ("company", "departments") in service._cache

        await service.set_many(
            [("company", "departments", "[]")],
            expected_updated_at_map={("company", "departments"): ""},
        )
        # Cache must be cleared for the written key.
        assert ("company", "departments") not in service._cache
