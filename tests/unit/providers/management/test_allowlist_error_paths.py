"""Error-path coverage for the discovery allowlist manager.

The ``update_for_*`` hooks are best-effort: a settings-layer failure
while loading or persisting the allowlist is logged and swallowed
(never propagated into provider CRUD), and an interpreter-critical
exception still escapes via ``reraise_critical``.
"""

from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from typeguard import suppress_type_checks

from synthorg.providers.management.allowlist import DiscoveryAllowlistManager
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from tests._shared import mock_of


def _failing_manager() -> DiscoveryAllowlistManager:
    """Manager whose settings load always raises a non-critical error."""
    settings = mock_of[SettingsService](
        get=AsyncMock(
            spec=SettingsService.get,
            side_effect=RuntimeError("settings boom"),
        ),
    )
    resolver = mock_of[ConfigResolver]()
    return DiscoveryAllowlistManager(
        settings_service=settings,
        config_resolver=resolver,
    )


@pytest.mark.unit
class TestAllowlistUpdateErrorPaths:
    @pytest.fixture(autouse=True)
    def _suppress_typeguard(self) -> Iterator[None]:
        """Suppress typeguard: tests pass ``SimpleNamespace`` provider-config
        doubles to the best-effort ``update_for_*`` hooks; these verify the
        swallow-and-log behaviour, not config type conformance.
        """
        with suppress_type_checks():
            yield

    async def test_update_for_create_swallows_load_failure(self) -> None:
        manager = _failing_manager()
        config = SimpleNamespace(base_url="http://my-server:9090/v1")

        # Settings failure is logged, not raised, so provider create
        # is not blocked by an allowlist hiccup.
        await manager.update_for_create(config)  # type: ignore[arg-type]

    async def test_update_for_delete_swallows_load_failure(self) -> None:
        manager = _failing_manager()
        removed = SimpleNamespace(base_url="http://del-server:8080/v1")

        await manager.update_for_delete(removed, {})  # type: ignore[arg-type]

    async def test_update_for_update_swallows_load_failure(self) -> None:
        manager = _failing_manager()
        old = SimpleNamespace(base_url="http://old-server:8080/v1")
        new = SimpleNamespace(base_url="http://new-server:9090/v1")

        await manager.update_for_update(old, new, {})  # type: ignore[arg-type]
