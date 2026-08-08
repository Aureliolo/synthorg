"""Conformance tests for ``LifecycleTransitionRepository`` (SQLite + Postgres).

The ledger is what makes "only ``evaluate.py`` writes COMPLETED" provable
from persisted state rather than a container log, so both backends must
round-trip the same rows, filter to one entity, and hold the retention
contract the rest of the append-only stores hold.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.lifecycle_transition import (
    LifecycleEntityKind,
    LifecycleTransition,
)
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.lifecycle_transition_protocol import (
    LifecycleTransitionFilterSpec,
)
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _transition(
    *,
    entity_kind: LifecycleEntityKind = LifecycleEntityKind.PLAN,
    entity_id: str = "plan-001",
    from_status: str | None = "approved",
    to_status: str = "executing",
    requested_by: str | None = "operator-1",
    reason: str | None = "approved the plan",
    entity_version: int = 2,
    occurred_at: datetime | None = None,
) -> LifecycleTransition:
    return LifecycleTransition(
        entity_kind=entity_kind,
        entity_id=NotBlankStr(entity_id),
        from_status=NotBlankStr(from_status) if from_status else None,
        to_status=NotBlankStr(to_status),
        requested_by=NotBlankStr(requested_by) if requested_by else None,
        reason=NotBlankStr(reason) if reason else None,
        entity_version=entity_version,
        occurred_at=occurred_at or datetime.now(UTC),
    )


class TestLifecycleTransitionRepository:
    async def test_append_and_query_round_trip(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.lifecycle_transitions.append(_transition())

        page = await backend.lifecycle_transitions.query(
            LifecycleTransitionFilterSpec(entity_id=NotBlankStr("plan-001")),
        )
        assert len(page) == 1
        row = page[0]
        assert row.entity_kind is LifecycleEntityKind.PLAN
        assert row.from_status == "approved"
        assert row.to_status == "executing"
        assert row.requested_by == "operator-1"
        assert row.reason == "approved the plan"
        assert row.entity_version == 2

    async def test_a_system_move_has_no_actor(
        self, backend: PersistenceBackend
    ) -> None:
        """``None`` means the system moved it, which is itself the answer."""
        await backend.lifecycle_transitions.append(
            _transition(requested_by=None, reason=None, from_status=None),
        )

        page = await backend.lifecycle_transitions.query(
            LifecycleTransitionFilterSpec(entity_id=NotBlankStr("plan-001")),
        )
        assert page[0].requested_by is None
        assert page[0].reason is None
        assert page[0].from_status is None

    async def test_query_narrows_by_kind_and_entity(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.lifecycle_transitions.append(_transition(entity_id="plan-a"))
        await backend.lifecycle_transitions.append(_transition(entity_id="plan-b"))
        await backend.lifecycle_transitions.append(
            _transition(
                entity_kind=LifecycleEntityKind.PROJECT,
                entity_id="plan-a",
                to_status="active",
            ),
        )

        page = await backend.lifecycle_transitions.query(
            LifecycleTransitionFilterSpec(
                entity_kind=LifecycleEntityKind.PLAN,
                entity_id=NotBlankStr("plan-a"),
            ),
        )
        assert len(page) == 1
        assert page[0].to_status == "executing"

    async def test_query_returns_newest_first(
        self, backend: PersistenceBackend
    ) -> None:
        now = datetime.now(UTC)
        await backend.lifecycle_transitions.append(
            _transition(to_status="approved", occurred_at=now - timedelta(hours=2)),
        )
        await backend.lifecycle_transitions.append(
            _transition(to_status="executing", occurred_at=now),
        )

        page = await backend.lifecycle_transitions.query(
            LifecycleTransitionFilterSpec(entity_id=NotBlankStr("plan-001")),
        )
        assert [r.to_status for r in page] == ["executing", "approved"]

    async def test_purge_before(self, backend: PersistenceBackend) -> None:
        old = datetime.now(UTC) - timedelta(days=2)
        await backend.lifecycle_transitions.append(
            _transition(entity_id="old", occurred_at=old),
        )
        await backend.lifecycle_transitions.append(_transition(entity_id="new"))

        removed = await backend.lifecycle_transitions.purge_before(
            datetime.now(UTC) - timedelta(days=1),
        )

        assert removed == 1
        page = await backend.lifecycle_transitions.query(
            LifecycleTransitionFilterSpec(),
        )
        assert [r.entity_id for r in page] == ["new"]

    async def test_purge_before_rejects_naive_threshold(
        self, backend: PersistenceBackend
    ) -> None:
        # A naive threshold is rejected rather than silently coerced to UTC,
        # which could delete the wrong retention window.
        with pytest.raises(QueryError):
            await backend.lifecycle_transitions.purge_before(
                datetime(2026, 1, 1),  # noqa: DTZ001 -- naive on purpose
            )
