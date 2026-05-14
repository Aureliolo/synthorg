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
    )


@pytest.fixture
def registry() -> SettingsRegistry:
    r = SettingsRegistry()
    r.register(_read_only_definition())
    r.register(_writable_definition())
    return r


@pytest.fixture
def repo() -> AsyncMock:
    # ``AsyncMock(spec=SettingsRepository)`` already stubs every async
    # method on the protocol; configure the per-test return values via
    # ``.return_value`` rather than reassigning each attribute to a
    # fresh ``AsyncMock`` (the prior pattern was redundant).
    repo = AsyncMock(spec=SettingsRepository)
    repo.get.return_value = None
    repo.set.return_value = True
    repo.set_many.return_value = True
    repo.delete.return_value = True
    repo.get_namespace.return_value = ()
    repo.get_all.return_value = ()
    repo.delete_namespace_returning_keys.return_value = ("log_directory",)
    return repo


@pytest.fixture
def service(repo: AsyncMock, registry: SettingsRegistry) -> SettingsService:
    return SettingsService(
        repository=repo,
        registry=registry,
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


async def test_delete_namespace_sweeps_with_warning_when_mixed(
    service: SettingsService,
    repo: AsyncMock,
) -> None:
    # A namespace that contains a mix of writable and read-only-post-init
    # definitions must NOT be hostage to the read-only entry: writable
    # overrides the operator wants to clear should still go through.
    # Reads already bypass DB for read-only entries, so any stale row
    # that gets swept here is already a no-op for the running process.
    # The service still emits a WARNING that lists the read-only keys
    # so an operator auditing the deletion can see exactly which rows
    # were swept.
    import structlog.testing  # local import keeps dep optional for other tests

    with structlog.testing.capture_logs() as logs:
        deleted = await service.delete_namespace("observability")

    repo.delete_namespace_returning_keys.assert_awaited_once()
    assert deleted == 1  # the fake returns ("log_directory",)
    swept_logs = [
        log
        for log in logs
        if log.get("event") == "settings.validation.failed"
        and log.get("reason") == "read_only_post_init_swept"
    ]
    assert swept_logs, logs
    assert swept_logs[0].get("read_only_keys") == ["log_directory"]


# ── Resolution still works through env / YAML ────────────────────


async def test_read_only_resolves_through_env(
    service: SettingsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYNTHORG_OBSERVABILITY_LOG_DIRECTORY", "/var/log/synthorg")
    value = await service.get("observability", "log_directory")
    assert value.value == "/var/log/synthorg"


# ── DB row is ignored on reads ───────────────────────────────────


async def test_get_bypasses_db_for_read_only_post_init(
    service: SettingsService, repo: AsyncMock
) -> None:
    # A leftover row (from a prior schema or an ops mistake on a peer
    # node) must not surface from get() -- the running process has
    # already locked in the boot-time value, so reading the DB would
    # show a value the runtime no longer honours.
    repo.get.return_value = ("/from/db", "2026-04-27T12:00:00+00:00")
    value = await service.get("observability", "log_directory")
    assert value.value == ""  # falls through to env (unset) -> default
    repo.get.assert_not_awaited()


async def test_get_namespace_bypasses_db_for_read_only_post_init(
    service: SettingsService, repo: AsyncMock
) -> None:
    repo.get_namespace.return_value = (
        ("log_directory", "/from/db", "2026-04-27T12:00:00+00:00"),
    )
    entries = await service.get_namespace("observability")
    log_dir = next(e for e in entries if e.definition.key == "log_directory")
    assert log_dir.value == ""
    # get_namespace still issues the batch DB query (writable settings in
    # the same namespace need it); the per-entry resolver is responsible
    # for ignoring the row when the definition is read-only-post-init.


async def test_get_versioned_returns_no_override_for_read_only_post_init(
    service: SettingsService, repo: AsyncMock
) -> None:
    repo.get.return_value = ("/from/db", "2026-04-27T12:00:00+00:00")
    value, updated_at = await service.get_versioned("observability", "log_directory")
    assert (value, updated_at) == ("", "")
    repo.get.assert_not_awaited()
