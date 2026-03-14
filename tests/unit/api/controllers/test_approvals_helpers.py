"""Tests for approvals controller helper functions."""

from unittest.mock import MagicMock

import pytest

from ai_company.api.controllers.approvals import (
    _log_approval_decision,
    _publish_approval_event,
    _resolve_decision,
    _signal_resume_intent,
)
from ai_company.api.errors import ConflictError, UnauthorizedError
from ai_company.api.state import AppState
from ai_company.core.approval import ApprovalItem
from ai_company.core.enums import ApprovalRiskLevel, ApprovalStatus

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def _make_pending_item(approval_id: str = "approval-1") -> ApprovalItem:
    from datetime import UTC, datetime

    return ApprovalItem(
        id=approval_id,
        action_type="deploy:production",
        title="Deploy to prod",
        description="Deploy v2.0",
        requested_by="agent-1",
        risk_level=ApprovalRiskLevel.HIGH,
        status=ApprovalStatus.PENDING,
        created_at=datetime.now(UTC),
    )


def _make_request(*, user: object = None) -> MagicMock:
    request = MagicMock()
    request.scope = {"user": user}
    request.app.plugins = []
    return request


def _make_auth_user(username: str = "admin") -> MagicMock:
    from ai_company.api.auth.models import AuthenticatedUser

    user = MagicMock(spec=AuthenticatedUser)
    user.username = username
    return user


class TestResolveDecision:
    """_resolve_decision() pre-checks."""

    def test_raises_conflict_when_not_pending(self) -> None:
        request = _make_request(user=_make_auth_user())
        item = _make_pending_item().model_copy(
            update={"status": ApprovalStatus.APPROVED},
        )
        with pytest.raises(ConflictError, match="not pending"):
            _resolve_decision(request, item, "approval-1")

    def test_raises_unauthorized_when_no_user(self) -> None:
        request = _make_request(user=None)
        item = _make_pending_item()
        with pytest.raises(UnauthorizedError, match="Authentication"):
            _resolve_decision(request, item, "approval-1")

    def test_raises_unauthorized_when_wrong_user_type(self) -> None:
        request = _make_request(user="not-an-auth-user")
        item = _make_pending_item()
        with pytest.raises(UnauthorizedError, match="Authentication"):
            _resolve_decision(request, item, "approval-1")

    def test_returns_auth_user_when_valid(self) -> None:
        auth_user = _make_auth_user("ceo")
        request = _make_request(user=auth_user)
        item = _make_pending_item()
        result = _resolve_decision(request, item, "approval-1")
        assert result is auth_user


class TestLogApprovalDecision:
    """_log_approval_decision() logs correctly."""

    def test_logs_approved(self) -> None:
        # Should not raise
        _log_approval_decision(
            "approval-1",
            approved=True,
            decided_by="admin",
        )

    def test_logs_rejected(self) -> None:
        _log_approval_decision(
            "approval-1",
            approved=False,
            decided_by="reviewer",
        )


class TestSignalResumeIntent:
    """_signal_resume_intent() logging stub."""

    async def test_noop_when_no_gate(self) -> None:
        app_state = MagicMock(spec=AppState)
        app_state.approval_gate = None
        # Should return without error
        await _signal_resume_intent(
            app_state,
            "approval-1",
            approved=True,
            decided_by="admin",
        )

    async def test_logs_when_gate_present(self) -> None:
        app_state = MagicMock(spec=AppState)
        app_state.approval_gate = MagicMock()
        await _signal_resume_intent(
            app_state,
            "approval-1",
            approved=True,
            decided_by="admin",
            decision_reason="LGTM",
        )

    async def test_logs_reject_with_reason(self) -> None:
        app_state = MagicMock(spec=AppState)
        app_state.approval_gate = MagicMock()
        await _signal_resume_intent(
            app_state,
            "approval-1",
            approved=False,
            decided_by="reviewer",
            decision_reason="Too risky",
        )


class TestPublishApprovalEvent:
    """_publish_approval_event() best-effort WebSocket publishing."""

    def test_logs_warning_when_no_channels_plugin(self) -> None:
        from ai_company.api.ws_models import WsEventType

        request = _make_request()
        request.app.plugins = []  # No ChannelsPlugin
        item = _make_pending_item()
        # Should not raise — best-effort
        _publish_approval_event(
            request,
            WsEventType.APPROVAL_SUBMITTED,
            item,
        )

    def test_publishes_when_plugin_available(self) -> None:
        from litestar.channels import ChannelsPlugin

        from ai_company.api.ws_models import WsEventType

        plugin = MagicMock(spec=ChannelsPlugin)
        request = _make_request()
        request.app.plugins = [plugin]
        item = _make_pending_item()

        _publish_approval_event(
            request,
            WsEventType.APPROVAL_SUBMITTED,
            item,
        )
        plugin.publish.assert_called_once()

    def test_logs_warning_when_publish_fails(self) -> None:
        from litestar.channels import ChannelsPlugin

        from ai_company.api.ws_models import WsEventType

        plugin = MagicMock(spec=ChannelsPlugin)
        plugin.publish.side_effect = RuntimeError("not started")
        request = _make_request()
        request.app.plugins = [plugin]
        item = _make_pending_item()

        # Should not raise — best-effort
        _publish_approval_event(
            request,
            WsEventType.APPROVAL_SUBMITTED,
            item,
        )
