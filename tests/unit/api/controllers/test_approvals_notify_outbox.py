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
        self.restored: ApprovalItem | None = None

    async def save_if_pending(self, item: ApprovalItem) -> ApprovalItem:
        self.saved = item
        return item

    async def save(self, item: ApprovalItem) -> ApprovalItem:
        self.restored = item
        return item


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    store: _FakeStore,
    *,
    cleared: list[str] | None = None,
) -> list[datetime]:
    """Stub every collaborator except the ordering under test.

    Returns:
        The wall-clock times at which the marker was recorded.
    """
    recorded_at: list[datetime] = []

    async def _record(_app_state: object, _approval_id: str) -> None:
        recorded_at.append(datetime.now(UTC))

    async def _clear(_app_state: object, approval_id: str) -> None:
        if cleared is not None:
            cleared.append(approval_id)

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
    monkeypatch.setattr(notify_mod, "clear_resume_intent", _clear)
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


class TestSaveDecisionAndNotifyResumeFailure:
    """A failed dispatch must leave the approval decidable again.

    Without the rollback the approval stays decided while nothing resumed:
    every dashboard retry hits ``ConflictError`` and the operator's decision
    is stranded until the next process restart drains the marker.
    """

    @staticmethod
    def _boom(monkeypatch: pytest.MonkeyPatch) -> None:
        async def _fail(*_args: object, **_kwargs: object) -> None:
            msg = "resume routing down"
            raise RuntimeError(msg)

        monkeypatch.setattr(notify_mod, "signal_resume_intent", _fail)

    async def _decide(self, store: _FakeStore) -> None:
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

    async def test_failure_restores_the_pending_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _FakeStore()
        _patch(monkeypatch, store)
        self._boom(monkeypatch)

        with pytest.raises(RuntimeError):
            await self._decide(store)

        assert store.restored is not None
        assert store.restored.status is ApprovalStatus.PENDING
        assert store.restored.decided_at is None
        assert store.restored.decided_by is None

    async def test_failure_clears_the_marker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The approval is back to PENDING, so the marker has nothing left to
        # recover and must not be re-dispatched by a later drain.
        store = _FakeStore()
        cleared: list[str] = []
        _patch(monkeypatch, store, cleared=cleared)
        self._boom(monkeypatch)

        with pytest.raises(RuntimeError):
            await self._decide(store)

        assert cleared == ["ap-1"]

    async def test_failure_is_re_raised_not_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _FakeStore()
        _patch(monkeypatch, store)
        self._boom(monkeypatch)

        with pytest.raises(RuntimeError, match="resume routing down"):
            await self._decide(store)
