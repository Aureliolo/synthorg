"""Unit tests for BackupController -- direct method invocation with mocked service.

Litestar decorators (``@post``, ``@get``, etc.) wrap handler methods as
``HTTPRouteHandler`` objects.  To unit-test the handler logic without
bootstrapping a full Litestar app, we call the raw function via
``handler.fn(self, ...)``.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from litestar.exceptions import InternalServerException
from litestar.testing import TestClient

from synthorg.api.controllers.backup import BackupController
from synthorg.api.cursor import CursorSecret
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.services.idempotency_service import (
    IdempotencyResult,
    IdempotencyService,
)
from synthorg.api.state import AppState
from synthorg.backup.errors import (
    BackupInProgressError,
    BackupNotFoundError,
    ManifestError,
    RestoreError,
)
from synthorg.backup.models import (
    BackupComponent,
    BackupManifest,
    BackupTrigger,
    RestoreRequest,
    RestoreResponse,
)
from synthorg.backup.service import BackupService
from synthorg.core.domain_errors import ConflictError, NotFoundError, ValidationError
from tests.unit.api.conftest import make_auth_headers


def _make_manifest(
    *,
    backup_id: str = "abc123def456",
    trigger: BackupTrigger = BackupTrigger.MANUAL,
) -> BackupManifest:
    """Build a minimal BackupManifest for test assertions."""
    return BackupManifest(
        synthorg_version="0.3.2",
        timestamp="2026-03-18T12:00:00+00:00",
        trigger=trigger,
        components=(BackupComponent.PERSISTENCE,),
        size_bytes=4096,
        checksum="sha256:" + "a" * 64,
        backup_id=backup_id,
    )


def _make_restore_response(
    *,
    backup_id: str = "abc123def456",
    safety_id: str = "safe00000001",
) -> RestoreResponse:
    """Build a minimal RestoreResponse for test assertions."""
    manifest = _make_manifest(backup_id=backup_id)
    return RestoreResponse(
        manifest=manifest,
        restored_components=(BackupComponent.PERSISTENCE,),
        safety_backup_id=safety_id,
    )


def _make_state_and_service() -> tuple[SimpleNamespace, AsyncMock]:
    """Create a mock Litestar State with a mock BackupService in app_state.

    Returns:
        Tuple of (mock_state, mock_backup_service).
    """
    service = AsyncMock(spec=BackupService)
    app_state = MagicMock(spec=AppState)
    app_state.backup_service = service
    # The controller now wraps every backup in idempotency_service.
    # Mock the service so run_idempotent invokes the callback inline
    # and returns a fresh outcome with the manifest dict. ``spec=`` on
    # the wrapper enforces the interface; ``run_idempotent`` is set to
    # an inline async helper that exercises the awaitable contract.
    idempotency_service = MagicMock(spec=IdempotencyService)

    async def _run_idempotent(
        *,
        scope: object,
        key: object,
        callback: Any,
    ) -> IdempotencyResult:
        del scope, key
        result = await callback()
        return IdempotencyResult(result=result, fresh=True, timed_out=False)

    idempotency_service.run_idempotent = _run_idempotent
    app_state.idempotency_service = idempotency_service
    # Pagination requires a real cursor secret; MagicMock's default
    # attribute resolution would hand back a Mock to ``paginate_cursor``
    # which ultimately fails the HMAC pipeline.
    app_state.cursor_secret = CursorSecret.from_key("test-key-32-bytes-padding0000000")

    # ``SimpleNamespace`` is the right sentinel for the ``state``
    # carrier here: it has no auto-mocking magic, so ``state.app_state``
    # always returns the assigned object. ``MagicMock(spec=State)``
    # would intercept attribute access via Litestar's
    # ``State.__getattr__`` and might hand back a fresh auto-mock
    # instead of the spec-bound ``AppState`` we just built; a plain
    # ``MagicMock()`` would trip the no-bare-mock gate.
    return SimpleNamespace(app_state=app_state), service


def _controller() -> BackupController:
    """Create a BackupController instance for testing."""
    return BackupController(owner=BackupController)  # type: ignore[arg-type]


@pytest.mark.unit
class TestCreateBackup:
    """BackupController.create_backup endpoint."""

    async def test_create_backup_calls_service_with_manual_trigger(self) -> None:
        state, service = _make_state_and_service()
        manifest = _make_manifest()
        service.create_backup.return_value = manifest

        ctrl = _controller()
        result = await ctrl.create_backup.fn(
            ctrl,
            state=state,
            idempotency_key="test-key-001",
        )

        service.create_backup.assert_awaited_once_with(BackupTrigger.MANUAL)
        assert isinstance(result, ApiResponse)
        assert result.data == manifest

    async def test_create_backup_returns_409_on_in_progress(self) -> None:
        state, service = _make_state_and_service()
        service.create_backup.side_effect = BackupInProgressError("busy")

        ctrl = _controller()
        with pytest.raises(ConflictError) as exc_info:
            await ctrl.create_backup.fn(
                ctrl,
                state=state,
                idempotency_key="test-key-002",
            )

        assert exc_info.value.status_code == 409


@pytest.mark.unit
class TestListBackups:
    """BackupController.list_backups endpoint."""

    async def test_list_backups_calls_service(self) -> None:
        state, service = _make_state_and_service()
        service.list_backups.return_value = ()

        ctrl = _controller()
        result = await ctrl.list_backups.fn(ctrl, state=state)

        service.list_backups.assert_awaited_once()
        assert isinstance(result, PaginatedResponse)
        assert result.data == ()
        assert result.pagination.has_more is False
        assert result.pagination.next_cursor is None


@pytest.mark.unit
class TestGetBackup:
    """BackupController.get_backup endpoint."""

    async def test_get_backup_calls_service_with_id(self) -> None:
        state, service = _make_state_and_service()
        manifest = _make_manifest()
        service.get_backup.return_value = manifest

        ctrl = _controller()
        result = await ctrl.get_backup.fn(
            ctrl,
            state=state,
            backup_id="abc123def456",
        )

        service.get_backup.assert_awaited_once_with("abc123def456")
        assert isinstance(result, ApiResponse)
        assert result.data is manifest

    async def test_get_backup_raises_404_on_not_found(self) -> None:
        state, service = _make_state_and_service()
        service.get_backup.side_effect = BackupNotFoundError("gone")

        ctrl = _controller()
        with pytest.raises(NotFoundError):
            await ctrl.get_backup.fn(
                ctrl,
                state=state,
                backup_id="nonexistent",
            )


@pytest.mark.unit
class TestDeleteBackup:
    """BackupController.delete_backup endpoint."""

    async def test_delete_backup_calls_service_with_id(self) -> None:
        state, service = _make_state_and_service()
        service.delete_backup.return_value = None

        ctrl = _controller()
        result = await ctrl.delete_backup.fn(
            ctrl,
            state=state,
            backup_id="abc123def456",
        )

        service.delete_backup.assert_awaited_once_with("abc123def456")
        assert result is None

    async def test_delete_backup_raises_404_on_not_found(self) -> None:
        state, service = _make_state_and_service()
        service.delete_backup.side_effect = BackupNotFoundError("gone")

        ctrl = _controller()
        with pytest.raises(NotFoundError):
            await ctrl.delete_backup.fn(
                ctrl,
                state=state,
                backup_id="nonexistent",
            )


@pytest.mark.unit
class TestRestoreBackup:
    """BackupController.restore_backup endpoint."""

    async def test_restore_calls_service_with_confirm_true(self) -> None:
        state, service = _make_state_and_service()
        response = _make_restore_response()
        service.restore_from_backup.return_value = response

        request = RestoreRequest(
            backup_id="abc123def456",
            confirm=True,
        )
        ctrl = _controller()
        result = await ctrl.restore_backup.fn(
            ctrl,
            state=state,
            data=request,
        )

        service.restore_from_backup.assert_awaited_once_with(
            "abc123def456",
            components=None,
        )
        assert isinstance(result, ApiResponse)
        assert result.data is response

    async def test_restore_passes_components_to_service(self) -> None:
        state, service = _make_state_and_service()
        response = _make_restore_response()
        service.restore_from_backup.return_value = response

        components = (BackupComponent.PERSISTENCE, BackupComponent.CONFIG)
        request = RestoreRequest(
            backup_id="abc123def456",
            components=components,
            confirm=True,
        )
        ctrl = _controller()
        await ctrl.restore_backup.fn(ctrl, state=state, data=request)

        service.restore_from_backup.assert_awaited_once_with(
            "abc123def456",
            components=components,
        )

    async def test_restore_raises_422_without_confirm(self) -> None:
        state, _service = _make_state_and_service()
        request = RestoreRequest(
            backup_id="abc123def456",
            confirm=False,
        )

        ctrl = _controller()
        with pytest.raises(ValidationError) as exc_info:
            await ctrl.restore_backup.fn(ctrl, state=state, data=request)

        assert exc_info.value.status_code == 422

    async def test_restore_raises_404_on_not_found(self) -> None:
        state, service = _make_state_and_service()
        service.restore_from_backup.side_effect = BackupNotFoundError("gone")

        request = RestoreRequest(
            backup_id="000000000099",
            confirm=True,
        )
        ctrl = _controller()
        with pytest.raises(NotFoundError):
            await ctrl.restore_backup.fn(ctrl, state=state, data=request)

    async def test_restore_raises_409_on_in_progress(self) -> None:
        state, service = _make_state_and_service()
        service.restore_from_backup.side_effect = BackupInProgressError("busy")

        request = RestoreRequest(
            backup_id="abc123def456",
            confirm=True,
        )
        ctrl = _controller()
        with pytest.raises(ConflictError) as exc_info:
            await ctrl.restore_backup.fn(ctrl, state=state, data=request)

        assert exc_info.value.status_code == 409

    async def test_restore_raises_422_on_manifest_error(self) -> None:
        state, service = _make_state_and_service()
        service.restore_from_backup.side_effect = ManifestError("corrupt manifest")

        request = RestoreRequest(
            backup_id="abc123def456",
            confirm=True,
        )
        ctrl = _controller()
        with pytest.raises(ValidationError) as exc_info:
            await ctrl.restore_backup.fn(ctrl, state=state, data=request)

        assert exc_info.value.status_code == 422

    async def test_restore_raises_500_on_restore_error(self) -> None:
        state, service = _make_state_and_service()
        service.restore_from_backup.side_effect = RestoreError("disk failure")

        request = RestoreRequest(
            backup_id="abc123def456",
            confirm=True,
        )
        ctrl = _controller()
        with pytest.raises(InternalServerException):
            await ctrl.restore_backup.fn(ctrl, state=state, data=request)


@pytest.mark.unit
class TestRestoreConfirmGate:
    """confirm=true safety gate is enforced before any service interaction."""

    async def test_service_not_called_when_confirm_false(
        self,
    ) -> None:
        state, service = _make_state_and_service()
        request = RestoreRequest(
            backup_id="abc123def456",
            confirm=False,
        )

        ctrl = _controller()
        with pytest.raises(ValidationError):
            await ctrl.restore_backup.fn(ctrl, state=state, data=request)

        # Service must never be called when confirm is false
        service.restore_from_backup.assert_not_awaited()


@pytest.mark.unit
class TestBackupGuards:
    """HTTP-level guard tests for backup controller access control."""

    @pytest.fixture(autouse=True)
    def _mock_backup_service(
        self,
        test_client: TestClient[Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> Any:
        """Override the API-wide backup disable.

        Guard tests need a backup service instance in ``app_state``
        so the list-backups endpoint responds instead of crashing,
        but we still want to avoid real filesystem I/O.

        Also sets the mock directly on AppState to support
        session-scoped apps where the factory patch cannot affect
        the already-created app.
        """
        from synthorg.backup.scheduler import BackupScheduler

        mock_svc = MagicMock(spec=BackupService)
        mock_svc.on_startup = False
        mock_svc.on_shutdown = False
        mock_svc.list_backups.return_value = []
        scheduler = MagicMock(spec=BackupScheduler)
        scheduler.stop = AsyncMock(spec=BackupScheduler.stop)
        mock_svc.scheduler = scheduler
        monkeypatch.setattr(
            "synthorg.api.app.build_backup_service",
            lambda *_a, **_kw: mock_svc,
        )
        app_state = test_client.app.state.app_state
        old = app_state._backup_service
        app_state._backup_service = mock_svc
        yield
        app_state._backup_service = old

    def test_ceo_can_access(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get(
            "/api/v1/admin/backups",
            headers=make_auth_headers("ceo"),
        )
        # 200 = guard passed (may return empty list)
        assert resp.status_code == 200

    @pytest.mark.parametrize(
        "role",
        ["manager", "board_member", "pair_programmer", "observer"],
    )
    def test_non_admin_blocked(
        self,
        test_client: TestClient[Any],
        role: str,
    ) -> None:
        resp = test_client.get(
            "/api/v1/admin/backups",
            headers=make_auth_headers(role),
        )
        assert resp.status_code == 403


@pytest.mark.unit
class TestBackupPathParamValidation:
    """Path parameter validation via Litestar Parameter constraints."""

    def test_oversized_backup_id_rejected(
        self,
        test_client: TestClient[Any],
    ) -> None:
        long_id = "x" * 129
        resp = test_client.get(
            f"/api/v1/admin/backups/{long_id}",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400
