"""Tests for the approval resume-intent crash-recovery outbox."""

from datetime import UTC, datetime
from typing import override

import pytest

import synthorg.api.resume_intent_outbox as outbox_mod
from synthorg.api.resume_intent_outbox import (
    ResumeIntentDrain,
    clear_resume_intent,
    record_resume_intent,
)
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.approval import ApprovalItem
from synthorg.core.resume_intent import ResumeIntent
from synthorg.core.types import NotBlankStr
from tests._shared import as_uuid
from tests._shared.mock_of import mock_of

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


def _item(
    status: ApprovalStatus = ApprovalStatus.APPROVED,
    *,
    decision_reason: str | None = "looks fine",
) -> ApprovalItem:
    decided = status is not ApprovalStatus.PENDING
    return ApprovalItem(
        id=as_uuid("ap-1"),
        action_type="mcp:invoke",
        title="Destructive MCP call",
        description="delete a repo",
        requested_by="agent-dev",
        risk_level=ApprovalRiskLevel.HIGH,
        status=status,
        created_at=_NOW,
        decided_at=_NOW if decided else None,
        decided_by="operator-1" if decided else None,
        decision_reason=decision_reason if decided else None,
        task_id="task-9",
    )


class _FakeRepo:
    def __init__(self, *approval_ids: str, recorded_at: datetime = _NOW) -> None:
        self.rows: dict[str, ResumeIntent] = {
            approval_id: ResumeIntent(
                approval_id=NotBlankStr(approval_id), recorded_at=recorded_at
            )
            for approval_id in approval_ids
        }

    async def save(self, intent: ResumeIntent) -> None:
        # Insert-if-absent, mirroring both backends: a later caller must
        # not overwrite the earlier marker's timestamp.
        self.rows.setdefault(intent.approval_id, intent)

    async def get(self, approval_id: str) -> ResumeIntent | None:
        return self.rows.get(approval_id)

    async def delete(self, approval_id: str) -> bool:
        return self.rows.pop(approval_id, None) is not None

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[ResumeIntent, ...]:
        ordered = [self.rows[key] for key in sorted(self.rows)]
        return tuple(ordered[offset : offset + limit])


class _FakeStore:
    def __init__(self, item: ApprovalItem | None) -> None:
        self._item = item

    async def get(self, _approval_id: str) -> ApprovalItem | None:
        return self._item


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    repo: _FakeRepo | None,
    store: _FakeStore | None = None,
) -> list[dict[str, object]]:
    dispatched: list[dict[str, object]] = []

    async def _fake_signal(
        _app_state: object, approval_id: str, **kwargs: object
    ) -> None:
        dispatched.append({"approval_id": approval_id, **kwargs})

    monkeypatch.setattr(outbox_mod, "resume_intents_of", lambda _s: repo)
    monkeypatch.setattr(outbox_mod, "signal_resume_intent", _fake_signal)
    if store is not None:
        monkeypatch.setattr(outbox_mod, "approval_store_of", lambda _s: store)
    return dispatched


class TestRecordAndClear:
    async def test_record_writes_the_marker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _FakeRepo()
        _patch(monkeypatch, repo)
        await record_resume_intent(mock_of[AppState](), "ap-1")
        assert "ap-1" in repo.rows

    async def test_record_keeps_the_earliest_marker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A second caller racing the same approval must not overwrite the
        # first marker: the drain reasons about that earlier timestamp.
        repo = _FakeRepo("ap-1")
        _patch(monkeypatch, repo)
        await record_resume_intent(mock_of[AppState](), "ap-1")
        assert repo.rows["ap-1"].recorded_at == _NOW

    async def test_clear_removes_the_marker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _FakeRepo("ap-1")
        _patch(monkeypatch, repo)
        await clear_resume_intent(mock_of[AppState](), "ap-1")
        assert repo.rows == {}

    async def test_no_backend_is_a_no_op(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A run with no database cannot survive a crash anyway, so the
        # marker degrades rather than 503-ing a serviceable decision.
        _patch(monkeypatch, None)
        await record_resume_intent(mock_of[AppState](), "ap-1")
        await clear_resume_intent(mock_of[AppState](), "ap-1")

    async def test_repository_failure_does_not_propagate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Forfeiting crash recovery for one decision beats turning a
        # perfectly serviceable approval into a 500.
        class _BrokenRepo(_FakeRepo):
            @override
            async def save(self, intent: ResumeIntent) -> None:
                msg = "outbox down"
                raise RuntimeError(msg)

            @override
            async def delete(self, approval_id: str) -> bool:
                msg = "outbox down"
                raise RuntimeError(msg)

        _patch(monkeypatch, _BrokenRepo())
        await record_resume_intent(mock_of[AppState](), "ap-1")
        await clear_resume_intent(mock_of[AppState](), "ap-1")


class TestResumeIntentDrain:
    async def test_decided_approval_is_redispatched_and_cleared(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _FakeRepo("ap-1")
        dispatched = _patch(monkeypatch, repo, _FakeStore(_item()))

        assert await ResumeIntentDrain(mock_of[AppState]()).drain() == 1
        assert repo.rows == {}
        assert dispatched == [
            {
                "approval_id": "ap-1",
                "approved": True,
                "decided_by": "operator-1",
                "decision_reason": "looks fine",
                "task_id": "task-9",
            }
        ]

    async def test_rejection_is_redispatched_as_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The outcome is read off the approval, so a rejected decision
        # cannot be re-dispatched as an approval.
        repo = _FakeRepo("ap-1")
        dispatched = _patch(
            monkeypatch, repo, _FakeStore(_item(ApprovalStatus.REJECTED))
        )

        await ResumeIntentDrain(mock_of[AppState]()).drain()
        assert dispatched[0]["approved"] is False

    async def test_still_pending_marker_is_discarded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The crash landed before the decision write, so the approval is
        # still decidable by a human; re-dispatching would invent consent.
        repo = _FakeRepo("ap-1")
        dispatched = _patch(
            monkeypatch, repo, _FakeStore(_item(ApprovalStatus.PENDING))
        )

        assert await ResumeIntentDrain(mock_of[AppState]()).drain() == 0
        assert repo.rows == {}
        assert dispatched == []

    async def test_unknown_approval_marker_is_discarded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _FakeRepo("ap-1")
        dispatched = _patch(monkeypatch, repo, _FakeStore(None))

        assert await ResumeIntentDrain(mock_of[AppState]()).drain() == 0
        assert repo.rows == {}
        assert dispatched == []

    async def test_marker_recorded_after_the_decision_is_discarded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Written by a caller that went on to lose ``save_if_pending`` (a
        # duplicate event, or a stale POST against an approval decided long
        # ago). The resume behind that decision already ran, so
        # re-dispatching would run it a second time.
        later = datetime(2026, 7, 24, 13, 0, tzinfo=UTC)
        repo = _FakeRepo("ap-1", recorded_at=later)
        dispatched = _patch(monkeypatch, repo, _FakeStore(_item()))

        assert await ResumeIntentDrain(mock_of[AppState]()).drain() == 0
        assert repo.rows == {}
        assert dispatched == []

    async def test_marker_recorded_before_the_decision_is_redispatched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        earlier = datetime(2026, 7, 24, 11, 0, tzinfo=UTC)
        repo = _FakeRepo("ap-1", recorded_at=earlier)
        dispatched = _patch(monkeypatch, repo, _FakeStore(_item()))

        assert await ResumeIntentDrain(mock_of[AppState]()).drain() == 1
        assert repo.rows == {}
        assert len(dispatched) == 1

    async def test_failed_redispatch_keeps_the_marker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The parked task is still unresumed; dropping the marker here
        # would hide that forever instead of retrying next startup.
        repo = _FakeRepo("ap-1")
        _patch(monkeypatch, repo, _FakeStore(_item()))

        async def _boom(*_args: object, **_kwargs: object) -> None:
            msg = "resume routing down"
            raise RuntimeError(msg)

        monkeypatch.setattr(outbox_mod, "signal_resume_intent", _boom)

        assert await ResumeIntentDrain(mock_of[AppState]()).drain() == 0
        assert "ap-1" in repo.rows

    async def test_one_failure_does_not_abort_the_rest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _FakeRepo("ap-1", "ap-2")
        seen: list[str] = []
        monkeypatch.setattr(outbox_mod, "resume_intents_of", lambda _s: repo)
        monkeypatch.setattr(
            outbox_mod, "approval_store_of", lambda _s: _FakeStore(_item())
        )

        async def _first_fails(
            _app_state: object, approval_id: str, **_kwargs: object
        ) -> None:
            seen.append(approval_id)
            if approval_id == "ap-1":
                msg = "transient"
                raise RuntimeError(msg)

        monkeypatch.setattr(outbox_mod, "signal_resume_intent", _first_fails)

        assert await ResumeIntentDrain(mock_of[AppState]()).drain() == 1
        assert seen == ["ap-1", "ap-2"]
        assert set(repo.rows) == {"ap-1"}

    async def test_empty_outbox_is_a_no_op(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dispatched = _patch(monkeypatch, _FakeRepo(), _FakeStore(_item()))
        assert await ResumeIntentDrain(mock_of[AppState]()).drain() == 0
        assert dispatched == []

    async def test_no_backend_drains_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch(monkeypatch, None)
        assert await ResumeIntentDrain(mock_of[AppState]()).drain() == 0
