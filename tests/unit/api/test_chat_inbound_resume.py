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

    async def get(self, _approval_id: str) -> ApprovalItem | None:
        return self._item

    async def save_if_pending(self, item: ApprovalItem) -> ApprovalItem | None:
        if not self._save_pending:
            return None
        self.saved = item
        return item


def _patch(
    monkeypatch: pytest.MonkeyPatch, store: _FakeStore
) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    async def _fake_signal(
        app_state: object, approval_id: str, **kwargs: object
    ) -> None:
        calls.append({"approval_id": approval_id, **kwargs})

    monkeypatch.setattr(resume_mod, "approval_store_of", lambda _s: store)
    monkeypatch.setattr(resume_mod, "signal_resume_intent", _fake_signal)
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
