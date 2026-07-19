"""Unit tests for the out-of-band :class:`SecretCaptureService`.

Verify the single-use / TTL / binding invariants that keep a captured
secret out of the conversation while remaining resolvable exactly once by
``connections.create``.
"""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.secret_capture import (
    PendingSecretCapture,
    SecretCaptureService,
    resolve_credential_handles,
)
from synthorg.integrations.errors import SecretCaptureHandleInvalidError
from tests._shared import FakeClock, InMemorySecretBackend

pytestmark = pytest.mark.unit

_DRAFT = NotBlankStr("draft-1")
_FIELD = NotBlankStr("token")
_KIND = NotBlankStr("token")
_VALUE = "ghp_supersecretsentinel000000000000000000"


def _service(
    backend: InMemorySecretBackend,
    clock: FakeClock,
) -> SecretCaptureService:
    return SecretCaptureService(secret_backend=backend, clock=clock, ttl_seconds=600)


async def _capture(service: SecretCaptureService) -> str:
    return await service.capture(
        draft_id=_DRAFT,
        field_name=_FIELD,
        secret_kind=_KIND,
        value=_VALUE,
    )


async def test_capture_then_consume_returns_value() -> None:
    backend = InMemorySecretBackend()
    service = _service(backend, FakeClock())
    handle = await _capture(service)

    value = await service.consume(handle_id=handle, draft_id=_DRAFT, field_name=_FIELD)

    assert value == _VALUE
    # The backing secret is deleted after a successful consume.
    assert backend.stored_count() == 0


async def test_consume_is_single_use() -> None:
    backend = InMemorySecretBackend()
    service = _service(backend, FakeClock())
    handle = await _capture(service)

    await service.consume(handle_id=handle, draft_id=_DRAFT, field_name=_FIELD)

    with pytest.raises(SecretCaptureHandleInvalidError):
        await service.consume(handle_id=handle, draft_id=_DRAFT, field_name=_FIELD)


async def test_consume_rejects_expired_handle() -> None:
    backend = InMemorySecretBackend()
    clock = FakeClock()
    service = _service(backend, clock)
    handle = await _capture(service)

    clock.advance(601)

    with pytest.raises(SecretCaptureHandleInvalidError):
        await service.consume(handle_id=handle, draft_id=_DRAFT, field_name=_FIELD)
    # The expired handle's backing secret is swept on the rejected consume.
    assert backend.stored_count() == 0


async def test_consume_rejects_wrong_field_binding() -> None:
    backend = InMemorySecretBackend()
    service = _service(backend, FakeClock())
    handle = await _capture(service)

    with pytest.raises(SecretCaptureHandleInvalidError):
        await service.consume(
            handle_id=handle,
            draft_id=_DRAFT,
            field_name=NotBlankStr("password"),
        )


async def test_consume_rejects_wrong_draft_binding() -> None:
    backend = InMemorySecretBackend()
    service = _service(backend, FakeClock())
    handle = await _capture(service)

    with pytest.raises(SecretCaptureHandleInvalidError):
        await service.consume(
            handle_id=handle,
            draft_id=NotBlankStr("draft-2"),
            field_name=_FIELD,
        )


async def test_consume_rejects_unknown_handle() -> None:
    service = _service(InMemorySecretBackend(), FakeClock())
    with pytest.raises(SecretCaptureHandleInvalidError):
        await service.consume(
            handle_id="sech_nope",
            draft_id=_DRAFT,
            field_name=_FIELD,
        )


async def test_purge_expired_sweeps_backing_secrets() -> None:
    backend = InMemorySecretBackend()
    clock = FakeClock()
    service = _service(backend, clock)
    await _capture(service)
    assert backend.stored_count() == 1

    clock.advance(601)
    purged = await service.purge_expired()

    assert purged == 1
    assert backend.stored_count() == 0


async def test_handle_is_opaque_and_unique() -> None:
    service = _service(InMemorySecretBackend(), FakeClock())
    first = await _capture(service)
    second = await _capture(service)

    assert first.startswith("sech_")
    assert first != second


async def test_resolve_credential_handles_merges_secret_and_inline() -> None:
    # The shared resolver both REST and MCP create paths use: inline
    # non-secret fields merge with out-of-band handle-resolved secrets, and
    # the raw value only ever exists in-process here.
    service = _service(InMemorySecretBackend(), FakeClock())
    handle = await _capture(service)

    resolved = await resolve_credential_handles(
        service,
        credentials={"username": "svc-account"},
        credential_handles={_FIELD: handle},
        connection_draft_id=_DRAFT,
    )

    assert resolved == {"username": "svc-account", _FIELD: _VALUE}


async def test_resolve_credential_handles_no_handles_is_passthrough() -> None:
    service = _service(InMemorySecretBackend(), FakeClock())

    resolved = await resolve_credential_handles(
        service,
        credentials={"username": "svc-account"},
        credential_handles={},
        connection_draft_id=_DRAFT,
    )

    assert resolved == {"username": "svc-account"}


async def test_resolve_credential_handles_rejects_invalid_handle() -> None:
    service = _service(InMemorySecretBackend(), FakeClock())

    with pytest.raises(SecretCaptureHandleInvalidError):
        await resolve_credential_handles(
            service,
            credentials={},
            credential_handles={_FIELD: "sech_bogus"},
            connection_draft_id=_DRAFT,
        )


def _pending(field: str) -> PendingSecretCapture:
    return PendingSecretCapture(
        draft_id=_DRAFT,
        connection_type=NotBlankStr("database"),
        field_name=NotBlankStr(field),
        secret_kind=NotBlankStr(field),
        label=NotBlankStr(field.title()),
    )


def test_take_pending_consumes_registered_requests() -> None:
    # The in-chat capture signal: request_secret_capture registers, the console
    # reads-and-clears once per turn so a request is surfaced exactly once.
    service = _service(InMemorySecretBackend(), FakeClock())
    service.register_pending(_pending("password"))

    first = service.take_pending(_DRAFT)
    second = service.take_pending(_DRAFT)

    assert [p.field_name for p in first] == ["password"]
    assert second == ()


def test_register_pending_dedupes_by_field() -> None:
    service = _service(InMemorySecretBackend(), FakeClock())
    service.register_pending(_pending("password"))
    replacement = PendingSecretCapture(
        draft_id=_DRAFT,
        connection_type=NotBlankStr("database"),
        field_name=NotBlankStr("password"),
        secret_kind=NotBlankStr("password"),
        label=NotBlankStr("Database password (re-asked)"),
    )
    service.register_pending(replacement)

    pending = service.take_pending(_DRAFT)
    assert len(pending) == 1
    # Last write wins: the re-asked field replaces the earlier one rather than
    # stacking, so a first-write-wins implementation would fail this.
    assert pending[0].label == replacement.label


def test_take_pending_is_scoped_by_draft() -> None:
    service = _service(InMemorySecretBackend(), FakeClock())
    service.register_pending(_pending("password"))

    assert service.take_pending(NotBlankStr("other-draft")) == ()
    assert len(service.take_pending(_DRAFT)) == 1
