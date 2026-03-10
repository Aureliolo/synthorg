"""Tests for the ParkedContext model."""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from ai_company.security.timeout.parked_context import ParkedContext

pytestmark = pytest.mark.timeout(30)


def _make_parked_context(**overrides: Any) -> ParkedContext:
    """Create a valid ParkedContext with sensible defaults."""
    defaults: dict[str, Any] = {
        "execution_id": "exec-1",
        "agent_id": "agent-1",
        "task_id": "task-1",
        "approval_id": "approval-1",
        "parked_at": datetime.now(UTC),
        "context_json": '{"key": "value"}',
    }
    defaults.update(overrides)
    return ParkedContext(**defaults)


@pytest.mark.unit
class TestParkedContext:
    """Tests for ParkedContext model validation and immutability."""

    def test_creation(self) -> None:
        """Valid creation with all fields."""
        now = datetime.now(UTC)
        parked = ParkedContext(
            id="custom-id",
            execution_id="exec-1",
            agent_id="agent-1",
            task_id="task-1",
            approval_id="approval-1",
            parked_at=now,
            context_json='{"data": true}',
            metadata={"tool": "git"},
        )
        assert parked.id == "custom-id"
        assert parked.execution_id == "exec-1"
        assert parked.agent_id == "agent-1"
        assert parked.task_id == "task-1"
        assert parked.approval_id == "approval-1"
        assert parked.parked_at == now
        assert parked.context_json == '{"data": true}'
        assert parked.metadata == {"tool": "git"}

    def test_frozen(self) -> None:
        """Cannot modify fields on a frozen model."""
        parked = _make_parked_context()
        with pytest.raises(ValidationError):
            parked.agent_id = "other"  # type: ignore[misc]

    def test_default_id_generated(self) -> None:
        """id gets a UUID default when not provided."""
        parked = _make_parked_context()
        assert parked.id  # non-empty
        assert len(parked.id) > 0

    def test_unique_ids(self) -> None:
        """Two instances get different default IDs."""
        a = _make_parked_context()
        b = _make_parked_context()
        assert a.id != b.id

    def test_empty_metadata_default(self) -> None:
        """metadata defaults to empty dict."""
        parked = _make_parked_context()
        assert parked.metadata == {}

    def test_blank_agent_id_rejected(self) -> None:
        """Blank agent_id raises ValidationError."""
        with pytest.raises(ValidationError):
            _make_parked_context(agent_id="")

        with pytest.raises(ValidationError):
            _make_parked_context(agent_id="   ")
