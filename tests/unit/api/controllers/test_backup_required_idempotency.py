"""Idempotency-Key is mandatory for POST /admin/backups.

Without a key, a network-flake-driven 5xx retry could launch
concurrent backups and violate the at-most-one-running invariant.
The header is required by Litestar's parameter validation; missing
or empty values yield HTTP 400.

These tests avoid spinning up the full Litestar app: we inspect the
route handler's parameter signature and verify the controller
correctly invokes the idempotency service when the key is supplied.
"""

import inspect
from collections.abc import Awaitable, Callable
from unittest.mock import MagicMock

import pytest
from litestar.datastructures import State

from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.controllers.backup import BackupController
from synthorg.api.cursor import CursorSecret
from synthorg.api.services.idempotency_service import (
    IdempotencyResult,
    IdempotencyService,
)
from synthorg.backup.models import (
    BackupComponent,
    BackupManifest,
    BackupTrigger,
)
from synthorg.backup.service import BackupService
from synthorg.backup.state import BackupStateSlice
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def _make_manifest() -> BackupManifest:
    return BackupManifest(
        synthorg_version="0.3.2",
        timestamp="2026-03-18T12:00:00+00:00",
        trigger=BackupTrigger.MANUAL,
        components=(BackupComponent.PERSISTENCE,),
        size_bytes=4096,
        checksum="sha256:" + "a" * 64,
        backup_id="abc123def456",
    )


def _make_state(  # type: ignore[explicit-any]  # Callable ellipsis mirrors IdempotencyService.run_idempotent
    *,
    run_idempotent: Callable[..., Awaitable[IdempotencyResult]],
) -> tuple[State, MagicMock]:
    # ``MagicMock(spec=BackupService)`` auto-mocks ``create_backup``
    # as an AsyncMock. Set ``return_value`` on the auto-mock directly
    # so the spec-bound interface is preserved (replacing the
    # auto-mock with a fresh AsyncMock would discard the bound
    # signature).
    service = MagicMock(spec=BackupService)
    service.create_backup.return_value = _make_manifest()
    idempotency_service = MagicMock(spec=IdempotencyService)
    idempotency_service.run_idempotent = run_idempotent
    app_state = make_app_state(
        cursor_secret=CursorSecret.from_key("test-key-32-bytes-padding0000000"),
        slices={
            BackupStateSlice: {"service": service},
            ApiCoreStateSlice: {"idempotency_service": idempotency_service},
        },
    )
    return State({"app_state": app_state}), service


class TestRequiredIdempotencyKey:
    """The header is declared mandatory and the handler delegates to the service."""

    def test_signature_marks_idempotency_key_required(self) -> None:
        sig = inspect.signature(BackupController.create_backup.fn)
        param = sig.parameters["idempotency_key"]
        # A required parameter has no default. Annotated[str, Parameter(...)]
        # without a default value reflects the required header.
        assert param.default is inspect.Parameter.empty

    async def test_handler_invokes_idempotency_service(self) -> None:
        captured: dict[str, object] = {}

        async def fake_run_idempotent(
            *,
            scope: object,
            key: object,
            callback: Callable[[], Awaitable[object]],
        ) -> IdempotencyResult:
            captured["scope"] = scope
            captured["key"] = key
            await callback()
            return IdempotencyResult(
                result=_make_manifest().model_dump(mode="json"),
                fresh=True,
                timed_out=False,
            )

        ctrl = BackupController(owner=BackupController)  # type: ignore[arg-type]
        state, service = _make_state(run_idempotent=fake_run_idempotent)
        await ctrl.create_backup.fn(
            ctrl,
            state=state,
            idempotency_key="key-abc-123",
        )
        assert str(captured["scope"]) == "backup"
        assert str(captured["key"]) == "key-abc-123"
        # The fake_run_idempotent helper above ``await callback()``-s
        # the controller's wrapper, which must delegate to
        # ``BackupService.create_backup``. Without this assertion the
        # test would pass even if the controller stopped invoking the
        # service entirely (e.g. a refactor that returned a stale
        # cached manifest from the idempotency layer without ever
        # producing a fresh one).
        service.create_backup.assert_awaited_once_with(BackupTrigger.MANUAL)
