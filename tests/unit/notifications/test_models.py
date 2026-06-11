"""Tests for notification domain models."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)
from tests._shared import as_uuid


@pytest.mark.unit
class TestNotificationCategory:
    def test_values(self) -> None:
        assert NotificationCategory.APPROVAL.value == "approval"
        assert NotificationCategory.BUDGET.value == "budget"
        assert NotificationCategory.SECURITY.value == "security"
        assert NotificationCategory.SYSTEM.value == "system"
        assert NotificationCategory.AGENT.value == "agent"
        assert NotificationCategory.HEALTH.value == "health"

    def test_stagnation_removed(self) -> None:
        """Regression guard: STAGNATION was unused and removed in favour of HEALTH."""
        with pytest.raises(AttributeError):
            _ = NotificationCategory.STAGNATION  # type: ignore[attr-defined]
        assert "stagnation" not in {c.value for c in NotificationCategory}


@pytest.mark.unit
class TestNotificationSeverity:
    def test_values(self) -> None:
        assert NotificationSeverity.INFO.value == "info"
        assert NotificationSeverity.WARNING.value == "warning"
        assert NotificationSeverity.ERROR.value == "error"
        assert NotificationSeverity.CRITICAL.value == "critical"


@pytest.mark.unit
class TestNotification:
    def test_construction_with_defaults(self) -> None:
        n = Notification(
            category=NotificationCategory.BUDGET,
            severity=NotificationSeverity.WARNING,
            title="Budget threshold crossed",
            source="budget.enforcer",
        )
        assert n.category == NotificationCategory.BUDGET
        assert n.severity == NotificationSeverity.WARNING
        assert n.title == "Budget threshold crossed"
        assert n.body == ""
        assert n.source == "budget.enforcer"
        assert isinstance(n.id, UUID)  # auto-generated
        assert n.timestamp.tzinfo is not None
        assert n.metadata == {}

    def test_construction_with_all_fields(self) -> None:
        ts = datetime(2026, 4, 6, 12, 0, tzinfo=UTC)
        n = Notification(
            id=as_uuid("custom-id"),
            category=NotificationCategory.APPROVAL,
            severity=NotificationSeverity.CRITICAL,
            title="Approval escalated",
            body="Approval timed out for agent X",
            source="security.timeout.scheduler",
            timestamp=ts,
            metadata={"approval_id": "abc-123"},
        )
        assert n.id == as_uuid("custom-id")
        assert n.body == "Approval timed out for agent X"
        assert n.timestamp == ts
        assert n.metadata == {"approval_id": "abc-123"}

    def test_frozen(self) -> None:
        n = Notification(
            category=NotificationCategory.SYSTEM,
            severity=NotificationSeverity.ERROR,
            title="System error",
            source="engine",
        )
        with pytest.raises(ValidationError):
            n.title = "changed"  # type: ignore[misc]

    def test_blank_title_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Notification(
                category=NotificationCategory.BUDGET,
                severity=NotificationSeverity.INFO,
                title="   ",
                source="test",
            )

    def test_blank_source_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Notification(
                category=NotificationCategory.BUDGET,
                severity=NotificationSeverity.INFO,
                title="Test",
                source="  ",
            )

    def test_metadata_deep_copy_isolation(self) -> None:
        values = [1, 2, 3]
        meta: dict[str, object] = {"key": values}
        n = Notification(
            category=NotificationCategory.AGENT,
            severity=NotificationSeverity.INFO,
            title="Test",
            source="test",
            metadata=meta,
        )
        values.append(4)
        assert n.metadata["key"] == [1, 2, 3]

    def test_unique_ids(self) -> None:
        n1 = Notification(
            category=NotificationCategory.SYSTEM,
            severity=NotificationSeverity.INFO,
            title="A",
            source="test",
        )
        n2 = Notification(
            category=NotificationCategory.SYSTEM,
            severity=NotificationSeverity.INFO,
            title="B",
            source="test",
        )
        assert n1.id != n2.id
