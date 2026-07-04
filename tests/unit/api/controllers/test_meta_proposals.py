"""Tests for the ``GET /meta/proposals`` read endpoint.

Covers both live proposal producers: the manual MCP-tool-driven
submission path (``action_type == PROPOSAL_ACTION_TYPE``) and the
automated self-improvement-cycle guard (``action_type`` prefixed with
``PROPOSAL_GUARD_ACTION_TYPE_PREFIX``, altitude-suffixed). Unrelated
action types must not appear in the listing.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.approval import ApprovalItem
from synthorg.meta.guards.approval_gate import PROPOSAL_GUARD_ACTION_TYPE_PREFIX
from synthorg.meta.signals.service import PROPOSAL_ACTION_TYPE
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers

pytestmark = pytest.mark.unit

_BASE = "/api/v1/meta/proposals"
_HEADERS = make_auth_headers("ceo")
_NOW = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)


def _item(item_id: str, action_type: str, title: str) -> ApprovalItem:
    return ApprovalItem(
        id=item_id,  # type: ignore[arg-type]
        action_type=action_type,
        title=title,
        description="A proposal awaiting review",
        requested_by="meta_improvement_service",
        risk_level=ApprovalRiskLevel.MEDIUM,
        status=ApprovalStatus.PENDING,
        created_at=_NOW,
    )


class TestListProposals:
    async def test_matches_both_producer_action_types(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        manual = _item(
            str(uuid4()), PROPOSAL_ACTION_TYPE, "Manually submitted proposal"
        )
        automated = _item(
            str(uuid4()),
            f"{PROPOSAL_GUARD_ACTION_TYPE_PREFIX}config_tuning",
            "Automated cycle proposal",
        )
        unrelated = _item(str(uuid4()), "credential:password_change", "Not a proposal")

        store = ApprovalStore()
        await store.add(manual)
        await store.add(automated)
        await store.add(unrelated)

        app_state = async_test_client.app.state.app_state
        original_slice = app_state.slice(ApprovalStateSlice)
        app_state.wire(ApprovalStateSlice, store=store)
        try:
            resp = await async_test_client.get(_BASE, headers=_HEADERS)
            assert resp.status_code == 200
            body = resp.json()
            titles = {row["title"] for row in body["data"]}
            assert titles == {"Manually submitted proposal", "Automated cycle proposal"}
        finally:
            app_state.swap_slice(original_slice)
