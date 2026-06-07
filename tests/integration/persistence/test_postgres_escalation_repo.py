"""Integration tests for :class:`PostgresEscalationRepository`.

Requires a real Postgres via ``testcontainers``; runs under the
``integration`` marker.  Uses the shared ``postgres_backend`` fixture
from :mod:`tests.integration.persistence.conftest` so migrations are
applied once per session.
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
from synthorg.hr.seniority import SeniorityLevel
from synthorg.persistence.postgres.backend import PostgresPersistenceBackend
from synthorg.persistence.postgres.escalation_repo import (
    PostgresEscalationRepository,
)

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def _make_conflict(conflict_id: str = "conflict-pg-0001") -> Conflict:
    """Two-agent architectural conflict fixture."""
    return Conflict(
        id=conflict_id,
        type=ConflictType.ARCHITECTURE,
        subject="Pick a storage engine",
        positions=(
            ConflictPosition(
                agent_id="agent-a",
                agent_department="engineering",
                agent_level=SeniorityLevel.SENIOR,
                position="PostgreSQL",
                reasoning="Strong consistency",
                timestamp=datetime.now(UTC),
            ),
            ConflictPosition(
                agent_id="agent-b",
                agent_department="engineering",
                agent_level=SeniorityLevel.SENIOR,
                position="SQLite",
                reasoning="Simpler ops",
                timestamp=datetime.now(UTC),
            ),
        ),
        detected_at=datetime.now(UTC),
    )


def _make_escalation(
    *,
    escalation_id: str,
    expires_at: datetime | None = None,
    conflict_id: str | None = None,
) -> Escalation:
    """Build a pending escalation.

    ``conflict_id`` defaults to a value derived from ``escalation_id`` so
    tests that create several escalations in the same Postgres session
    do not trip the schema's partial-unique "one PENDING per conflict"
    index.
    """
    resolved_conflict_id = conflict_id or f"conflict-for-{escalation_id}"
    return Escalation(
        id=escalation_id,
        conflict=_make_conflict(resolved_conflict_id),
        created_at=datetime.now(UTC),
        expires_at=expires_at,
    )


@pytest.fixture
def repo(postgres_backend: PostgresPersistenceBackend) -> PostgresEscalationRepository:
    """Yield a repository bound to the session-scoped Postgres backend."""
    assert postgres_backend._pool is not None
    return PostgresEscalationRepository(postgres_backend._pool)


async def test_create_and_get(repo: PostgresEscalationRepository) -> None:
    row = _make_escalation(escalation_id="escalation-pg-get-01")
    await repo.create(row)
    fetched = await repo.get("escalation-pg-get-01")
    assert fetched is not None
    assert fetched.id == row.id
    assert fetched.status == EscalationStatus.PENDING
    assert fetched.conflict.id == row.conflict.id


async def test_duplicate_id_raises(repo: PostgresEscalationRepository) -> None:
    from synthorg.core.persistence_errors import (
        ConstraintViolationError,
    )

    row = _make_escalation(escalation_id="escalation-pg-dup-01")
    await repo.create(row)
    with pytest.raises(ConstraintViolationError):
        await repo.create(row)


async def test_list_items_filters_by_status(
    repo: PostgresEscalationRepository,
) -> None:
    # Seed two PENDING rows + one CANCELLED row; the CANCELLED row
    # must be excluded from a ``status=PENDING`` filter so the
    # dashboard queue never surfaces terminal rows by accident.
    await repo.create(_make_escalation(escalation_id="escalation-pg-list-a"))
    await repo.create(_make_escalation(escalation_id="escalation-pg-list-b"))
    await repo.create(_make_escalation(escalation_id="escalation-pg-list-c"))
    await repo.cancel("escalation-pg-list-c", cancelled_by="human:op-list-test")
    pending, total_pending = await repo.list_items(
        status=EscalationStatus.PENDING,
    )
    assert total_pending >= 2
    ids = {e.id for e in pending}
    assert {"escalation-pg-list-a", "escalation-pg-list-b"}.issubset(ids)
    assert "escalation-pg-list-c" not in ids
    # The CANCELLED row is still visible under its own status filter.
    cancelled, _ = await repo.list_items(status=EscalationStatus.CANCELLED)
    assert "escalation-pg-list-c" in {e.id for e in cancelled}


async def test_apply_winner_decision_round_trips(
    repo: PostgresEscalationRepository,
) -> None:
    await repo.create(_make_escalation(escalation_id="escalation-pg-win"))
    decision = WinnerDecision(
        winning_agent_id="agent-a",
        reasoning="Stronger consistency wins",
    )
    updated = await repo.apply_decision(
        "escalation-pg-win",
        decision=decision,
        decided_by="human:op-1",
    )
    assert updated.status == EscalationStatus.DECIDED
    assert updated.decision == decision
    reloaded = await repo.get("escalation-pg-win")
    assert reloaded is not None
    assert reloaded.status == EscalationStatus.DECIDED
    assert reloaded.decision == decision


async def test_apply_reject_decision_round_trips(
    repo: PostgresEscalationRepository,
) -> None:
    await repo.create(_make_escalation(escalation_id="escalation-pg-rej"))
    decision = RejectDecision(reasoning="Both positions off-strategy")
    updated = await repo.apply_decision(
        "escalation-pg-rej",
        decision=decision,
        decided_by="human:op-2",
    )
    assert updated.status == EscalationStatus.DECIDED
    assert updated.decision == decision


async def test_apply_decision_missing_raises_keyerror(
    repo: PostgresEscalationRepository,
) -> None:
    with pytest.raises(KeyError):
        await repo.apply_decision(
            "escalation-pg-missing",
            decision=WinnerDecision(winning_agent_id="agent-a", reasoning="x"),
            decided_by="human:op-x",
        )


async def test_apply_decision_on_decided_raises_valueerror(
    repo: PostgresEscalationRepository,
) -> None:
    await repo.create(_make_escalation(escalation_id="escalation-pg-dbl"))
    decision = WinnerDecision(winning_agent_id="agent-a", reasoning="ok")
    await repo.apply_decision(
        "escalation-pg-dbl",
        decision=decision,
        decided_by="human:op-1",
    )
    with pytest.raises(ValueError, match="cannot transition"):
        await repo.apply_decision(
            "escalation-pg-dbl",
            decision=decision,
            decided_by="human:op-2",
        )


async def test_cancel_transitions_to_cancelled(
    repo: PostgresEscalationRepository,
) -> None:
    await repo.create(_make_escalation(escalation_id="escalation-pg-canc"))
    updated = await repo.cancel(
        "escalation-pg-canc",
        cancelled_by="human:op-3",
    )
    assert updated.status == EscalationStatus.CANCELLED
    assert updated.decided_by == "human:op-3"


async def test_mark_expired_transitions_stale_rows(
    repo: PostgresEscalationRepository,
) -> None:
    past = datetime.now(UTC) - timedelta(seconds=30)
    future = datetime.now(UTC) + timedelta(seconds=3600)
    await repo.create(
        _make_escalation(escalation_id="escalation-pg-old", expires_at=past),
    )
    await repo.create(
        _make_escalation(escalation_id="escalation-pg-fresh", expires_at=future),
    )
    expired = await repo.mark_expired(datetime.now(UTC).isoformat())
    assert "escalation-pg-old" in expired
    old = await repo.get("escalation-pg-old")
    assert old is not None
    assert old.status == EscalationStatus.EXPIRED
    fresh = await repo.get("escalation-pg-fresh")
    assert fresh is not None
    assert fresh.status == EscalationStatus.PENDING
