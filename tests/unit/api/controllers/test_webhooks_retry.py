"""Unit tests for the webhook receipt retry endpoint.

The retry handler updates the persisted receipt status, re-publishes
the captured payload to the bus, then marks the receipt
``received``. The tests below pin the lifecycle: status transitions
are logged in order, ``update_status`` boolean results gate the
log emission, a publish failure marks the receipt ``failed`` and
re-raises, and an unknown receipt id raises ``NotFoundError``.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing
from litestar import Router

from synthorg.api.controllers import webhooks as webhooks_module
from synthorg.api.controllers.webhooks import WebhooksController
from synthorg.core.domain_errors import ConflictError, NotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import WebhookReceipt
from synthorg.observability.events.integrations import (
    WEBHOOK_RECEIPT_STATUS_TRANSITIONED,
)

# Litestar's ``@post`` decorator wraps the controller method into an
# ``HTTPRouteHandler``; the original async function is preserved at
# ``.fn`` and takes ``self`` as the first arg. Calling it directly
# bypasses Litestar's request parsing -- these tests exercise the
# orchestrator branch logic, not the framework wiring.
_retry_receipt_fn = WebhooksController.retry_receipt.fn


def _make_receipt(  # noqa: PLR0913 -- kw-only test fixture builder
    *,
    receipt_id: str = "rcpt-retry",
    connection_name: str = "conn-a",
    event_type: str = "issues.opened",
    status: str = "failed",
    payload_json: str = '{"hello": "world"}',
    error: str | None = "previous failure",
) -> WebhookReceipt:
    """Build a :class:`WebhookReceipt` fixture for the retry path."""
    return WebhookReceipt(
        id=NotBlankStr(receipt_id),
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
    update_results: list[bool] | None = None,
) -> tuple[dict[str, Any], AsyncMock, AsyncMock]:
    """Build a minimal Litestar State dict with the persistence stubs.

    Returns ``(state, get_mock, update_combined_mock)``. The combined
    mock counts every status-transition call regardless of whether the
    handler routed it through ``update_status`` (plain) or
    ``update_status_if_current`` (CAS for the ``retrying`` transition).
    ``update_results`` controls the boolean returned by sequential
    transition calls in order; the default ``[True, True]`` covers the
    happy retrying -> received flow.
    """
    if update_results is None:
        update_results = [True, True]

    get_mock = AsyncMock(return_value=receipt)
    # Single underlying counter so tests can keep asserting
    # ``await_count`` against the conceptual "number of status
    # transitions" without caring which method the controller called.
    combined_mock = AsyncMock(side_effect=update_results)

    async def _cas(*_args: Any, **_kwargs: Any) -> bool:
        result = await combined_mock()
        return bool(result)

    async def _plain(*_args: Any, **_kwargs: Any) -> bool:
        result = await combined_mock()
        return bool(result)

    update_cas_mock = AsyncMock(side_effect=_cas)
    update_plain_mock = AsyncMock(side_effect=_plain)

    class _WebhookReceipts:
        get = get_mock
        update_status = update_plain_mock
        update_status_if_current = update_cas_mock

    class _Persistence:
        webhook_receipts = _WebhookReceipts()

    class _AppState:
        persistence = _Persistence()
        message_bus = object()

    state: dict[str, Any] = {"app_state": _AppState()}
    return state, get_mock, combined_mock


def _self_stub() -> Any:
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
        state, _, update_mock = _build_state(receipt=receipt)

        async def fake_publish(**_kwargs: Any) -> dict[str, object]:
            return {"status": "accepted", "event_type": "issues.opened"}

        monkeypatch.setattr(
            webhooks_module,
            "_publish_webhook_event_and_log",
            fake_publish,
        )

        with structlog.testing.capture_logs() as logs:
            response = await _retry_receipt_fn(
                _self_stub(),
                state=state,
                receipt_id="rcpt-retry",
            )

        assert response.data == {
            "status": "accepted",
            "event_type": "issues.opened",
            "receipt_id": "rcpt-retry",
        }
        transitions = [
            e for e in logs if e.get("event") == WEBHOOK_RECEIPT_STATUS_TRANSITIONED
        ]
        assert len(transitions) == 2
        assert transitions[0]["status"] == "retrying"
        assert transitions[0]["previous_status"] == "failed"
        assert transitions[1]["status"] == "received"
        assert transitions[1]["previous_status"] == "retrying"
        assert update_mock.await_count == 2

    async def test_invalid_payload_json_wraps_raw_bytes(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Garbled payload_json is wrapped as ``{"raw": ...}`` for publish."""
        receipt = _make_receipt(payload_json="not-json-{")
        state, _, _ = _build_state(receipt=receipt)
        captured: dict[str, Any] = {}

        async def fake_publish(**kwargs: Any) -> dict[str, object]:
            captured.update(kwargs)
            return {"status": "accepted", "event_type": "issues.opened"}

        monkeypatch.setattr(
            webhooks_module,
            "_publish_webhook_event_and_log",
            fake_publish,
        )

        await _retry_receipt_fn(
            _self_stub(),
            state=state,
            receipt_id="rcpt-retry",
        )
        assert captured["payload"] == {"raw": "not-json-{"}

    async def test_list_payload_wrapped_under_data_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A non-mapping JSON payload normalises into ``{"data": ...}``."""
        receipt = _make_receipt(payload_json="[1, 2, 3]")
        state, _, _ = _build_state(receipt=receipt)
        captured: dict[str, Any] = {}

        async def fake_publish(**kwargs: Any) -> dict[str, object]:
            captured.update(kwargs)
            return {"status": "accepted", "event_type": "issues.opened"}

        monkeypatch.setattr(
            webhooks_module,
            "_publish_webhook_event_and_log",
            fake_publish,
        )

        await _retry_receipt_fn(
            _self_stub(),
            state=state,
            receipt_id="rcpt-retry",
        )
        assert captured["payload"] == {"data": [1, 2, 3]}


@pytest.mark.unit
class TestRetryReceiptErrorPaths:
    """404 / publish-failure / missing-on-update branches."""

    async def test_missing_receipt_raises_not_found(self) -> None:
        """``get`` returning ``None`` yields ``NotFoundError`` before any write."""
        state, _, update_mock = _build_state(receipt=None)
        with pytest.raises(NotFoundError):
            await _retry_receipt_fn(
                _self_stub(),
                state=state,
                receipt_id="rcpt-missing",
            )
        update_mock.assert_not_awaited()

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
        state, _, update_mock = _build_state(
            receipt=receipt,
            update_results=[False],
        )

        async def fake_publish(**_kwargs: Any) -> dict[str, object]:  # pragma: no cover
            msg = "publish must not run after update_status returns False"
            raise AssertionError(msg)

        monkeypatch.setattr(
            webhooks_module,
            "_publish_webhook_event_and_log",
            fake_publish,
        )

        with pytest.raises(NotFoundError):
            await _retry_receipt_fn(
                _self_stub(),
                state=state,
                receipt_id="rcpt-retry",
            )
        assert update_mock.await_count == 1

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
        # update_status is called twice: first to flip to retrying
        # (True), then to flip to failed in the exception path (True).
        state, _, update_mock = _build_state(
            receipt=receipt,
            update_results=[True, True],
        )
        publish_error = RuntimeError("bus is wedged")

        async def fake_publish(**_kwargs: Any) -> dict[str, object]:
            raise publish_error

        monkeypatch.setattr(
            webhooks_module,
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
                receipt_id="rcpt-retry",
            )

        transitions = [
            e for e in logs if e.get("event") == WEBHOOK_RECEIPT_STATUS_TRANSITIONED
        ]
        assert len(transitions) == 2
        assert transitions[0]["status"] == "retrying"
        assert transitions[1]["status"] == "failed"
        assert transitions[1]["previous_status"] == "retrying"
        assert update_mock.await_count == 2

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
        # First (and only) transition call returns False -- the CAS
        # lost the race. publish must NEVER run after that.
        state, _, update_mock = _build_state(
            receipt=receipt,
            update_results=[False],
        )

        async def fake_publish(**_kwargs: Any) -> dict[str, object]:  # pragma: no cover
            msg = "publish must not run after a lost CAS"
            raise AssertionError(msg)

        monkeypatch.setattr(
            webhooks_module,
            "_publish_webhook_event_and_log",
            fake_publish,
        )

        with pytest.raises(NotFoundError):
            await _retry_receipt_fn(
                _self_stub(),
                state=state,
                receipt_id="rcpt-retry",
            )
        # Exactly one transition was attempted (the CAS); no
        # subsequent ``received`` or ``failed`` write fired.
        assert update_mock.await_count == 1

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
        state, _, update_mock = _build_state(receipt=receipt)

        async def fake_publish(**_kwargs: Any) -> dict[str, object]:  # pragma: no cover
            msg = "publish must not run when the receipt is not failed"
            raise AssertionError(msg)

        monkeypatch.setattr(
            webhooks_module,
            "_publish_webhook_event_and_log",
            fake_publish,
        )

        with pytest.raises(ConflictError):
            await _retry_receipt_fn(
                _self_stub(),
                state=state,
                receipt_id="rcpt-retry",
            )
        # No transition write fires on the rejection path.
        update_mock.assert_not_awaited()
