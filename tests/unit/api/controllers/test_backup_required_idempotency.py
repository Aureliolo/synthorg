"""Idempotency-Key is mandatory for POST /admin/backups.

Per audit #133 (idempotency / retry safety): without a key, a
network-flake-driven 5xx retry could launch concurrent backups and
violate the at-most-one-running invariant. The header is now
required by Litestar's parameter validation; missing or empty values
yield HTTP 400.

The shape of these tests intentionally avoids spinning up the full
Litestar app: we inspect the route handler's parameter signature and
verify the controller correctly invokes the idempotency service when
the key is supplied.
"""

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.api.controllers.backup import BackupController
from synthorg.api.cursor import CursorSecret
from synthorg.backup.models import (
    BackupComponent,
    BackupManifest,
    BackupTrigger,
)

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


def _make_state(*, run_idempotent: Any) -> MagicMock:
    service = AsyncMock()
    service.create_backup = AsyncMock(return_value=_make_manifest())
    app_state = MagicMock()
    app_state.backup_service = service
    idempotency_service = MagicMock()
    idempotency_service.run_idempotent = run_idempotent
    app_state.idempotency_service = idempotency_service
    app_state.cursor_secret = CursorSecret.from_key(
        "test-key-32-bytes-padding0000000",
    )
    state = MagicMock()
    state.app_state = app_state
    return state


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
            callback: Any,
        ) -> Any:
            captured["scope"] = scope
            captured["key"] = key
            await callback()
            outcome = MagicMock()
            outcome.timed_out = False
            outcome.result = _make_manifest().model_dump(mode="json")
            outcome.fresh = True
            return outcome

        ctrl = BackupController(owner=BackupController)  # type: ignore[arg-type]
        state = _make_state(run_idempotent=fake_run_idempotent)
        await ctrl.create_backup.fn(
            ctrl,
            state=state,
            idempotency_key="key-abc-123",
        )
        assert str(captured["scope"]) == "backup"
        assert str(captured["key"]) == "key-abc-123"
