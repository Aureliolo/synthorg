"""Unit tests for ``SettingsService.pending_restart``."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.persistence.settings_protocol import SettingRow, SettingsRepository
from synthorg.settings.enums import SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import SettingsRegistry
from synthorg.settings.service import SettingsService
from tests._shared import mock_of

_BOOT = datetime(2026, 7, 31, 10, 0, 0, tzinfo=UTC)
_BEFORE_BOOT = "2026-07-31T09:00:00Z"
_AFTER_BOOT = "2026-07-31T11:00:00Z"


def _definition(
    key: str,
    *,
    restart_required: bool,
    namespace: SettingNamespace = SettingNamespace.MEMORY,
) -> SettingDefinition:
    return SettingDefinition(
        namespace=namespace,
        key=NotBlankStr(key),
        type=SettingType.STRING,
        default=None,
        description=f"what {key} does",
        group="test",
        restart_required=restart_required,
    )


def _row(
    key: str,
    updated_at: str,
    *,
    namespace: str = "memory",
) -> SettingRow:
    return SettingRow(
        namespace=NotBlankStr(namespace),
        key=NotBlankStr(key),
        value="x",
        updated_at=updated_at,
    )


@pytest.fixture
def registry() -> SettingsRegistry:
    r = SettingsRegistry()
    r.register(_definition("embedder_model", restart_required=True))
    r.register(_definition("embedder_dims", restart_required=True))
    r.register(_definition("search_limit", restart_required=False))
    return r


@pytest.fixture
def mock_repo() -> AsyncMock:
    repo: AsyncMock = mock_of[SettingsRepository](
        get=AsyncMock(return_value=None),
        save=AsyncMock(),
    )
    repo.list_items = AsyncMock(return_value=())
    return repo


@pytest.fixture
def service(mock_repo: AsyncMock, registry: SettingsRegistry) -> SettingsService:
    return SettingsService(repository=mock_repo, registry=registry)


@pytest.mark.unit
class TestPendingRestart:
    """A pending restart is derived from writes the process has not read."""

    async def test_no_overrides_means_nothing_pending(
        self, service: SettingsService
    ) -> None:
        assert await service.pending_restart(since=_BOOT) == ()

    async def test_write_after_boot_is_pending(
        self, service: SettingsService, mock_repo: AsyncMock
    ) -> None:
        mock_repo.list_items.return_value = (_row("embedder_model", _AFTER_BOOT),)
        pending = await service.pending_restart(since=_BOOT)
        assert [p.key for p in pending] == ["embedder_model"]
        assert pending[0].namespace == SettingNamespace.MEMORY
        assert pending[0].description == "what embedder_model does"
        assert pending[0].updated_at == _AFTER_BOOT

    async def test_write_before_boot_is_already_in_effect(
        self, service: SettingsService, mock_repo: AsyncMock
    ) -> None:
        """The restart already happened, so the notice must clear itself."""
        mock_repo.list_items.return_value = (_row("embedder_model", _BEFORE_BOOT),)
        assert await service.pending_restart(since=_BOOT) == ()

    async def test_hot_reloadable_setting_never_pends(
        self, service: SettingsService, mock_repo: AsyncMock
    ) -> None:
        mock_repo.list_items.return_value = (_row("search_limit", _AFTER_BOOT),)
        assert await service.pending_restart(since=_BOOT) == ()

    async def test_unregistered_row_is_ignored(
        self, service: SettingsService, mock_repo: AsyncMock
    ) -> None:
        """A row whose definition was removed cannot claim a restart."""
        mock_repo.list_items.return_value = (_row("retired_key", _AFTER_BOOT),)
        assert await service.pending_restart(since=_BOOT) == ()

    async def test_results_are_sorted_by_namespace_then_key(
        self, service: SettingsService, mock_repo: AsyncMock
    ) -> None:
        mock_repo.list_items.return_value = (
            _row("embedder_model", _AFTER_BOOT),
            _row("embedder_dims", _AFTER_BOOT),
        )
        pending = await service.pending_restart(since=_BOOT)
        assert [p.key for p in pending] == ["embedder_dims", "embedder_model"]

    async def test_mixed_rows_return_only_the_pending_ones(
        self, service: SettingsService, mock_repo: AsyncMock
    ) -> None:
        mock_repo.list_items.return_value = (
            _row("embedder_model", _AFTER_BOOT),
            _row("embedder_dims", _BEFORE_BOOT),
            _row("search_limit", _AFTER_BOOT),
        )
        pending = await service.pending_restart(since=_BOOT)
        assert [p.key for p in pending] == ["embedder_model"]
