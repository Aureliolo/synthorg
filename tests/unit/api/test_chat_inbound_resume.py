"""Tests for the approval-backed inbound resume dispatcher."""

from datetime import UTC, datetime

import pytest

import synthorg.api.chat_inbound_resume as resume_mod
from synthorg.api.chat_inbound_resume import ApprovalResumeDispatcher
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.approval import ApprovalItem
from tests._shared import as_uuid
from tests._shared.mock_of import mock_of

pytestmark = pytest.mark.unit


def _item(status: ApprovalStatus = ApprovalStatus.PENDING) -> ApprovalItem:
    now = datetime.now(UTC)
    decided = status is not ApprovalStatus.PENDING
    return ApprovalItem(
        id=as_uuid("ap-1"),
        action_type="mcp:invoke",
        title="Destructive MCP call",
        description="delete a repo",
        requested_by="agent-dev",
        risk_level=ApprovalRiskLevel.HIGH,
        status=status,
        created_at=now,
        decided_at=now if decided else None,
        decided_by="prior-decider" if decided else None,
        decision_reason="already handled" if decided else None,
        task_id="task-9",
    )


class _FakeStore:
    def __init__(self, item: ApprovalItem | None, *, save_pending: bool = True) -> None:
        self._item = item
        self._save_pending = save_pending
        self.saved: ApprovalItem | None = None
        self.restored: ApprovalItem | None = None

    async def get(self, _approval_id: str) -> ApprovalItem | None:
        return self._item

    async def save_if_pending(self, item: ApprovalItem) -> ApprovalItem | None:
        if not self._save_pending:
            return None
        self.saved = item
        return item

    async def save(self, item: ApprovalItem) -> ApprovalItem:
        self.restored = item
        return item


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    store: _FakeStore,
    outbox: list[str] | None = None,
) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []
    trail = outbox if outbox is not None else []

    async def _fake_signal(
        app_state: object, approval_id: str, **kwargs: object
    ) -> None:
        trail.append("dispatch")
        calls.append({"approval_id": approval_id, **kwargs})

    async def _fake_record(_app_state: object, _approval_id: str) -> None:
        trail.append("record")

    async def _fake_clear(_app_state: object, _approval_id: str) -> None:
        trail.append("clear")

    monkeypatch.setattr(resume_mod, "approval_store_of", lambda _s: store)
    monkeypatch.setattr(resume_mod, "signal_resume_intent", _fake_signal)
    monkeypatch.setattr(resume_mod, "record_resume_intent", _fake_record)
    monkeypatch.setattr(resume_mod, "clear_resume_intent", _fake_clear)
    return calls


class TestApprovalResumeDispatcher:
    async def test_unknown_approval_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _patch(monkeypatch, _FakeStore(None))
        dispatcher = ApprovalResumeDispatcher(app_state=mock_of[AppState]())
        result = await dispatcher.resume(
            approval_id="ap-1", approved=True, decided_by="U1", decision_reason="go"
        )
        assert result is False
        assert calls == []

    async def test_already_decided_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _patch(monkeypatch, _FakeStore(_item(ApprovalStatus.APPROVED)))
        dispatcher = ApprovalResumeDispatcher(app_state=mock_of[AppState]())
        result = await dispatcher.resume(
            approval_id="ap-1", approved=True, decided_by="U1", decision_reason="go"
        )
        assert result is False
        assert calls == []

    async def test_pending_records_and_resumes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _FakeStore(_item())
        calls = _patch(monkeypatch, store)
        dispatcher = ApprovalResumeDispatcher(app_state=mock_of[AppState]())
        result = await dispatcher.resume(
            approval_id="ap-1",
            approved=True,
            decided_by="U1",
            decision_reason="proceed",
        )
        assert result is True
        assert store.saved is not None
        assert store.saved.status is ApprovalStatus.APPROVED
        assert store.saved.decided_by == "U1"
        assert calls[0]["approval_id"] == "ap-1"
        assert calls[0]["approved"] is True
        assert calls[0]["task_id"] == "task-9"

    async def test_reject_records_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _FakeStore(_item())
        _patch(monkeypatch, store)
        dispatcher = ApprovalResumeDispatcher(app_state=mock_of[AppState]())
        result = await dispatcher.resume(
            approval_id="ap-1", approved=False, decided_by="U1", decision_reason="no"
        )
        assert result is True
        assert store.saved is not None
        assert store.saved.status is ApprovalStatus.REJECTED

    async def test_dispatch_failure_restores_pending(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed resume must leave the approval decidable again.

        The decision is persisted before the hand-off, so a dispatch that
        blows up would otherwise strand the parked task: no longer
        PENDING, so neither a redelivered event nor the dashboard could
        act on it.
        """
        store = _FakeStore(_item())
        _patch(monkeypatch, store)

        async def _boom(*_args: object, **_kwargs: object) -> None:
            msg = "resume routing down"
            raise RuntimeError(msg)

        monkeypatch.setattr(resume_mod, "signal_resume_intent", _boom)
        dispatcher = ApprovalResumeDispatcher(app_state=mock_of[AppState]())
        result = await dispatcher.resume(
            approval_id="ap-1", approved=True, decided_by="U1", decision_reason="go"
        )
        assert result is False
        assert store.restored is not None
        assert store.restored.status is ApprovalStatus.PENDING

    async def test_lost_pending_race_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _FakeStore(_item(), save_pending=False)
        calls = _patch(monkeypatch, store)
        dispatcher = ApprovalResumeDispatcher(app_state=mock_of[AppState]())
        result = await dispatcher.resume(
            approval_id="ap-1", approved=True, decided_by="U1", decision_reason="go"
        )
        assert result is False
        assert calls == []


class TestApprovalResumeDispatcherOutbox:
    """The crash-recovery marker brackets the decision at this call site."""

    async def test_marker_is_recorded_before_the_decision_write(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Recording after the decision would leave the very window the
        # marker exists to close: decided, unresumed, and unrecoverable.
        trail: list[str] = []
        _patch(monkeypatch, _FakeStore(_item()), trail)
        dispatcher = ApprovalResumeDispatcher(app_state=mock_of[AppState]())
        await dispatcher.resume(
            approval_id="ap-1", approved=True, decided_by="U1", decision_reason="go"
        )
        assert trail == ["record", "dispatch", "clear"]

    async def test_marker_is_left_alone_when_the_pending_race_is_lost(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A concurrent winner may own an in-flight resume behind that
        # marker; clearing it here would delete the winner's safety net.
        # The drain retires an unowned marker on its own.
        trail: list[str] = []
        _patch(monkeypatch, _FakeStore(_item(), save_pending=False), trail)
        dispatcher = ApprovalResumeDispatcher(app_state=mock_of[AppState]())
        await dispatcher.resume(
            approval_id="ap-1", approved=True, decided_by="U1", decision_reason="go"
        )
        assert trail == ["record"]

    async def test_marker_is_cleared_when_the_dispatch_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The approval is rolled back to PENDING on this path, so the
        # marker has nothing left to recover.
        trail: list[str] = []
        _patch(monkeypatch, _FakeStore(_item()), trail)

        async def _boom(*_args: object, **_kwargs: object) -> None:
            msg = "resume routing down"
            raise RuntimeError(msg)

        monkeypatch.setattr(resume_mod, "signal_resume_intent", _boom)
        dispatcher = ApprovalResumeDispatcher(app_state=mock_of[AppState]())
        await dispatcher.resume(
            approval_id="ap-1", approved=True, decided_by="U1", decision_reason="go"
        )
        assert trail == ["record", "clear"]

    async def test_marker_is_recorded_before_the_decision_timestamp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``recorded_at`` must not postdate ``decided_at``.

        The drain retires any marker recorded after the decision, so
        stamping ``decided_at`` first would make every genuine marker look
        stale and silently disable crash recovery on this path.
        """
        store = _FakeStore(_item())
        recorded_at: list[datetime] = []
        _patch(monkeypatch, store)

        async def _record(_app_state: object, _approval_id: str) -> None:
            recorded_at.append(datetime.now(UTC))

        monkeypatch.setattr(resume_mod, "record_resume_intent", _record)
        dispatcher = ApprovalResumeDispatcher(app_state=mock_of[AppState]())
        await dispatcher.resume(
            approval_id="ap-1", approved=True, decided_by="U1", decision_reason="go"
        )

        assert store.saved is not None
        assert store.saved.decided_at is not None
        assert recorded_at[0] <= store.saved.decided_at

    async def test_no_marker_when_the_approval_is_not_pending(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trail: list[str] = []
        _patch(monkeypatch, _FakeStore(_item(ApprovalStatus.APPROVED)), trail)
        dispatcher = ApprovalResumeDispatcher(app_state=mock_of[AppState]())
        await dispatcher.resume(
            approval_id="ap-1", approved=True, decided_by="U1", decision_reason="go"
        )
        assert trail == []
