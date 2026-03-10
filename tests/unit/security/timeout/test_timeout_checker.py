"""Tests for the TimeoutChecker."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from ai_company.core.approval import ApprovalItem
from ai_company.core.enums import ApprovalRiskLevel, ApprovalStatus, TimeoutActionType
from ai_company.security.timeout.models import TimeoutAction
from ai_company.security.timeout.timeout_checker import TimeoutChecker

pytestmark = pytest.mark.timeout(30)


def _make_approval_item(**overrides: Any) -> ApprovalItem:
    """Create a valid pending ApprovalItem with sensible defaults."""
    defaults: dict[str, Any] = {
        "id": "approval-1",
        "action_type": "code:write",
        "title": "Test approval",
        "description": "Testing",
        "requested_by": "agent-1",
        "risk_level": ApprovalRiskLevel.MEDIUM,
        "status": ApprovalStatus.PENDING,
        "created_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return ApprovalItem(**defaults)


def _make_mock_policy(
    *,
    action: TimeoutActionType = TimeoutActionType.WAIT,
    reason: str = "test reason",
) -> AsyncMock:
    """Create a mock TimeoutPolicy returning the given action."""
    mock_policy = AsyncMock()
    mock_policy.determine_action.return_value = TimeoutAction(
        action=action,
        reason=reason,
    )
    return mock_policy


@pytest.mark.unit
class TestTimeoutCheckerCheck:
    """Tests for TimeoutChecker.check()."""

    async def test_check_returns_action(self) -> None:
        """Checker delegates to policy and returns its action."""
        mock_policy = _make_mock_policy(
            action=TimeoutActionType.WAIT,
            reason="Still waiting",
        )
        checker = TimeoutChecker(policy=mock_policy)
        item = _make_approval_item()

        result = await checker.check(item)

        assert result.action == TimeoutActionType.WAIT
        assert result.reason == "Still waiting"
        mock_policy.determine_action.assert_called_once()


@pytest.mark.unit
class TestTimeoutCheckerCheckAndResolve:
    """Tests for TimeoutChecker.check_and_resolve()."""

    async def test_check_and_resolve_approve(self) -> None:
        """When policy returns APPROVE, item status is updated to APPROVED."""
        mock_policy = _make_mock_policy(
            action=TimeoutActionType.APPROVE,
            reason="Auto-approved after timeout",
        )
        checker = TimeoutChecker(policy=mock_policy)
        item = _make_approval_item()

        updated_item, action = await checker.check_and_resolve(item)

        assert action.action == TimeoutActionType.APPROVE
        assert updated_item.status == ApprovalStatus.APPROVED
        assert updated_item.decided_by == "timeout_policy"
        assert updated_item.decided_at is not None

    async def test_check_and_resolve_deny(self) -> None:
        """When policy returns DENY, item status is updated to REJECTED."""
        mock_policy = _make_mock_policy(
            action=TimeoutActionType.DENY,
            reason="Denied after timeout",
        )
        checker = TimeoutChecker(policy=mock_policy)
        item = _make_approval_item()

        updated_item, action = await checker.check_and_resolve(item)

        assert action.action == TimeoutActionType.DENY
        assert updated_item.status == ApprovalStatus.REJECTED
        assert updated_item.decided_by == "timeout_policy"
        assert updated_item.decided_at is not None

    async def test_check_and_resolve_wait(self) -> None:
        """When policy returns WAIT, item status stays PENDING."""
        mock_policy = _make_mock_policy(
            action=TimeoutActionType.WAIT,
            reason="Still waiting",
        )
        checker = TimeoutChecker(policy=mock_policy)
        item = _make_approval_item()

        updated_item, action = await checker.check_and_resolve(item)

        assert action.action == TimeoutActionType.WAIT
        assert updated_item.status == ApprovalStatus.PENDING

    async def test_check_and_resolve_escalate(self) -> None:
        """When policy returns ESCALATE, item status stays PENDING."""
        mock_policy = _make_mock_policy(
            action=TimeoutActionType.ESCALATE,
            reason="Escalating to manager",
        )
        checker = TimeoutChecker(policy=mock_policy)
        item = _make_approval_item()

        updated_item, action = await checker.check_and_resolve(item)

        assert action.action == TimeoutActionType.ESCALATE
        assert updated_item.status == ApprovalStatus.PENDING
