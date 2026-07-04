"""Unit tests for persistence-gated infrastructure read-facade auto-wiring.

Covers ``wire_persistence_facades`` and its user / backup branches: each
facade wires only once its backing service reached its slice during startup,
and re-running the sweep never replaces a live facade.
"""

import pytest

from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.auth.service import AuthService
from synthorg.api.lifecycle_helpers.persistence_facade_autowire import (
    wire_persistence_facades,
)
from synthorg.api.state import AppState
from synthorg.backup.service import BackupService
from synthorg.backup.state import BackupStateSlice
from synthorg.infrastructure.services import BackupFacadeService, UserFacadeService
from synthorg.infrastructure.state import FacadesStateSlice
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _app_state(*, with_auth: bool = False, with_backup: bool = False) -> AppState:
    """Compose an app state with the requested backing services present.

    Returns:
        The composed ``AppState``.
    """
    api_core: dict[str, object] = {}
    backup: dict[str, object] = {}
    if with_auth:
        api_core["auth_service"] = mock_of[AuthService]()
    if with_backup:
        backup["service"] = mock_of[BackupService]()
    return make_app_state(
        slices={ApiCoreStateSlice: api_core, BackupStateSlice: backup},
    )


class TestUserFacadeWiring:
    async def test_wired_when_auth_present(self) -> None:
        app_state = _app_state(with_auth=True)
        await wire_persistence_facades(app_state)
        assert app_state.slice(FacadesStateSlice).user_facade_service is not None

    async def test_absent_without_auth(self) -> None:
        app_state = _app_state(with_auth=False)
        await wire_persistence_facades(app_state)
        assert app_state.slice(FacadesStateSlice).user_facade_service is None

    async def test_idempotent_keeps_existing(self) -> None:
        app_state = _app_state(with_auth=True)
        existing = UserFacadeService(auth_service=mock_of[AuthService]())
        app_state.wire(FacadesStateSlice, user_facade_service=existing)
        await wire_persistence_facades(app_state)
        assert app_state.slice(FacadesStateSlice).user_facade_service is existing


class TestBackupFacadeWiring:
    async def test_wired_when_backup_service_present(self) -> None:
        app_state = _app_state(with_backup=True)
        await wire_persistence_facades(app_state)
        assert app_state.slice(FacadesStateSlice).backup_facade_service is not None

    async def test_absent_without_backup_service(self) -> None:
        app_state = _app_state(with_backup=False)
        await wire_persistence_facades(app_state)
        assert app_state.slice(FacadesStateSlice).backup_facade_service is None

    async def test_idempotent_keeps_existing(self) -> None:
        app_state = _app_state(with_backup=True)
        existing = BackupFacadeService(service=mock_of[BackupService]())
        app_state.wire(FacadesStateSlice, backup_facade_service=existing)
        await wire_persistence_facades(app_state)
        assert app_state.slice(FacadesStateSlice).backup_facade_service is existing
