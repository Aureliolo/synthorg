"""Service-layer rejection of writes on read-only-post-init settings.

A ``SettingDefinition`` marked ``read_only_post_init=True`` is sourced
exclusively from environment variables or YAML at process startup; the
registry entry exists for discoverability via the /settings API.  The
service layer must reject every write surface (``set``, ``set_many``,
``delete``, ``delete_namespace``) so an operator cannot believe the
value was overridden when the running process actually keeps using the
boot-time value.
"""

from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ConfigDict

from synthorg.persistence.settings_protocol import SettingsRepository
from synthorg.settings.enums import SettingNamespace, SettingType
from synthorg.settings.errors import SettingReadOnlyError, SettingValidationError
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import SettingsRegistry
from synthorg.settings.service import SettingsService

pytestmark = pytest.mark.unit


class _FakeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)


def _read_only_definition() -> SettingDefinition:
    return SettingDefinition(
        namespace=SettingNamespace.OBSERVABILITY,
        key="log_directory",
        type=SettingType.STRING,
        default="",
        description="Log output directory (env-only, restart required)",
        group="Logging",
        read_only_post_init=True,
        restart_required=True,
        yaml_path="logging.log_dir",
    )


def _writable_definition() -> SettingDefinition:
    return SettingDefinition(
        namespace=SettingNamespace.OBSERVABILITY,
        key="root_log_level",
        type=SettingType.ENUM,
        default="info",
        description="Root logger level",
        group="Logging",
        enum_values=("debug", "info", "warning", "error", "critical"),
        yaml_path="logging.root_level",
    )


@pytest.fixture
def registry() -> SettingsRegistry:
    r = SettingsRegistry()
    r.register(_read_only_definition())
    r.register(_writable_definition())
    return r


@pytest.fixture
def repo() -> AsyncMock:
    repo = AsyncMock(spec=SettingsRepository)
    repo.get = AsyncMock(return_value=None)
    repo.set = AsyncMock(return_value=True)
    repo.set_many = AsyncMock(return_value=True)
    repo.delete = AsyncMock(return_value=True)
    repo.get_namespace = AsyncMock(return_value=())
    repo.get_all = AsyncMock(return_value=())
    repo.delete_namespace_returning_keys = AsyncMock(return_value=("log_directory",))
    return repo


@pytest.fixture
def service(repo: AsyncMock, registry: SettingsRegistry) -> SettingsService:
    return SettingsService(
        repository=repo,
        registry=registry,
        config=_FakeConfig(),
    )


# ── set() ────────────────────────────────────────────────────────


async def test_set_rejects_read_only_post_init(
    service: SettingsService, repo: AsyncMock
) -> None:
    with pytest.raises(SettingReadOnlyError):
        await service.set("observability", "log_directory", "/var/log/synthorg")
    repo.set.assert_not_awaited()


async def test_set_read_only_error_is_validation_error_subclass(
    service: SettingsService,
) -> None:
    # The HTTP 422 mapping in API controllers walks the
    # SettingValidationError hierarchy; SettingReadOnlyError must be a
    # subclass so existing controllers keep working without code change.
    with pytest.raises(SettingValidationError):
        await service.set("observability", "log_directory", "/var/log/synthorg")


async def test_set_writable_setting_still_works(
    service: SettingsService, repo: AsyncMock
) -> None:
    await service.set("observability", "root_log_level", "debug")
    repo.set.assert_awaited_once()


# ── set_many() ───────────────────────────────────────────────────


async def test_set_many_rejects_when_any_item_is_read_only(
    service: SettingsService, repo: AsyncMock
) -> None:
    items = (
        ("observability", "root_log_level", "warning"),
        ("observability", "log_directory", "/var/log/synthorg"),
    )
    with pytest.raises(SettingReadOnlyError):
        await service.set_many(items, expected_updated_at_map={})
    repo.set_many.assert_not_awaited()


# ── delete() ─────────────────────────────────────────────────────


async def test_delete_rejects_read_only_post_init(
    service: SettingsService, repo: AsyncMock
) -> None:
    with pytest.raises(SettingReadOnlyError):
        await service.delete("observability", "log_directory")
    repo.delete.assert_not_awaited()


async def test_delete_writable_setting_still_works(
    service: SettingsService, repo: AsyncMock
) -> None:
    await service.delete("observability", "root_log_level")
    repo.delete.assert_awaited_once()


# ── delete_namespace() ───────────────────────────────────────────


async def test_delete_namespace_rejects_when_any_definition_is_read_only(
    service: SettingsService, repo: AsyncMock
) -> None:
    # Even one read-only definition in the namespace must block the
    # whole-namespace delete: a bulk operation that silently leaves
    # the read-only row in place would be misleading; rejecting up
    # front forces the operator to delete by key.
    with pytest.raises(SettingReadOnlyError):
        await service.delete_namespace("observability")
    repo.delete_namespace_returning_keys.assert_not_awaited()


# ── Resolution still works through env / YAML ────────────────────


async def test_read_only_resolves_through_env(
    service: SettingsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYNTHORG_OBSERVABILITY_LOG_DIRECTORY", "/var/log/synthorg")
    value = await service.get("observability", "log_directory")
    assert value.value == "/var/log/synthorg"
