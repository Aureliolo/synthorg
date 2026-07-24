"""Crash-recovery ordering on the dashboard approval-decision path.

The startup drain retires any resume marker whose ``recorded_at``
postdates the approval's ``decided_at``. The dashboard controllers build
``updated`` (timestamp included) before reaching
``_save_decision_and_notify``, so the decision timestamp must be
re-stamped there, after the marker is recorded. Getting that order wrong
makes every genuine marker look stale and silently disables crash
recovery on this path.
"""

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from litestar import Request

import synthorg.api.controllers.approvals._notify as notify_mod
from synthorg.api.controllers.approvals._notify import _save_decision_and_notify
from synthorg.api.controllers.approvals._shared import ApprovalResponse
from synthorg.api.state import AppState
from synthorg.api.ws_models import WsEventType
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.approval import ApprovalItem
from tests._shared import as_uuid
from tests._shared.mock_of import mock_of

pytestmark = pytest.mark.unit

_STALE_DECIDED_AT = datetime(2020, 1, 1, tzinfo=UTC)


def _decided_item() -> ApprovalItem:
    """Build a decided approval carrying a deliberately old timestamp.

    The old ``decided_at`` stands in for the controller having stamped it
    before this function runs.
    """
    return ApprovalItem(
        id=as_uuid("ap-1"),
        action_type="mcp:invoke",
        title="Destructive MCP call",
        description="delete a repo",
        requested_by="agent-dev",
        risk_level=ApprovalRiskLevel.HIGH,
        status=ApprovalStatus.APPROVED,
        created_at=_STALE_DECIDED_AT,
        decided_at=_STALE_DECIDED_AT,
        decided_by="operator-1",
        decision_reason="looks fine",
        task_id="task-9",
    )


class _FakeStore:
    def __init__(self) -> None:
        self.saved: ApprovalItem | None = None

    async def save_if_pending(self, item: ApprovalItem) -> ApprovalItem:
        self.saved = item
        return item


def _patch(monkeypatch: pytest.MonkeyPatch, store: _FakeStore) -> list[datetime]:
    """Stub every collaborator except the ordering under test.

    Returns:
        The wall-clock times at which the marker was recorded.
    """
    recorded_at: list[datetime] = []

    async def _record(_app_state: object, _approval_id: str) -> None:
        recorded_at.append(datetime.now(UTC))

    async def _noop_async(*_args: object, **_kwargs: object) -> None:
        return None

    def _noop(*_args: object, **_kwargs: object) -> None:
        return None

    response = cast(ApprovalResponse, mock_of[ApprovalResponse]())

    async def _publish(*_args: object, **_kwargs: object) -> ApprovalResponse:
        return response

    monkeypatch.setattr(notify_mod, "_run_review_gate_preflight", _noop_async)
    monkeypatch.setattr(notify_mod, "require_service", lambda *_a, **_k: store)
    monkeypatch.setattr(notify_mod, "record_resume_intent", _record)
    monkeypatch.setattr(notify_mod, "clear_resume_intent", _noop_async)
    monkeypatch.setattr(notify_mod, "_log_state_transition_and_metrics", _noop)
    monkeypatch.setattr(notify_mod, "_log_approval_decision", _noop)
    monkeypatch.setattr(notify_mod, "_publish_approval_event", _publish)
    monkeypatch.setattr(notify_mod, "signal_resume_intent", _noop_async)
    return recorded_at


class TestSaveDecisionAndNotifyOutboxOrdering:
    async def test_marker_is_recorded_before_the_decision_timestamp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _FakeStore()
        recorded_at = _patch(monkeypatch, store)

        await _save_decision_and_notify(
            mock_of[AppState](),
            mock_of[Request](),
            "ap-1",
            _decided_item(),
            approved=True,
            decided_by="operator-1",
            decided_by_user_id="user-1",
            previous_status=ApprovalStatus.PENDING,
            decision_reason="looks fine",
            ws_event=WsEventType.APPROVAL_APPROVED,
        )

        assert store.saved is not None
        assert store.saved.decided_at is not None
        assert recorded_at[0] <= store.saved.decided_at

    async def test_caller_supplied_timestamp_is_refreshed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The controllers stamp ``decided_at`` before calling in, so the
        # persisted value must be the refreshed one; keeping the caller's
        # would leave it earlier than the marker.
        store = _FakeStore()
        _patch(monkeypatch, store)

        await _save_decision_and_notify(
            mock_of[AppState](),
            mock_of[Request](),
            "ap-1",
            _decided_item(),
            approved=True,
            decided_by="operator-1",
            decided_by_user_id="user-1",
            previous_status=ApprovalStatus.PENDING,
            decision_reason="looks fine",
            ws_event=WsEventType.APPROVAL_APPROVED,
        )

        assert store.saved is not None
        assert store.saved.decided_at is not None
        assert store.saved.decided_at != _STALE_DECIDED_AT
        assert datetime.now(UTC) - store.saved.decided_at < timedelta(minutes=1)
