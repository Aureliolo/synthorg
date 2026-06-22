"""Unit tests for the webhook receipt retry endpoint.

The retry handler updates the persisted receipt status, re-publishes
the captured payload to the bus, then marks the receipt
``received``. The tests below pin the lifecycle: status transitions
are logged in order, ``update_status`` boolean results gate the
log emission, a publish failure marks the receipt ``failed`` and
re-raises, and an unknown receipt id raises ``NotFoundError``.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest
import structlog.testing
from litestar import Router
from litestar.datastructures import State

from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.controllers.webhooks import _shared as webhooks_shared
from synthorg.api.controllers.webhooks.retry import WebhooksRetryController
from synthorg.communication.bus_protocol import MessageBus
from synthorg.core.domain_errors import ConflictError, NotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.idempotency import (
    IdempotencyResult,
    IdempotencyService,
)
from synthorg.integrations.connections.models import WebhookReceipt
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.integrations.webhooks.receipt_service import WebhookReceiptService
from synthorg.observability.events.integrations import (
    WEBHOOK_RECEIPT_STATUS_TRANSITIONED,
)
from synthorg.persistence.connection_protocol import WebhookReceiptRepository
from tests._shared import as_pk, make_app_state, mock_of, sid

_RCPT_ID = sid("rcpt-retry")


async def _passthrough_run_idempotent(
    *,
    scope: str,
    key: str,
    callback: Callable[[], Awaitable[object]],
) -> IdempotencyResult:
    """Invoke the callback directly and return its result as fresh.

    Tests assert on the controller's transition lifecycle (CAS + plain
    status writes + log emission). The real ``IdempotencyService``
    semantics (claim/poll/replay) are tested in
    ``tests/unit/api/services/test_idempotency_service.py``; here we pass
    through so the controller's callback runs exactly once and its
    exceptions reach the test as-is.
    """
    del scope, key
    result = await callback()
    return IdempotencyResult(result=result, fresh=True, timed_out=False)


# Litestar's ``@post`` decorator wraps the controller method into an
# ``HTTPRouteHandler``; the original async function is preserved at
# ``.fn`` and takes ``self`` as the first arg. Calling it directly
# bypasses Litestar's request parsing -- these tests exercise the
# orchestrator branch logic, not the framework wiring.
_retry_receipt_fn = WebhooksRetryController.retry_receipt.fn


def _make_receipt(  # noqa: PLR0913 -- kw-only test fixture builder
    *,
    receipt_id: str = _RCPT_ID,
    connection_name: str = "conn-a",
    event_type: str = "issues.opened",
    status: str = "failed",
    payload_json: str = '{"hello": "world"}',
    error: str | None = "previous failure",
) -> WebhookReceipt:
    """Build a :class:`WebhookReceipt` fixture for the retry path."""
    return WebhookReceipt(
        id=as_pk(receipt_id),
        connection_name=NotBlankStr(connection_name),
        event_type=event_type,
        status=status,
        received_at=datetime(2026, 5, 13, 10, 0, 0, tzinfo=UTC),
        processed_at=None,
        payload_json=payload_json,
        error=error,
    )


def _build_state(
    *,
    receipt: WebhookReceipt | None,
    cas_result: bool = True,
    plain_results: list[bool] | None = None,
) -> tuple[State, AsyncMock, AsyncMock, AsyncMock]:
    """Build a minimal Litestar State dict with the persistence stubs.

    Returns ``(state, get_mock, cas_mock, plain_mock)`` so tests can
    assert that the controller used the CAS write
    (``update_status_if_current``) for the initial ``failed -> retrying``
    transition AND used the plain write (``update_status``) for the
    follow-up ``retrying -> received`` / ``retrying -> failed``
    transitions. Sharing one mock between the two methods (the prior
    design) would let a regression silently swap the CAS for a plain
    update_status on the retrying claim and the double-delivery fix
    would go unpinned.

    ``cas_result`` controls the single CAS return; ``plain_results``
    drives the sequential follow-up ``update_status`` returns
    (default ``[True]`` for the happy ``retrying -> received`` flow).
    """
    if plain_results is None:
        plain_results = [True]

    get_mock = AsyncMock(return_value=receipt)
    cas_mock = AsyncMock(return_value=cas_result)
    plain_mock = AsyncMock(side_effect=plain_results)

    receipts_repo = mock_of[WebhookReceiptRepository](
        get=get_mock,
        update_status=plain_mock,
        update_status_if_current=cas_mock,
    )
    app_state = make_app_state(
        message_bus=mock_of[MessageBus](),
        slices={
            ApiCoreStateSlice: {
                "idempotency_service": mock_of[IdempotencyService](
                    run_idempotent=_passthrough_run_idempotent,
                ),
            },
            IntegrationsStateSlice: {
                "webhook_receipt_service": WebhookReceiptService(
                    receipts_repo=receipts_repo,
                ),
            },
        },
    )
    state = State({"app_state": app_state})
    return state, get_mock, cas_mock, plain_mock


def _self_stub() -> Any:  # type: ignore[explicit-any]  # MagicMock self stub for the unwrapped route handler
    """Return a stand-in for the controller's bound ``self``.

    The function under test never touches ``self`` (the route handler
    is a thin orchestrator over module-level helpers + persistence),
    so a ``Router``-shaped MagicMock satisfies the parameter without
    pulling in the full Litestar app wiring.
    """
    return MagicMock(spec=Router)


@pytest.mark.unit
class TestRetryReceiptHappyPath:
    """Happy path: retrying -> publish -> received."""

    async def test_full_lifecycle_logs_and_returns_receipt_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two status transitions are logged and the response carries the id."""
        receipt = _make_receipt()
        state, _, cas_mock, plain_mock = _build_state(receipt=receipt)

        async def fake_publish(**_kwargs: object) -> dict[str, object]:
            return {"status": "accepted", "event_type": "issues.opened"}

        monkeypatch.setattr(
            webhooks_shared,
            "_publish_webhook_event_and_log",
            fake_publish,
        )

        with structlog.testing.capture_logs() as logs:
            response = await _retry_receipt_fn(
                _self_stub(),
                state=state,
                receipt_id=_RCPT_ID,
            )

        assert response.data == {
            "status": "accepted",
            "event_type": "issues.opened",
            "receipt_id": _RCPT_ID,
        }
        transitions = [
            e for e in logs if e.get("event") == WEBHOOK_RECEIPT_STATUS_TRANSITIONED
        ]
        assert len(transitions) == 2
        assert transitions[0]["status"] == "retrying"
        assert transitions[0]["previous_status"] == "failed"
        assert transitions[1]["status"] == "received"
        assert transitions[1]["previous_status"] == "retrying"
        # Retrying claim MUST go through the CAS write (TOCTOU guard);
        # the follow-up ``received`` write is the plain ``update_status``.
        # Asserting the two mocks separately pins the contract: a
        # regression that swapped the claim back to plain
        # ``update_status`` would still satisfy a combined
        # ``await_count == 2`` assertion but would leave
        # ``cas_mock.await_count == 0``, which this catches.
        #
        # ``_with`` further pins the kwargs so a regression that flips
        # ``expected_status`` away from ``"failed"`` or the target
        # ``status`` away from ``"retrying"`` / ``"received"`` would
        # surface here rather than silently shipping the wrong contract.
        cas_mock.assert_awaited_once_with(
            _RCPT_ID,
            expected_status="failed",
            status="retrying",
            processed_at=None,
            error=None,
        )
        plain_mock.assert_awaited_once_with(
            _RCPT_ID,
            status="received",
            processed_at=ANY,
            error=None,
        )

    async def test_invalid_payload_json_wraps_raw_bytes(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Garbled payload_json is wrapped as ``{"raw": ...}`` for publish."""
        receipt = _make_receipt(payload_json="not-json-{")
        state, _, _, _ = _build_state(receipt=receipt)
        captured: dict[str, object] = {}

        async def fake_publish(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {"status": "accepted", "event_type": "issues.opened"}

        monkeypatch.setattr(
            webhooks_shared,
            "_publish_webhook_event_and_log",
            fake_publish,
        )

        await _retry_receipt_fn(
            _self_stub(),
            state=state,
            receipt_id=_RCPT_ID,
        )
        assert captured["payload"] == {"raw": "not-json-{"}

    async def test_list_payload_wrapped_under_data_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A non-mapping JSON payload normalises into ``{"data": ...}``."""
        receipt = _make_receipt(payload_json="[1, 2, 3]")
        state, _, _, _ = _build_state(receipt=receipt)
        captured: dict[str, object] = {}

        async def fake_publish(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {"status": "accepted", "event_type": "issues.opened"}

        monkeypatch.setattr(
            webhooks_shared,
            "_publish_webhook_event_and_log",
            fake_publish,
        )

        await _retry_receipt_fn(
            _self_stub(),
            state=state,
            receipt_id=_RCPT_ID,
        )
        assert captured["payload"] == {"data": [1, 2, 3]}

    async def test_empty_payload_wraps_as_raw_empty_string(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An empty ``payload_json`` preserves the ``{"raw": ""}`` envelope.

        Pins the contract that a zero-byte webhook body persisted to
        ``payload_json=""`` retries with the same envelope shape
        (``{"raw": ""}``) that ``receive_webhook`` would have published
        on the original delivery. A regression that short-circuits the
        empty string to ``{}`` would silently change the retry payload
        shape relative to the first attempt.
        """
        receipt = _make_receipt(payload_json="")
        state, _, _, _ = _build_state(receipt=receipt)
        captured: dict[str, object] = {}

        async def fake_publish(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {"status": "accepted", "event_type": "issues.opened"}

        monkeypatch.setattr(
            webhooks_shared,
            "_publish_webhook_event_and_log",
            fake_publish,
        )

        await _retry_receipt_fn(
            _self_stub(),
            state=state,
            receipt_id=_RCPT_ID,
        )
        assert captured["payload"] == {"raw": ""}


@pytest.mark.unit
class TestRetryReceiptErrorPaths:
    """404 / publish-failure / missing-on-update branches."""

    async def test_missing_receipt_raises_not_found(self) -> None:
        """``get`` returning ``None`` yields ``NotFoundError`` before any write."""
        state, _, cas_mock, plain_mock = _build_state(receipt=None)
        with pytest.raises(NotFoundError):
            await _retry_receipt_fn(
                _self_stub(),
                state=state,
                receipt_id="rcpt-missing",
            )
        cas_mock.assert_not_awaited()
        plain_mock.assert_not_awaited()

    async def test_update_status_returns_false_raises_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``update_status -> False`` on the first call yields ``NotFoundError``.

        Simulates the row being deleted between the ``get`` and the
        ``update_status`` call: the receipt was visible at lookup
        time but no longer exists when the retry write fires. The
        endpoint must surface this as a 404 instead of silently
        emitting a log claim against a row the DB never wrote.
        """
        receipt = _make_receipt()
        state, _, cas_mock, plain_mock = _build_state(
            receipt=receipt,
            cas_result=False,
        )

        async def fake_publish(
            **_kwargs: object,
        ) -> dict[str, object]:  # pragma: no cover
            msg = "publish must not run after update_status returns False"
            raise AssertionError(msg)

        monkeypatch.setattr(
            webhooks_shared,
            "_publish_webhook_event_and_log",
            fake_publish,
        )

        with pytest.raises(NotFoundError):
            await _retry_receipt_fn(
                _self_stub(),
                state=state,
                receipt_id=_RCPT_ID,
            )
        # CAS fired once and lost; no plain update_status follow-up.
        cas_mock.assert_awaited_once()
        plain_mock.assert_not_awaited()

    async def test_publish_failure_marks_receipt_failed_and_reraises(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A bus publish exception transitions to ``failed`` and propagates.

        Without the ``try/except`` the receipt would remain pinned
        at ``retrying`` forever; the test pins the ``failed``
        transition + log emission so the regression cannot land.
        """
        receipt = _make_receipt()
        # CAS wins for the retrying claim; plain update_status fires
        # once more on the exception path to flip the row back to
        # failed so a future retry can pick it up again.
        state, _, cas_mock, plain_mock = _build_state(
            receipt=receipt,
            cas_result=True,
            plain_results=[True],
        )
        publish_error = RuntimeError("bus is wedged")

        async def fake_publish(**_kwargs: object) -> dict[str, object]:
            raise publish_error

        monkeypatch.setattr(
            webhooks_shared,
            "_publish_webhook_event_and_log",
            fake_publish,
        )

        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(RuntimeError, match="bus is wedged"),
        ):
            await _retry_receipt_fn(
                _self_stub(),
                state=state,
                receipt_id=_RCPT_ID,
            )

        transitions = [
            e for e in logs if e.get("event") == WEBHOOK_RECEIPT_STATUS_TRANSITIONED
        ]
        assert len(transitions) == 2
        assert transitions[0]["status"] == "retrying"
        assert transitions[1]["status"] == "failed"
        assert transitions[1]["previous_status"] == "retrying"
        cas_mock.assert_awaited_once_with(
            _RCPT_ID,
            expected_status="failed",
            status="retrying",
            processed_at=None,
            error=None,
        )
        # Failure-path follow-up: plain update_status flips to ``failed``
        # with the publish error's safe description and a processed_at
        # timestamp. Pinning ``status="failed"`` here prevents a
        # regression that accidentally leaves the row in ``retrying``
        # after a publish failure (which would block future retries).
        plain_mock.assert_awaited_once_with(
            _RCPT_ID,
            status="failed",
            processed_at=ANY,
            error=ANY,
        )

    async def test_cas_lost_race_raises_not_found_without_publishing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A concurrent retry that already flipped the row loses the CAS.

        Simulates two operator-triggered retries hitting the same
        receipt: the first wins the compare-and-set transition to
        ``retrying`` and proceeds to publish; the second sees its CAS
        return ``False`` (the row's status no longer matches the
        ``expected_status`` the late caller passed) and surfaces
        ``NotFoundError`` instead of double-publishing the payload.
        Without the CAS this loser would have flipped the row a
        second time and re-published.
        """
        receipt = _make_receipt()
        # CAS returns False -- the late caller lost the race. publish
        # must NEVER run after that and no plain update_status follow-up
        # may fire either (otherwise the loser would still flip the
        # row a second time).
        state, _, cas_mock, plain_mock = _build_state(
            receipt=receipt,
            cas_result=False,
        )

        async def fake_publish(
            **_kwargs: object,
        ) -> dict[str, object]:  # pragma: no cover
            msg = "publish must not run after a lost CAS"
            raise AssertionError(msg)

        monkeypatch.setattr(
            webhooks_shared,
            "_publish_webhook_event_and_log",
            fake_publish,
        )

        with pytest.raises(NotFoundError):
            await _retry_receipt_fn(
                _self_stub(),
                state=state,
                receipt_id=_RCPT_ID,
            )
        # Only the CAS write fired; no follow-up plain update_status.
        cas_mock.assert_awaited_once()
        plain_mock.assert_not_awaited()

    @pytest.mark.parametrize(
        "non_failed_status",
        ["received", "retrying", "rejected", "delivered"],
    )
    async def test_retry_rejects_non_failed_receipts_with_conflict(
        self,
        monkeypatch: pytest.MonkeyPatch,
        non_failed_status: str,
    ) -> None:
        """Only ``failed`` receipts are retryable; everything else raises 409.

        The retry endpoint exists to re-publish deliveries that the
        downstream consumer failed. Letting a stale dashboard link
        replay a ``received`` receipt would double-publish a delivery
        that already succeeded; letting one replay a ``retrying`` row
        would race against the in-flight attempt. The guard short-
        circuits both before any persistence write or bus publish.
        """
        receipt = _make_receipt(status=non_failed_status)
        state, _, cas_mock, plain_mock = _build_state(receipt=receipt)

        async def fake_publish(
            **_kwargs: object,
        ) -> dict[str, object]:  # pragma: no cover
            msg = "publish must not run when the receipt is not failed"
            raise AssertionError(msg)

        monkeypatch.setattr(
            webhooks_shared,
            "_publish_webhook_event_and_log",
            fake_publish,
        )

        with pytest.raises(ConflictError):
            await _retry_receipt_fn(
                _self_stub(),
                state=state,
                receipt_id=_RCPT_ID,
            )
        # Neither write fires on the rejection path -- the guard
        # short-circuits before any persistence call.
        cas_mock.assert_not_awaited()
        plain_mock.assert_not_awaited()
