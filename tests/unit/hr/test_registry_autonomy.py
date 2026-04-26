"""Tests for AgentRegistryService.update_autonomy().

Mirrors the REST endpoint semantics: every change routes through human
approval (no direct mutation), emits the standard promotion-request
audit events, and -- when an approval store is wired -- enqueues the
request for human review.
"""

from typing import Any
from uuid import uuid4

import pytest
import structlog.testing

from synthorg.core.agent import AgentIdentity
from synthorg.core.approval import ApprovalItem
from synthorg.core.enums import (
    ApprovalRiskLevel,
    ApprovalStatus,
    AutonomyLevel,
)
from synthorg.core.types import NotBlankStr
from synthorg.hr.errors import AgentNotFoundError
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability.events.security import (
    SECURITY_AUTONOMY_PROMOTION_DENIED,
    SECURITY_AUTONOMY_PROMOTION_REQUESTED,
)
from synthorg.security.autonomy.models import AutonomyUpdate
from tests.unit.hr.conftest import make_test_identity


def _make_identity(
    *,
    autonomy_level: AutonomyLevel | None = AutonomyLevel.SUPERVISED,
) -> AgentIdentity:
    return make_test_identity(
        name="autonomy-test",
        autonomy_level=autonomy_level,
    )


class _RecordingApprovalStore:
    """Minimal in-memory ApprovalStoreProtocol stub for tests."""

    def __init__(self) -> None:
        self.added: list[ApprovalItem] = []

    async def clear(self) -> None:
        self.added.clear()

    def reset_for_test_sync(self) -> None:
        self.added.clear()

    async def add(self, item: ApprovalItem) -> None:
        self.added.append(item)

    async def get(self, approval_id: Any) -> ApprovalItem | None:
        return None

    async def list_items(
        self,
        *,
        status: ApprovalStatus | None = None,
        risk_level: ApprovalRiskLevel | None = None,
        action_type: NotBlankStr | None = None,
    ) -> tuple[ApprovalItem, ...]:
        return tuple(self.added)

    async def save(self, item: ApprovalItem) -> ApprovalItem | None:
        return None

    async def save_if_pending(
        self,
        item: ApprovalItem,
    ) -> ApprovalItem | None:
        return None


class TestUpdateAutonomy:
    """update_autonomy() requests promotions through the approval queue."""

    @pytest.mark.unit
    async def test_pending_when_no_approval_store(self) -> None:
        identity = _make_identity()
        registry = AgentRegistryService()
        await registry.register(identity)

        result = await registry.update_autonomy(
            str(identity.id),
            AutonomyUpdate(
                requested_level=AutonomyLevel.SEMI,
                reason="agent earned trust",
                requested_by="alice",
            ),
        )
        assert result.agent_id == str(identity.id)
        assert result.current_level == AutonomyLevel.SUPERVISED
        assert result.requested_level == AutonomyLevel.SEMI
        assert result.promotion_pending is True
        assert result.approval_id is None

    @pytest.mark.unit
    async def test_emits_request_and_denied_events(self) -> None:
        identity = _make_identity()
        registry = AgentRegistryService()
        await registry.register(identity)

        with structlog.testing.capture_logs() as logs:
            await registry.update_autonomy(
                str(identity.id),
                AutonomyUpdate(
                    requested_level=AutonomyLevel.SEMI,
                    reason="trusted operator",
                ),
            )

        events = {e.get("event") for e in logs}
        assert SECURITY_AUTONOMY_PROMOTION_REQUESTED in events
        assert SECURITY_AUTONOMY_PROMOTION_DENIED in events

    @pytest.mark.unit
    async def test_enqueues_when_approval_store_wired(self) -> None:
        identity = _make_identity()
        registry = AgentRegistryService()
        await registry.register(identity)
        store = _RecordingApprovalStore()

        result = await registry.update_autonomy(
            str(identity.id),
            AutonomyUpdate(
                requested_level=AutonomyLevel.SEMI,
                reason="trusted operator",
                requested_by="alice",
            ),
            approval_store=store,
        )
        assert result.approval_id is not None
        assert len(store.added) == 1
        item = store.added[0]
        assert item.action_type == "autonomy:promote"
        assert item.requested_by == "alice"
        assert item.risk_level == ApprovalRiskLevel.HIGH
        assert item.status == ApprovalStatus.PENDING
        assert item.id == result.approval_id

    @pytest.mark.unit
    async def test_unknown_agent_raises(self) -> None:
        registry = AgentRegistryService()
        with pytest.raises(AgentNotFoundError):
            await registry.update_autonomy(
                str(uuid4()),
                AutonomyUpdate(
                    requested_level=AutonomyLevel.SEMI,
                    reason="missing agent",
                ),
            )

    @pytest.mark.unit
    async def test_no_approval_for_same_or_lower_level(self) -> None:
        """Demotion requests still go through approval -- this is policy.

        For META-MCP-3 we mirror the REST endpoint's "everything pends"
        behavior; future work may distinguish demotions, but pinning the
        invariant here prevents silent regressions.
        """
        identity = _make_identity(autonomy_level=AutonomyLevel.SEMI)
        registry = AgentRegistryService()
        await registry.register(identity)
        store = _RecordingApprovalStore()

        result = await registry.update_autonomy(
            str(identity.id),
            AutonomyUpdate(
                requested_level=AutonomyLevel.SUPERVISED,
                reason="reduce autonomy",
            ),
            approval_store=store,
        )
        assert result.promotion_pending is True
        assert len(store.added) == 1
