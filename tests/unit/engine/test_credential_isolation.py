"""Tests for credential isolation boundary enforcement."""

import pytest
import structlog.testing

from synthorg.core.task import Task
from synthorg.core.task_enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.engine._validation import validate_task_metadata
from synthorg.engine.errors import ExecutionStateError
from synthorg.observability.events.execution import (
    EXECUTION_CREDENTIAL_ISOLATION_VIOLATION,
)
from tests._shared import as_uuid


def _make_task(metadata: dict[str, object]) -> Task:
    """Create a minimal task with the given metadata."""
    return Task(
        id=as_uuid("task-cred-001"),
        title="Test task",
        description="Task for credential isolation testing.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-001",
        created_by="tester",
        assigned_to="agent-001",
        status=TaskStatus.ASSIGNED,
        estimated_complexity=Complexity.SIMPLE,
        metadata=metadata,
    )


@pytest.mark.unit
class TestCredentialIsolationValidator:
    """validate_task_metadata rejects credential-like keys."""

    @pytest.mark.parametrize(
        ("key", "match"),
        [
            pytest.param("api_token", "api_token", id="token"),
            pytest.param("my_secret", "my_secret", id="secret"),
            pytest.param("API_KEY", "API_KEY", id="api_key"),
            pytest.param("api-key", "api-key", id="api_key_hyphen"),
            pytest.param("db_password", "db_password", id="password"),
            pytest.param("bearer_token", "bearer", id="bearer"),
            pytest.param("SECRET_VALUE", "SECRET_VALUE", id="case_insensitive"),
        ],
    )
    def test_rejects_credential_key(self, key: str, match: str) -> None:
        task = _make_task({key: "leaked"})
        with pytest.raises(ExecutionStateError, match=match):
            validate_task_metadata(task, agent_id="a1", task_id="t1")

    def test_accepts_safe_metadata(self) -> None:
        task = _make_task({"priority": "high", "team": "backend"})
        validate_task_metadata(task, agent_id="a1", task_id="t1")

    def test_accepts_empty_metadata(self) -> None:
        task = _make_task({})
        validate_task_metadata(task, agent_id="a1", task_id="t1")

    def test_rejects_multiple_violations(self) -> None:
        task = _make_task({"api_token": "x", "password": "y"})
        with pytest.raises(ExecutionStateError, match="api_token") as exc_info:
            validate_task_metadata(task, agent_id="a1", task_id="t1")
        assert "password" in str(exc_info.value)

    def test_rejects_nested_credential_key(self) -> None:
        task = _make_task({"config": {"db_password": "leaked"}})
        with pytest.raises(ExecutionStateError, match="db_password"):
            validate_task_metadata(task, agent_id="a1", task_id="t1")

    def test_rejects_deeply_nested_credential_key(self) -> None:
        task = _make_task({"a": {"b": [{"api_key": "leaked"}]}})
        with pytest.raises(ExecutionStateError, match="api_key"):
            validate_task_metadata(task, agent_id="a1", task_id="t1")

    def test_accepts_safe_nested_metadata(self) -> None:
        task = _make_task({"config": {"retries": 3, "timeout": 30}})
        validate_task_metadata(task, agent_id="a1", task_id="t1")

    def test_logs_violation_event(self) -> None:
        task = _make_task({"api_token": "leaked"})
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(ExecutionStateError),
        ):
            validate_task_metadata(task, agent_id="a1", task_id="t1")

        violation_logs = [
            log
            for log in logs
            if log.get("event") == EXECUTION_CREDENTIAL_ISOLATION_VIOLATION
        ]
        assert len(violation_logs) == 1
        assert violation_logs[0]["agent_id"] == "a1"
        assert violation_logs[0]["task_id"] == "t1"
