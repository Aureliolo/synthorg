"""Tests for the shared ``_resolve_db`` pipeline between get() and get_versioned().

PR #1239 deferred the ``get()``/``get_versioned()`` unification as a
follow-up. These tests lock the new behaviour: both public methods now
route DB reads through the shared ``_resolve_db`` helper, so sensitive
values are decrypted consistently.
"""

from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict

from synthorg.persistence.settings_protocol import SettingsRepository
from synthorg.settings.encryption import SettingsEncryptor
from synthorg.settings.enums import SettingNamespace, SettingType
from synthorg.settings.errors import SettingsEncryptionError
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
        key="openai_api_key",
        type=SettingType.STRING,
        default=None,
        description="test",
        group="test",
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
        encryptor=None,
    )


@pytest.mark.unit
class TestGetVersionedSharedPipeline:
    """get_versioned() goes through the same DB pipeline as get()."""

    async def test_returns_plaintext_for_plain_setting(
        self,
        service: SettingsService,
        mock_repo: AsyncMock,
    ) -> None:
        mock_repo.get.return_value = ("[]", "2026-04-11T10:00:00Z")
        value, updated_at = await service.get_versioned("company", "departments")
        assert value == "[]"
        assert updated_at == "2026-04-11T10:00:00Z"

    async def test_decrypts_sensitive_value(
        self,
        service: SettingsService,
        mock_repo: AsyncMock,
        encryptor: SettingsEncryptor,
    ) -> None:
        """Locks the new unified behaviour: get_versioned must decrypt."""
        ciphertext = encryptor.encrypt("sk-secret-plaintext")
        mock_repo.get.return_value = (ciphertext, "2026-04-11T10:00:00Z")
        value, updated_at = await service.get_versioned("providers", "openai_api_key")
        assert value == "sk-secret-plaintext"
        assert updated_at == "2026-04-11T10:00:00Z"

    async def test_returns_sentinel_for_missing_db_row(
        self,
        service: SettingsService,
        mock_repo: AsyncMock,
    ) -> None:
        mock_repo.get.return_value = None
        value, updated_at = await service.get_versioned("company", "departments")
        assert value == ""
        assert updated_at == ""

    async def test_returns_sentinel_for_unknown_registry_key(
        self,
        service: SettingsService,
        mock_repo: AsyncMock,
    ) -> None:
        value, updated_at = await service.get_versioned("company", "not_registered")
        assert value == ""
        assert updated_at == ""
        # Registry short-circuit -- no DB call was made.
        mock_repo.get.assert_not_awaited()

    async def test_both_methods_hit_shared_resolve_db(
        self,
        service: SettingsService,
        mock_repo: AsyncMock,
    ) -> None:
        """get() and get_versioned() both funnel through _resolve_db."""
        mock_repo.get.return_value = ("[]", "2026-04-11T10:00:00Z")

        await service.get("company", "departments")
        await service.get_versioned("company", "departments")

        # Cache is populated after get() for non-sensitive keys, so
        # only one DB hit is expected. get_versioned bypasses the cache
        # (CAS callers must always read live DB state), so it triggers
        # a second DB read.
        assert mock_repo.get.await_count == 2


@pytest.mark.unit
class TestResolveDbErrorPaths:
    """Failure paths in the shared ``_resolve_db`` pipeline."""

    async def test_missing_encryptor_on_sensitive_read_raises(
        self,
        service_no_encryptor: SettingsService,
        mock_repo: AsyncMock,
    ) -> None:
        """get_versioned on a sensitive key without encryptor raises."""
        mock_repo.get.return_value = (
            "ciphertext",
            "2026-04-11T10:00:00Z",
        )
        with pytest.raises(SettingsEncryptionError, match="no encryptor"):
            await service_no_encryptor.get_versioned("providers", "openai_api_key")

    async def test_decrypt_failure_raises(
        self,
        service: SettingsService,
        mock_repo: AsyncMock,
        encryptor: SettingsEncryptor,
    ) -> None:
        """Corrupted ciphertext in the DB raises SettingsEncryptionError."""
        mock_repo.get.return_value = ("not-valid-fernet", "2026-04-11T10:00:00Z")
        with pytest.raises(SettingsEncryptionError):
            await service.get_versioned("providers", "openai_api_key")
