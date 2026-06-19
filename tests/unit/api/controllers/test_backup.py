"""Unit tests for BackupController -- direct method invocation with mocked service.

Litestar decorators (``@post``, ``@get``, etc.) wrap handler methods as
``HTTPRouteHandler`` objects.  To unit-test the handler logic without
bootstrapping a full Litestar app, we call the raw function via
``handler.fn(self, ...)``.
"""

import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock

import pytest
from litestar.datastructures import State

from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.controllers.backup import BackupController
from synthorg.api.cursor import CursorSecret
from synthorg.api.dto import ApiResponse, PaginatedResponse
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
from synthorg.backup.state import BackupStateSlice
from synthorg.core.domain_errors import ConflictError, ValidationError
from synthorg.core.error_taxonomy import ErrorCode
from synthorg.idempotency import (
    IdempotencyResult,
    IdempotencyService,
)
from tests._shared import LoopAsyncClient, make_app_state
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


def _make_state_and_service() -> tuple[State, AsyncMock]:
    """Create a Litestar ``State`` carrying a mock BackupService in app_state.

    Returns:
        Tuple of (state, mock_backup_service).
    """
    service = AsyncMock(spec=BackupService)
    # The controller now wraps every backup in idempotency_service.
    # Mock the service so run_idempotent invokes the callback inline
    # and returns a fresh outcome with the manifest dict.
    idempotency_service = MagicMock(spec=IdempotencyService)

    async def _run_idempotent(
        *,
        scope: object,
        key: object,
        callback: Callable[[], Awaitable[object]],
    ) -> IdempotencyResult:
        del scope, key
        result = await callback()
        return IdempotencyResult(result=result, fresh=True, timed_out=False)

    idempotency_service.run_idempotent = _run_idempotent
    # Pagination requires a real cursor secret.
    app_state = make_app_state(
        cursor_secret=CursorSecret.from_key("test-key-32-bytes-padding0000000"),
        slices={
            BackupStateSlice: {"service": service},
            ApiCoreStateSlice: {"idempotency_service": idempotency_service},
        },
    )
    # Carry the bound ``AppState`` on a real ``State`` so the controller's
    # ``state.app_state`` read is exercised against the genuine litestar type.
    state = State()
    state.app_state = app_state
    return state, service


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

    async def test_create_backup_propagates_in_progress(self) -> None:
        """BackupInProgressError propagates to handle_backup_error.

        The controller does not translate ``BackupInProgressError``
        to ``ConflictError``; ``handle_backup_error`` maps it to
        409 with the domain-specific ``RESOURCE_CONFLICT`` envelope
        so clients can discriminate the conflict source.
        """
        state, service = _make_state_and_service()
        service.create_backup.side_effect = BackupInProgressError("busy")

        ctrl = _controller()
        with pytest.raises(BackupInProgressError):
            await ctrl.create_backup.fn(
                ctrl,
                state=state,
                idempotency_key="test-key-002",
            )


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
        assert result.data == manifest

    async def test_get_backup_propagates_not_found(self) -> None:
        """BackupNotFoundError propagates to handle_backup_error.

        The controller does not translate ``BackupNotFoundError``
        to the generic ``NotFoundError`` (which would collapse to
        ``RESOURCE_NOT_FOUND``).  ``handle_backup_error`` maps it
        to 404 with the domain-specific ``RECORD_NOT_FOUND``
        envelope so clients can discriminate which resource type
        was missing.
        """
        state, service = _make_state_and_service()
        service.get_backup.side_effect = BackupNotFoundError("gone")

        ctrl = _controller()
        with pytest.raises(BackupNotFoundError):
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

    async def test_delete_backup_propagates_not_found(self) -> None:
        """BackupNotFoundError propagates to handle_backup_error.

        ``handle_backup_error`` owns the 404 + ``RECORD_NOT_FOUND``
        translation; controller-level translation would collapse
        the type into the generic ``NotFoundError``.
        """
        state, service = _make_state_and_service()
        service.delete_backup.side_effect = BackupNotFoundError("gone")

        ctrl = _controller()
        with pytest.raises(BackupNotFoundError):
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
            idempotency_key="restore-key",
        )

        service.restore_from_backup.assert_awaited_once_with(
            "abc123def456",
            components=None,
        )
        assert isinstance(result, ApiResponse)
        assert result.data == response

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
        await ctrl.restore_backup.fn(
            ctrl, state=state, data=request, idempotency_key="restore-key"
        )

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
            await ctrl.restore_backup.fn(
                ctrl, state=state, data=request, idempotency_key="restore-key"
            )

        assert exc_info.value.status_code == 422

    async def test_restore_propagates_not_found(self) -> None:
        """``BackupNotFoundError`` propagates to the centralized handler.

        ``handle_backup_error`` maps the domain exception to 404 with
        the ``RECORD_NOT_FOUND`` envelope; the controller-level
        translation to a generic ``NotFoundError`` would have dropped
        the discriminating error code.
        """
        state, service = _make_state_and_service()
        service.restore_from_backup.side_effect = BackupNotFoundError("gone")

        request = RestoreRequest(
            backup_id="000000000099",
            confirm=True,
        )
        ctrl = _controller()
        with pytest.raises(BackupNotFoundError):
            await ctrl.restore_backup.fn(
                ctrl, state=state, data=request, idempotency_key="restore-key"
            )

    async def test_restore_raises_409_on_in_progress(self) -> None:
        state, service = _make_state_and_service()
        service.restore_from_backup.side_effect = BackupInProgressError("busy")

        request = RestoreRequest(
            backup_id="abc123def456",
            confirm=True,
        )
        ctrl = _controller()
        with pytest.raises(ConflictError) as exc_info:
            await ctrl.restore_backup.fn(
                ctrl, state=state, data=request, idempotency_key="restore-key"
            )

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
            await ctrl.restore_backup.fn(
                ctrl, state=state, data=request, idempotency_key="restore-key"
            )

        assert exc_info.value.status_code == 422

    async def test_restore_reraises_restore_error(self) -> None:
        # Audit 34: the controller re-raises ``RestoreError`` directly so
        # ``handle_domain_error`` preserves ``BACKUP_RESTORE_FAILED``
        # instead of collapsing it to a generic 500 / ``INTERNAL_ERROR``.
        state, service = _make_state_and_service()
        service.restore_from_backup.side_effect = RestoreError("disk failure")

        request = RestoreRequest(
            backup_id="abc123def456",
            confirm=True,
        )
        ctrl = _controller()
        with pytest.raises(RestoreError) as exc_info:
            await ctrl.restore_backup.fn(
                ctrl, state=state, data=request, idempotency_key="restore-key"
            )

        assert exc_info.value.status_code == 500
        assert exc_info.value.error_code == ErrorCode.BACKUP_RESTORE_FAILED

    def test_restore_signature_marks_idempotency_key_required(self) -> None:
        sig = inspect.signature(BackupController.restore_backup.fn)
        param = sig.parameters["idempotency_key"]
        # A required header parameter carries no default value.
        assert param.default is inspect.Parameter.empty

    async def test_restore_timed_out_raises_409(self) -> None:
        """A concurrent in-flight restore (timed_out) surfaces a 409."""

        async def _timed_out(
            *,
            scope: object,
            key: object,
            callback: Callable[[], Awaitable[object]],
        ) -> IdempotencyResult:
            del scope, key, callback
            return IdempotencyResult(result=None, fresh=False, timed_out=True)

        service = AsyncMock(spec=BackupService)
        idempotency_service = MagicMock(spec=IdempotencyService)
        idempotency_service.run_idempotent = _timed_out
        app_state = make_app_state(
            cursor_secret=CursorSecret.from_key("test-key-32-bytes-padding0000000"),
            slices={
                BackupStateSlice: {"service": service},
                ApiCoreStateSlice: {"idempotency_service": idempotency_service},
            },
        )
        state = State()
        state.app_state = app_state

        request = RestoreRequest(backup_id="abc123def456", confirm=True)
        ctrl = _controller()
        with pytest.raises(ConflictError) as exc_info:
            await ctrl.restore_backup.fn(
                ctrl, state=state, data=request, idempotency_key="restore-key"
            )
        assert exc_info.value.status_code == 409
        # The callback never ran: the restore must not touch the service
        # when a concurrent claim is in flight.
        service.restore_from_backup.assert_not_called()


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
            await ctrl.restore_backup.fn(
                ctrl, state=state, data=request, idempotency_key="restore-key"
            )

        # Service must never be called when confirm is false.
        # ``assert_not_called()`` is stricter than ``assert_not_awaited()``:
        # the former trips even on an unawaited coroutine, catching a
        # regression where the controller forgets the ``await`` but
        # still creates the call.
        service.restore_from_backup.assert_not_called()


@pytest.mark.unit
class TestBackupGuards:
    """HTTP-level guard tests for backup controller access control."""

    @pytest.fixture(autouse=True)
    async def _mock_backup_service(
        self,
        async_test_client: LoopAsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> AsyncIterator[None]:
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
            "synthorg.api.construction_phase.build_backup_service",
            lambda *_a, **_kw: mock_svc,
        )
        app_state = async_test_client.app.state.app_state
        old_slice = app_state.slice(BackupStateSlice)
        app_state.swap_slice(BackupStateSlice(service=mock_svc))
        yield
        app_state.swap_slice(old_slice)

    async def test_ceo_can_access(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/admin/backups",
            headers=make_auth_headers("ceo"),
        )
        # 200 = guard passed (may return empty list)
        assert resp.status_code == 200

    @pytest.mark.parametrize(
        "role",
        ["manager", "board_member", "pair_programmer", "observer"],
    )
    async def test_non_admin_blocked(
        self,
        async_test_client: LoopAsyncClient,
        role: str,
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/admin/backups",
            headers=make_auth_headers(role),
        )
        assert resp.status_code == 403


@pytest.mark.unit
class TestBackupPathParamValidation:
    """Path parameter validation via Litestar Parameter constraints."""

    async def test_oversized_backup_id_rejected(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        long_id = "x" * 129
        resp = await async_test_client.get(
            f"/api/v1/admin/backups/{long_id}",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400
