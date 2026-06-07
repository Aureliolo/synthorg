"""Conformance tests for ``EscalationQueueRepository``.

The escalation queue is accessed via ``backend.build_escalations()`` (a
method rather than a property because Postgres accepts an optional
NOTIFY channel). The conformance layer exercises the store contract
shared by both backends; the LISTEN/NOTIFY subscription path is
covered separately in ``test_escalation_notify.py``.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.communication.conflict_resolution.escalation.models import (
    Escalation,
    EscalationStatus,
    RejectDecision,
    WinnerDecision,
)
from synthorg.communication.conflict_resolution.models import (
    Conflict,
    ConflictPosition,
)
from synthorg.communication.enums import ConflictType
from synthorg.core.types import NotBlankStr
from synthorg.hr.seniority import SeniorityLevel
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 3, 15, 10, 0, 0, tzinfo=UTC)


def _conflict(conflict_id: str = "conflict-001") -> Conflict:
    return Conflict(
        id=conflict_id,
        type=ConflictType.ARCHITECTURE,
        subject="storage choice",
        positions=(
            ConflictPosition(
                agent_id="agent-a",
                agent_department="engineering",
                agent_level=SeniorityLevel.SENIOR,
                position="PostgreSQL",
                reasoning="consistency",
                timestamp=_NOW,
            ),
            ConflictPosition(
                agent_id="agent-b",
                agent_department="engineering",
                agent_level=SeniorityLevel.SENIOR,
                position="SQLite",
                reasoning="simplicity",
                timestamp=_NOW,
            ),
        ),
        detected_at=_NOW,
    )


def _escalation(
    *,
    escalation_id: str = "esc-001",
    conflict_id: str | None = None,
) -> Escalation:
    cid = conflict_id or f"conflict-for-{escalation_id}"
    return Escalation(
        id=escalation_id,
        conflict=_conflict(cid),
        created_at=_NOW,
        expires_at=_NOW + timedelta(hours=24),
    )


class TestEscalationQueueRepository:
    async def test_create_and_get(self, backend: PersistenceBackend) -> None:
        repo = backend.build_escalations()
        await repo.create(_escalation())

        fetched = await repo.get(NotBlankStr("esc-001"))
        assert fetched is not None
        assert fetched.id == "esc-001"
        assert fetched.status is EscalationStatus.PENDING

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        repo = backend.build_escalations()
        assert await repo.get(NotBlankStr("ghost")) is None

    async def test_list_items_filters_by_status(
        self, backend: PersistenceBackend
    ) -> None:
        repo = backend.build_escalations()
        await repo.create(_escalation(escalation_id="a"))
        await repo.create(_escalation(escalation_id="b"))

        pending, total = await repo.list_items(status=EscalationStatus.PENDING)
        assert total == 2
        assert {e.id for e in pending} == {"a", "b"}

        decided, total_decided = await repo.list_items(
            status=EscalationStatus.DECIDED,
        )
        assert total_decided == 0
        assert decided == ()

    async def test_apply_winner_decision(self, backend: PersistenceBackend) -> None:
        repo = backend.build_escalations()
        await repo.create(_escalation(escalation_id="win"))

        updated = await repo.apply_decision(
            NotBlankStr("win"),
            decision=WinnerDecision(
                winning_agent_id="agent-a",
                reasoning="strong consistency",
            ),
            decided_by=NotBlankStr("human:op-1"),
        )
        assert updated.status is EscalationStatus.DECIDED
        assert updated.decided_by == "human:op-1"

    async def test_apply_reject_decision(self, backend: PersistenceBackend) -> None:
        repo = backend.build_escalations()
        await repo.create(_escalation(escalation_id="rej"))

        updated = await repo.apply_decision(
            NotBlankStr("rej"),
            decision=RejectDecision(reasoning="both off-strategy"),
            decided_by=NotBlankStr("human:op-2"),
        )
        assert updated.status is EscalationStatus.DECIDED

    async def test_apply_decision_missing_raises(
        self, backend: PersistenceBackend
    ) -> None:
        repo = backend.build_escalations()
        with pytest.raises(KeyError):
            await repo.apply_decision(
                NotBlankStr("ghost"),
                decision=WinnerDecision(
                    winning_agent_id="agent-a",
                    reasoning="agent-a has stronger reasoning",
                ),
                decided_by=NotBlankStr("human:op-1"),
            )

    async def test_cancel(self, backend: PersistenceBackend) -> None:
        repo = backend.build_escalations()
        await repo.create(_escalation(escalation_id="cxl"))

        cancelled = await repo.cancel(
            NotBlankStr("cxl"),
            cancelled_by=NotBlankStr("human:op-3"),
        )
        assert cancelled.status is EscalationStatus.CANCELLED

    async def test_mark_expired(self, backend: PersistenceBackend) -> None:
        repo = backend.build_escalations()
        await repo.create(
            Escalation(
                id="exp",
                conflict=_conflict("conflict-for-exp"),
                created_at=_NOW,
                expires_at=_NOW + timedelta(minutes=1),
            ),
        )

        future_iso = (_NOW + timedelta(hours=2)).isoformat()
        expired = await repo.mark_expired(future_iso)
        assert "exp" in expired

        fetched = await repo.get(NotBlankStr("exp"))
        assert fetched is not None
        assert fetched.status is EscalationStatus.EXPIRED
