"""Postgres-specific integration tests for ``PostgresApprovalRepository``.

The protocol-surface coverage (save/get/list/delete/upsert) lives in
``tests/conformance/persistence/test_approval_repository.py``, exercised
against both backends through the parametrized ``backend`` fixture.

This file holds the cases that are inherently Postgres-only because
they bypass the protocol to drive raw psycopg primitives:

* JSONB rows accepted at the wire layer but rejected by Pydantic
* Primary-key duplication via raw ``INSERT`` (skipping ``ON CONFLICT``)
* DB-side ``CHECK`` constraints surfacing as ``ConstraintViolationError``
* Backend capability methods (``build_ontology_versioning``)
"""

from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.types.json import Jsonb

from synthorg.core.approval import ApprovalItem
from synthorg.core.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.persistence.postgres.approval_repo import (
    PostgresApprovalRepository,
)
from synthorg.persistence.postgres.backend import PostgresPersistenceBackend
from tests._shared import as_uuid

pytestmark = [pytest.mark.integration, pytest.mark.slow]


_FIXED_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _make_item(  # noqa: PLR0913 -- test factory with explicit knobs
    *,
    approval_id: str = "approval-pg-0001",
    status: ApprovalStatus = ApprovalStatus.PENDING,
    risk_level: ApprovalRiskLevel = ApprovalRiskLevel.HIGH,
    action_type: str = "deploy:production",
    task_id: str | None = None,
    metadata: dict[str, str] | None = None,
) -> ApprovalItem:
    """Build an ApprovalItem with sensible defaults."""
    now = _FIXED_NOW
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    if status in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
        decided_at = now
        decided_by = "operator-a"
        if status == ApprovalStatus.REJECTED:
            decision_reason = "Not authorised"
    return ApprovalItem(
        id=as_uuid(approval_id),
        action_type=action_type,
        title="Approve prod deploy",
        description="Rolls service v2 to prod.",
        requested_by="agent-eng-001",
        risk_level=risk_level,
        status=status,
        created_at=now,
        expires_at=now + timedelta(days=7),
        task_id=task_id,
        metadata=metadata or {},
        decided_at=decided_at,
        decided_by=decided_by,
        decision_reason=decision_reason,
    )


@pytest.fixture
def repo(
    postgres_backend: PostgresPersistenceBackend,
) -> PostgresApprovalRepository:
    """Approval repository wired to the shared postgres backend pool."""
    assert postgres_backend._pool is not None
    return PostgresApprovalRepository(postgres_backend._pool)


async def test_pydantic_invalid_row_raises_query_error(
    repo: PostgresApprovalRepository,
    postgres_backend: PostgresPersistenceBackend,
) -> None:
    """A DB row that the DB accepts but Pydantic rejects surfaces as
    :class:`QueryError` (wrapping the underlying ``ValidationError``),
    exercising the ``_row_to_item`` error path with ``ValidationError``
    in the caught exception tuple.

    The schema's CHECK constraints block invalid enum values, so we
    corrupt ``metadata`` to carry a non-string value -- permitted by
    the ``JSONB`` column type but rejected by ``ApprovalItem``'s
    ``dict[str, str]`` metadata field.
    """
    assert postgres_backend._pool is not None
    async with postgres_backend._pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "INSERT INTO approvals "
            "(id, action_type, title, description, requested_by, "
            " risk_level, status, created_at, expires_at, "
            " decided_at, decided_by, decision_reason, task_id, "
            " evidence_package, metadata) VALUES "
            "(%s, %s, %s, %s, %s, %s, %s, %s, %s, "
            " %s, %s, %s, %s, %s, %s)",
            (
                str(as_uuid("approval-invalid-001")),
                "deploy:production",
                "Invalid metadata",
                "metadata dict carries a non-string value",
                "agent-eng-001",
                "high",
                "pending",
                _FIXED_NOW,
                None,
                None,
                None,
                None,
                None,
                None,
                # Non-string value in metadata -- ApprovalItem
                # requires dict[str, str] so Pydantic rejects.
                Jsonb({"bad_field": 42}),
            ),
        )
        await conn.commit()

    with pytest.raises(QueryError):
        await repo.get(str(as_uuid("approval-invalid-001")))


async def test_save_duplicate_primary_key_raises_constraint(
    repo: PostgresApprovalRepository,
    postgres_backend: PostgresPersistenceBackend,
) -> None:
    """A manually INSERTed duplicate id surfaces as
    :class:`ConstraintViolationError`; the upsert path in ``save()``
    handles legitimate updates without raising, so we force the
    violation by directly inserting the second row via a raw
    ``INSERT`` that bypasses ``ON CONFLICT``.
    """
    assert postgres_backend._pool is not None
    item = _make_item(approval_id="approval-dup-001")
    await repo.save(item)

    async with postgres_backend._pool.connection() as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.UniqueViolation):
            await cur.execute(
                "INSERT INTO approvals "
                "(id, action_type, title, description, requested_by, "
                " risk_level, status, created_at, expires_at, "
                " decided_at, decided_by, decision_reason, task_id, "
                " evidence_package, metadata) VALUES "
                "(%s, %s, %s, %s, %s, %s, %s, %s, %s, "
                " %s, %s, %s, %s, %s, %s)",
                (
                    str(item.id),
                    item.action_type,
                    item.title,
                    item.description,
                    item.requested_by,
                    item.risk_level.value,
                    item.status.value,
                    item.created_at,
                    item.expires_at,
                    None,
                    None,
                    None,
                    None,
                    None,
                    Jsonb({}),
                ),
            )

    # The legitimate upsert retry (same id, different payload) goes
    # through ON CONFLICT and does NOT raise -- this documents the
    # semantics for callers that were previously unsure.
    updated = item.model_copy(update={"description": "Updated"})
    await repo.save(updated)
    fetched = await repo.get(str(item.id))
    assert fetched is not None
    assert fetched.description == "Updated"


async def test_constraint_violation_surfaces_from_save(
    repo: PostgresApprovalRepository,
) -> None:
    """A REJECTED item without a decision_reason fails at the DB CHECK
    constraint and is surfaced as
    :class:`ConstraintViolationError`.  We build an invalid row by
    bypassing Pydantic validation via ``model_construct``.
    """
    # The Pydantic validator refuses to build this state directly, so
    # use model_construct to bypass field validation and force the DB
    # CHECK to surface the violation.
    now = _FIXED_NOW
    bad_item = ApprovalItem.model_construct(
        id=as_uuid("approval-ck-001"),
        action_type="deploy:production",
        title="Rejected w/o reason",
        description="Should not be storable.",
        requested_by="agent-eng-001",
        risk_level=ApprovalRiskLevel.HIGH,
        status=ApprovalStatus.REJECTED,
        created_at=now,
        expires_at=now + timedelta(days=7),
        decided_at=now,
        decided_by="operator-a",
        decision_reason=None,
        task_id=None,
        evidence_package=None,
        metadata={},
    )
    with pytest.raises(ConstraintViolationError):
        await repo.save(bad_item)


async def test_build_ontology_versioning_returns_service(
    postgres_backend: PostgresPersistenceBackend,
) -> None:
    """Postgres capability method yields a wired VersioningService.

    Mirrors the SQLite-side unit test
    (``tests/unit/persistence/test_backend_capability_methods.py``)
    so the capability-method pattern is exercised on both backends.
    """
    from synthorg.versioning.service import VersioningService

    service = postgres_backend.build_ontology_versioning()
    assert isinstance(service, VersioningService)
    for method_name in ("snapshot_if_changed", "force_snapshot", "get_latest"):
        assert callable(getattr(service, method_name))
