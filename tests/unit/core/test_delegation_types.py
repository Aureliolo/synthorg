"""Tests for the delegation-request model engine classification routes through."""

import pytest
from pydantic import ValidationError

from synthorg.core.delegation_types import DelegationRequest
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskType
from tests._shared import coerce_id


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
    defaults["id"] = coerce_id(defaults["id"])
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
