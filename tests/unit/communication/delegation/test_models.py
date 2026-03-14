"""Tests for delegation models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.communication.delegation.models import (
    DelegationRecord,
    DelegationRequest,
    DelegationResult,
)
from synthorg.core.enums import TaskType
from synthorg.core.task import Task

pytestmark = pytest.mark.timeout(30)


def _make_task(**overrides: object) -> Task:
    defaults: dict[str, object] = {
        "id": "task-1",
        "title": "Test task",
        "description": "A test task",
        "type": TaskType.DEVELOPMENT,
        "project": "proj-1",
        "created_by": "pm-1",
    }
    defaults.update(overrides)
    return Task(**defaults)  # type: ignore[arg-type]


@pytest.mark.unit
class TestDelegationRequest:
    def test_minimal_valid(self) -> None:
        task = _make_task()
        req = DelegationRequest(
            delegator_id="cto",
            delegatee_id="dev",
            task=task,
        )
        assert req.delegator_id == "cto"
        assert req.delegatee_id == "dev"
        assert req.task is task
        assert req.refinement == ""
        assert req.constraints == ()

    def test_with_refinement_and_constraints(self) -> None:
        task = _make_task()
        req = DelegationRequest(
            delegator_id="cto",
            delegatee_id="dev",
            task=task,
            refinement="Focus on performance",
            constraints=("no-external-deps", "max-2-files"),
        )
        assert req.refinement == "Focus on performance"
        assert len(req.constraints) == 2

    def test_frozen(self) -> None:
        task = _make_task()
        req = DelegationRequest(
            delegator_id="cto",
            delegatee_id="dev",
            task=task,
        )
        with pytest.raises(ValidationError):
            req.delegator_id = "new"  # type: ignore[misc]

    def test_blank_delegator_rejected(self) -> None:
        task = _make_task()
        with pytest.raises(ValidationError):
            DelegationRequest(
                delegator_id="  ",
                delegatee_id="dev",
                task=task,
            )

    def test_blank_delegatee_rejected(self) -> None:
        task = _make_task()
        with pytest.raises(ValidationError):
            DelegationRequest(
                delegator_id="cto",
                delegatee_id="",
                task=task,
            )

    def test_self_delegation_rejected(self) -> None:
        task = _make_task()
        with pytest.raises(ValidationError, match="must differ"):
            DelegationRequest(
                delegator_id="cto",
                delegatee_id="cto",
                task=task,
            )


@pytest.mark.unit
class TestDelegationResult:
    def test_success_result(self) -> None:
        task = _make_task()
        result = DelegationResult(
            success=True,
            delegated_task=task,
        )
        assert result.success is True
        assert result.delegated_task is task
        assert result.rejection_reason is None
        assert result.blocked_by is None

    def test_failure_result(self) -> None:
        result = DelegationResult(
            success=False,
            rejection_reason="Authority denied",
            blocked_by="hierarchy",
        )
        assert result.success is False
        assert result.delegated_task is None
        assert result.rejection_reason == "Authority denied"
        assert result.blocked_by == "hierarchy"

    def test_frozen(self) -> None:
        task = _make_task()
        result = DelegationResult(success=True, delegated_task=task)
        with pytest.raises(ValidationError):
            result.success = False  # type: ignore[misc]

    def test_success_without_task_rejected(self) -> None:
        with pytest.raises(ValidationError, match="delegated_task is required"):
            DelegationResult(success=True)

    def test_success_with_rejection_reason_rejected(self) -> None:
        task = _make_task()
        with pytest.raises(ValidationError, match="rejection_reason must be None"):
            DelegationResult(
                success=True,
                delegated_task=task,
                rejection_reason="oops",
            )

    def test_success_with_blocked_by_rejected(self) -> None:
        task = _make_task()
        with pytest.raises(ValidationError, match="blocked_by must be None"):
            DelegationResult(
                success=True,
                delegated_task=task,
                blocked_by="guard",
            )

    def test_failure_without_reason_rejected(self) -> None:
        with pytest.raises(ValidationError, match="rejection_reason is required"):
            DelegationResult(success=False)

    def test_failure_with_task_rejected(self) -> None:
        task = _make_task()
        with pytest.raises(ValidationError, match="delegated_task must be None"):
            DelegationResult(
                success=False,
                delegated_task=task,
                rejection_reason="blocked",
            )


@pytest.mark.unit
class TestDelegationRecord:
    def test_valid_record(self) -> None:
        now = datetime.now(UTC)
        record = DelegationRecord(
            delegation_id="del-1",
            delegator_id="cto",
            delegatee_id="dev",
            original_task_id="task-1",
            delegated_task_id="task-2",
            timestamp=now,
        )
        assert record.delegation_id == "del-1"
        assert record.delegator_id == "cto"
        assert record.delegatee_id == "dev"
        assert record.original_task_id == "task-1"
        assert record.delegated_task_id == "task-2"
        assert record.timestamp == now
        assert record.refinement == ""

    def test_with_refinement(self) -> None:
        now = datetime.now(UTC)
        record = DelegationRecord(
            delegation_id="del-2",
            delegator_id="cto",
            delegatee_id="dev",
            original_task_id="task-1",
            delegated_task_id="task-3",
            timestamp=now,
            refinement="Focus on API layer",
        )
        assert record.refinement == "Focus on API layer"

    def test_frozen(self) -> None:
        now = datetime.now(UTC)
        record = DelegationRecord(
            delegation_id="del-1",
            delegator_id="cto",
            delegatee_id="dev",
            original_task_id="task-1",
            delegated_task_id="task-2",
            timestamp=now,
        )
        with pytest.raises(ValidationError):
            record.delegator_id = "new"  # type: ignore[misc]

    def test_blank_ids_rejected(self) -> None:
        now = datetime.now(UTC)
        with pytest.raises(ValidationError):
            DelegationRecord(
                delegation_id="  ",
                delegator_id="cto",
                delegatee_id="dev",
                original_task_id="task-1",
                delegated_task_id="task-2",
                timestamp=now,
            )

    def test_json_roundtrip(self) -> None:
        now = datetime.now(UTC)
        record = DelegationRecord(
            delegation_id="del-1",
            delegator_id="cto",
            delegatee_id="dev",
            original_task_id="task-1",
            delegated_task_id="task-2",
            timestamp=now,
        )
        json_str = record.model_dump_json()
        restored = DelegationRecord.model_validate_json(json_str)
        assert restored.delegation_id == record.delegation_id
        assert restored.timestamp == record.timestamp
