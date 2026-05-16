"""Coverage for the 4 new registry entries added by #1613.

These entries exist for /settings UI discoverability but their
runtime semantics differ:

- ``observability.log_directory``: read-only-post-init; sourced from
  SYNTHORG_LOG_DIR env > YAML at boot.
- ``observability.log_level_console``: mutable; sourced from DB > env
  (SYNTHORG_LOG_LEVEL) > unset.
- ``communication.nats_url``: read-only-post-init; sourced from env >
  YAML > default at boot.
- ``workers.count``: read-only-post-init; sourced from env > YAML >
  default at boot.

The read-only entries reject mutation through SettingsService.set(),
matching the contract documented in
``docs/reference/configuration-precedence.md``.
"""

from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ConfigDict

from synthorg.persistence.settings_protocol import SettingsRepository
from synthorg.settings import definitions as _settings_definitions  # noqa: F401
from synthorg.settings.errors import SettingReadOnlyError
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService

pytestmark = pytest.mark.unit


class _FakeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)


@pytest.fixture
def service() -> SettingsService:
    repo = AsyncMock(spec=SettingsRepository)
    repo.get = AsyncMock(return_value=None)
    repo.get_namespace = AsyncMock(return_value=())
    repo.list_items = AsyncMock(return_value=())
    return SettingsService(
        repository=repo,
        registry=get_registry(),
    )


def test_log_directory_registered_read_only_post_init() -> None:
    defn = get_registry().get("observability", "log_directory")
    assert defn is not None
    assert defn.read_only_post_init is True
    assert defn.restart_required is True


def test_log_level_console_registered_mutable() -> None:
    defn = get_registry().get("observability", "log_level_console")
    assert defn is not None
    assert defn.read_only_post_init is False
    # restart_required only required if read_only_post_init=True.
    assert defn.restart_required is False


def test_nats_url_registered_read_only_post_init() -> None:
    defn = get_registry().get("communication", "nats_url")
    assert defn is not None
    assert defn.read_only_post_init is True
    assert defn.default == "nats://nats:4222"


def test_workers_count_registered_read_only_post_init() -> None:
    defn = get_registry().get("workers", "count")
    assert defn is not None
    assert defn.read_only_post_init is True
    assert defn.default == "1"


async def test_log_directory_set_rejects(service: SettingsService) -> None:
    with pytest.raises(SettingReadOnlyError):
        await service.set("observability", "log_directory", "/var/log/synthorg")


async def test_nats_url_set_rejects(service: SettingsService) -> None:
    with pytest.raises(SettingReadOnlyError):
        await service.set("communication", "nats_url", "nats://other-host:4222")


async def test_workers_count_set_rejects(service: SettingsService) -> None:
    with pytest.raises(SettingReadOnlyError):
        await service.set("workers", "count", "4")


async def test_log_level_console_set_succeeds(service: SettingsService) -> None:
    # Mutable; the regex validator accepts the empty string and the
    # five canonical levels (case-insensitive).
    repo: AsyncMock = service._repository  # type: ignore[assignment]
    repo.save = AsyncMock(return_value=True)
    await service.set("observability", "log_level_console", "debug")
    repo.set.assert_awaited_once()


async def test_log_level_console_validator_rejects_garbage(
    service: SettingsService,
) -> None:
    from synthorg.settings.errors import SettingValidationError

    with pytest.raises(SettingValidationError):
        await service.set("observability", "log_level_console", "verbose")


# ── Env / YAML fallthrough for the read-only entries ────────────


async def test_log_directory_resolves_through_env(
    service: SettingsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``observability.log_directory`` reads the canonical SYNTHORG_LOG_DIR.

    The registry entry sets ``env_var_override="SYNTHORG_LOG_DIR"`` so
    the resolver looks up the established operator-facing name rather
    than the auto-derived ``SYNTHORG_OBSERVABILITY_LOG_DIRECTORY``.
    """
    monkeypatch.setenv("SYNTHORG_LOG_DIR", "/var/log/synthorg")
    value = await service.get("observability", "log_directory")
    assert value.value == "/var/log/synthorg"


async def test_log_directory_ignores_auto_derived_env_name(
    service: SettingsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto-derived env name is NOT consulted when env_var_override is set."""
    monkeypatch.delenv("SYNTHORG_LOG_DIR", raising=False)
    monkeypatch.setenv("SYNTHORG_OBSERVABILITY_LOG_DIRECTORY", "/should/be/ignored")
    value = await service.get("observability", "log_directory")
    # Falls through to default because only the override name is consulted.
    assert value.value == ""


async def test_nats_url_resolves_through_env(
    service: SettingsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``communication.nats_url`` reads SYNTHORG_NATS_URL (canonical name)."""
    monkeypatch.setenv("SYNTHORG_NATS_URL", "nats://override:4223")
    value = await service.get("communication", "nats_url")
    assert value.value == "nats://override:4223"


async def test_nats_url_falls_back_to_default(
    service: SettingsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without env or YAML, ``nats_url`` returns the registered default."""
    monkeypatch.delenv("SYNTHORG_NATS_URL", raising=False)
    value = await service.get("communication", "nats_url")
    assert value.value == "nats://nats:4222"


async def test_workers_count_resolves_through_env(
    service: SettingsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``workers.count`` reads SYNTHORG_WORKERS (canonical name)."""
    monkeypatch.setenv("SYNTHORG_WORKERS", "4")
    value = await service.get("workers", "count")
    assert value.value == "4"


async def test_workers_count_falls_back_to_default(
    service: SettingsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without env, ``workers.count`` returns the registered default."""
    monkeypatch.delenv("SYNTHORG_WORKERS", raising=False)
    value = await service.get("workers", "count")
    assert value.value == "1"
