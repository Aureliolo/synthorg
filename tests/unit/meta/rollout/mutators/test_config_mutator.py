"""SettingsServiceConfigMutator routes through the canonical service."""

from typing import cast
from unittest.mock import AsyncMock, create_autospec

import pytest

from synthorg.meta.errors import RollbackMutationDeniedError
from synthorg.meta.rollout.mutators import SettingsServiceConfigMutator
from synthorg.settings.errors import SettingNotFoundError, SettingReadOnlyError
from synthorg.settings.service import SettingsService

pytestmark = pytest.mark.unit


def _make_service() -> SettingsService:
    """Build a typed SettingsService stub via autospec."""
    service = create_autospec(SettingsService, instance=True, spec_set=True)
    service.set = AsyncMock()
    return cast("SettingsService", service)


class TestSettingsServiceConfigMutator:
    """The ConfigMutator surface for the rollback executor."""

    async def test_set_routes_to_settings_service_set(self) -> None:
        service = _make_service()
        mutator = SettingsServiceConfigMutator(settings_service=service)

        await mutator.set(path="api.server_port", value=8080)

        service.set.assert_awaited_once_with("api", "server_port", "8080")

    async def test_value_coerced_to_string(self) -> None:
        """Settings persist values as strings; coerce at the boundary."""
        service = _make_service()
        mutator = SettingsServiceConfigMutator(settings_service=service)

        await mutator.set(path="budget.daily_limit", value=42.5)

        service.set.assert_awaited_once_with("budget", "daily_limit", "42.5")

    async def test_missing_dot_in_path_rejected(self) -> None:
        service = _make_service()
        mutator = SettingsServiceConfigMutator(settings_service=service)

        with pytest.raises(RollbackMutationDeniedError, match=r"namespace\.key"):
            await mutator.set(path="not_a_dotted_path", value="x")
        service.set.assert_not_awaited()

    async def test_empty_namespace_rejected(self) -> None:
        service = _make_service()
        mutator = SettingsServiceConfigMutator(settings_service=service)

        with pytest.raises(RollbackMutationDeniedError):
            await mutator.set(path=".key_only", value="x")
        service.set.assert_not_awaited()

    async def test_read_only_post_init_surfaces_as_denied(self) -> None:
        """SettingReadOnlyError becomes RollbackMutationDeniedError."""
        service = _make_service()
        service.set.side_effect = SettingReadOnlyError("post-init readonly")
        mutator = SettingsServiceConfigMutator(settings_service=service)

        with pytest.raises(RollbackMutationDeniedError, match="post-init-readonly"):
            await mutator.set(path="api.server_host", value="0.0.0.0")  # noqa: S104

    async def test_unknown_setting_surfaces_as_denied(self) -> None:
        """SettingNotFoundError becomes RollbackMutationDeniedError."""
        service = _make_service()
        service.set.side_effect = SettingNotFoundError("missing")
        mutator = SettingsServiceConfigMutator(settings_service=service)

        with pytest.raises(RollbackMutationDeniedError, match="unknown setting"):
            await mutator.set(path="api.nope", value="x")
